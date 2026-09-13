"""Explicit per-case observations. Never infer PASS from absence of failures."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET


class ParseError(ValueError):
    pass


def add(result, case_id, status, detail=""):
    if not isinstance(case_id, str) or not case_id:
        raise ParseError("Empty/invalid test id")
    if case_id in result:
        raise ParseError(f"Duplicate test id (retries or ambiguous naming): {case_id}")
    if status not in {"PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT"}:
        raise ParseError(f"Unknown test status: {status}")
    result[case_id] = {"status": status, "detail": str(detail)}


def parse(text, spec):
    kind = spec["kind"]
    result = {}
    if kind == "json":
        # Canonical reporter contract: {"tests": [{"id", "status", "detail"}]}.
        for item in json.loads(text)["tests"]:
            add(result, item["id"], item["status"], item.get("detail", ""))
    elif kind == "junit":
        root = ET.fromstring(text)
        for case in root.iter("testcase"):
            parts = [case.get(key, "") for key in spec.get("id_fields", ["classname", "name"])]
            case_id = "::".join(x for x in parts if x)
            failures, errors, skips = case.findall("failure"), case.findall("error"), case.findall("skipped")
            detail = "\n".join((x.get("message", "") + "\n" + (x.text or "")) for x in failures + errors)
            status = "ERROR" if errors else "FAIL" if failures else "SKIP" if skips else "PASS"
            add(result, case_id, status, detail)
    elif kind == "jest":
        data = json.loads(text)
        statuses = {"passed": "PASS", "failed": "FAIL", "pending": "SKIP", "todo": "SKIP",
                    "disabled": "SKIP", "skipped": "SKIP"}
        prefix = spec.get("strip_prefix", "/testbed/")
        for suite in data["testResults"]:
            filename = suite["name"].replace("\\", "/")
            if prefix and filename.startswith(prefix):
                filename = filename[len(prefix):]
            assertions = suite.get("assertionResults", [])
            if not assertions and suite.get("status") == "failed":
                raise ParseError("Jest suite failed before producing test cases")
            for case in assertions:
                name = case.get("fullName") or " ".join(case.get("ancestorTitles", []) + [case["title"]])
                add(result, filename + "::" + name, statuses[case["status"]],
                    "\n".join(case.get("failureMessages", [])))
    elif kind == "mocha":
        data = json.loads(text)
        failed = {x["fullTitle"]: x for x in data.get("failures", [])}
        pending = {x["fullTitle"] for x in data.get("pending", [])}
        passed = {x["fullTitle"] for x in data.get("passes", [])}
        for case in data["tests"]:
            name = case["fullTitle"]
            detail = json.dumps(failed.get(name, {}).get("err", {}))
            if sum(name in group for group in (failed, pending, passed)) > 1:
                raise ParseError(f"Conflicting Mocha results: {name}")
            add(result, name, "FAIL" if name in failed else "SKIP" if name in pending else "PASS" if name in passed else "ERROR", detail)
    elif kind == "regex":
        pattern = re.compile(spec["pattern"])
        if not {"id", "status"} <= set(pattern.groupindex):
            raise ParseError("Regex requires named id and status groups")
        # Full-line matching deliberately excludes free-standing substring PASS.
        for line in text.splitlines():
            match = pattern.fullmatch(line)
            if match:
                add(result, match["id"], spec["status_map"][match["status"]],
                    match.groupdict().get("detail", ""))
    else:
        raise ParseError(f"Unsupported parser kind: {kind}")
    return result


def grade(observed, f2p, p2p, exit_code, timed_out=False):
    required = f2p + p2p
    if not required or len(set(required)) != len(required):
        raise ParseError("Empty/overlapping/duplicate test manifest")
    results = {name: observed.get(name, {"status": "MISSING", "detail": "Not observed"}) for name in required}
    all_pass = all(case["status"] == "PASS" for case in results.values())
    return {
        "reward": int(all_pass and exit_code == 0 and not timed_out),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "cases": results,
        "observed_ids": sorted(observed),
        "missing": [name for name in required if name not in observed],
        "unexpected": sorted(set(observed) - set(required)),
    }
