from __future__ import annotations

import re
from .common import require, utc, canonical_hash

# UTC, left inclusive and right exclusive; based on PR.created_at, not merged_at.
REPOSITORIES = {
    "Automattic/wp-calypso": ("2018-01-01", "2019-09-01"),
    "chartjs/Chart.js": ("2020-10-01", "2023-07-01"),
    "processing/p5.js": ("2018-07-01", "2023-05-01"),
    "markedjs/marked": ("2015-11-01", "2023-06-01"),
    "diegomura/react-pdf": ("2018-12-01", "2023-10-01"),
}


def instance_id(repo, number):
    require(repo in REPOSITORIES, f"Repository not in IID allowlist: {repo}")
    require(isinstance(number, int) and not isinstance(number, bool) and number > 0, "Invalid PR number")
    return repo.replace("/", "__") + f"-{number}"


def eligible(repo, created_at, merged_at, cutoff):
    if repo not in REPOSITORIES or not merged_at:
        return False
    created, merged, end = map(utc, (created_at, merged_at, cutoff))
    lower, upper = (utc(x + "T00:00:00Z") for x in REPOSITORIES[repo])
    return created <= merged <= end and not lower <= created < upper


def validate_source(source):
    iid = instance_id(source["repo"], source["pr_number"])
    require(eligible(source["repo"], source["created_at"], source["merged_at"], source["cutoff"]),
            f"Source outside collection policy: {iid}")
    require(source["pr_url"] == f'https://github.com/{source["repo"]}/pull/{source["pr_number"]}',
            "PR URL does not match source")
    for key in ("base_commit", "head_commit"):
        require(bool(re.fullmatch(r"[0-9a-f]{40}", source[key])), f"Invalid {key}")
    require(source.get("issue_urls"), "A real linked issue is required")
    for url in source["issue_urls"]:
        require(bool(re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/issues/[1-9]\d*", url)),
                f"Invalid issue URL: {url}")
    require(source.get("root_task_id"), "Missing root_task_id")
    return iid


def patch_fingerprint(patch):
    # Disregards hunk offsets and commit ids, preserving added/deleted code.
    lines = [re.sub(r"\s+", " ", x[1:]).strip() for x in patch.splitlines()
             if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]
    return canonical_hash(lines)


def duplicate_reasons(record, others):
    reasons = []
    for other in others:
        same_id = record.get("instance_id") == other.get("instance_id")
        same_root = record.get("root_task_id") and record.get("root_task_id") == other.get("root_task_id")
        issues = set(record.get("issue_urls", [])) & set(other.get("issue_urls", []))
        patch = record.get("patch_fingerprint") and record.get("patch_fingerprint") == other.get("patch_fingerprint")
        if same_id or same_root or issues or patch:
            reasons.append(other.get("instance_id", other.get("root_task_id", "unknown")))
    return reasons
