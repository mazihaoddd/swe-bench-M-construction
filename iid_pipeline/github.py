"""Incremental GitHub discovery. API responses and source provenance are archived."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

from .common import PipelineError, sha256, write_json, read_json, now, utc, load_records
from .policy import REPOSITORIES, eligible, instance_id


def closing_references(body, repo):
    # Plain mentions are not sufficient evidence of an issue being resolved.
    pattern = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
                         r"((?:(?:https://github\.com/)?[\w.-]+/[\w.-]+(?:/issues/|#))?#?\d+)", re.I)
    found = set()
    for ref in pattern.findall(body or ""):
        if ref.startswith("https://github.com/"):
            found.add(ref)
        elif "/" in ref:
            target, num = ref.rsplit("#", 1) if "#" in ref else ref.rsplit("/issues/", 1)
            found.add(f"https://github.com/{target}/issues/{num}")
        else:
            found.add(f"https://github.com/{repo}/issues/{ref.lstrip('#')}")
    return sorted(found)


class GitHub:
    def __init__(self, cache, token=None):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def request(self, route, data=None):
        url = "https://api.github.com" + route
        key = sha256((url + json.dumps(data, sort_keys=True)).encode())
        path = self.cache / (key + ".json")
        if path.exists():
            return read_json(path)["response"]
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "iid-pipeline",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(4):
            try:
                req = Request(url, headers=headers, data=json.dumps(data).encode() if data else None)
                with urlopen(req, timeout=60) as response:
                    value = json.load(response)
                if isinstance(value, dict) and value.get("errors"):
                    raise PipelineError("GitHub GraphQL returned errors; check token scope and query")
                write_json(path, {"url": url, "retrieved_at": now(), "response": value})
                return value
            except HTTPError as exc:
                if exc.code in (403, 429):
                    raise PipelineError("GitHub rate limit/permission failure; resume using the cache and GITHUB_TOKEN") from exc
                if exc.code < 500 or attempt == 3:
                    raise PipelineError(f"GitHub request failed: HTTP {exc.code} {route}") from exc
                time.sleep(2 ** attempt)

    def linked_issues(self, repo, pr):
        urls = set(closing_references(pr.get("body"), repo))
        if self.token:
            owner, name = repo.split("/")
            cursor = None
            while True:
                query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
                  repository(owner:$owner,name:$name){pullRequest(number:$number){
                    closingIssuesReferences(first:100,after:$cursor){nodes{url}
                      pageInfo{hasNextPage endCursor}}}}}"""
                data = self.request("/graphql", {"query": query, "variables": {
                    "owner": owner, "name": name, "number": pr["number"], "cursor": cursor}})
                refs = data["data"]["repository"]["pullRequest"]["closingIssuesReferences"]
                urls.update(node["url"] for node in refs["nodes"])
                if not refs["pageInfo"]["hasNextPage"]:
                    break
                cursor = refs["pageInfo"]["endCursor"]
        issues = []
        for url in sorted(urls):
            route = "/repos/" + url.removeprefix("https://github.com/")
            issue = self.request(route)
            if "pull_request" not in issue and utc(issue["created_at"]) <= utc(pr["merged_at"]):
                issues.append(issue)
        return issues


def collect(output, cache, cutoff, repos=None, max_prs=None):
    utc(cutoff)
    # A new cutoff uses a new snapshot cache; later collections must not reuse
    # an old page-1 response and silently miss newly merged PRs.
    api = GitHub(Path(cache) / sha256(cutoff.encode())[:16])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if output.exists():
        previous = {x["instance_id"]: x for x in load_records(output)}
    counts = {"scanned": 0, "eligible": 0, "linked": 0}
    for repo in repos or REPOSITORIES:
        if repo not in REPOSITORIES:
            raise PipelineError(f"Repository not allowed: {repo}")
        page, scanned = 1, 0
        while max_prs is None or scanned < max_prs:
            prs = api.request(f"/repos/{repo}/pulls?" + urlencode({"state": "closed", "sort": "created",
                              "direction": "desc", "per_page": 100, "page": page}))
            if not prs:
                break
            for summary in prs:
                if max_prs is not None and scanned >= max_prs:
                    break
                scanned += 1
                counts["scanned"] += 1
                if not eligible(repo, summary["created_at"], summary.get("merged_at"), cutoff):
                    continue
                counts["eligible"] += 1
                iid = instance_id(repo, summary["number"])
                if iid in previous:
                    continue
                pr = api.request(f"/repos/{repo}/pulls/{summary['number']}")
                if not eligible(repo, pr["created_at"], pr.get("merged_at"), cutoff):
                    continue
                issues = api.linked_issues(repo, pr)
                if not issues:
                    continue
                counts["linked"] += 1
                record = {"instance_id": iid, "source": {
                    "repo": repo, "pr_number": pr["number"], "created_at": pr["created_at"],
                    "merged_at": pr["merged_at"], "cutoff": cutoff, "pr_url": pr["html_url"],
                    "base_commit": pr["base"]["sha"], "head_commit": pr["head"]["sha"],
                    "issue_urls": sorted(x["html_url"] for x in issues),
                    "root_task_id": sorted(x["html_url"] for x in issues)[0], "retrieved_at": now()},
                    "pull_request": pr, "issues": issues,
                    "needs_pre_fix_context_review": any(utc(x["updated_at"]) > utc(pr["merged_at"]) for x in issues)}
                with output.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                previous[iid] = record
            page += 1
    return counts
