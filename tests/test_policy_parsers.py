import unittest

from iid_pipeline.github import closing_references
from iid_pipeline.policy import REPOSITORIES, eligible, duplicate_reasons
from iid_pipeline.runtime.logparsers import parse, grade, ParseError


class PolicyTests(unittest.TestCase):
    def test_jsonl_preserves_unicode_separators_in_issue_text(self):
        import json
        import tempfile
        from pathlib import Path
        from iid_pipeline.common import load_records
        records = [{"body": "before\u2028after\u2029end"}, {"body": "next"}]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "issues.jsonl"
            path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in records) + "\n", encoding="utf-8")
            self.assertEqual(load_records(path), records)

    def test_all_five_exclusion_boundaries(self):
        from datetime import timedelta
        from iid_pipeline.common import utc
        for repo, (lo, hi) in REPOSITORIES.items():
            lo, hi = utc(lo + "T00:00:00Z"), utc(hi + "T00:00:00Z")
            for created, expected in ((lo - timedelta(seconds=1), True), (lo, False),
                                      (hi - timedelta(seconds=1), False), (hi, True)):
                with self.subTest(repo=repo, created=created):
                    self.assertEqual(eligible(repo, created.isoformat(), "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"), expected)

    def test_unmerged_future_unknown_sources_rejected(self):
        self.assertFalse(eligible("chartjs/Chart.js", "2024-01-01T00:00:00Z", None, "2026-01-01T00:00:00Z"))
        self.assertFalse(eligible("chartjs/Chart.js", "2024-01-01T00:00:00Z", "2027-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
        self.assertFalse(eligible("other/repo", "2024-01-01T00:00:00Z", "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))

    def test_only_closing_references(self):
        self.assertEqual(closing_references("See #1. Fixes #2; resolves other/repo#3; closes https://github.com/a/b/issues/4", "chartjs/Chart.js"),
                         ["https://github.com/a/b/issues/4", "https://github.com/chartjs/Chart.js/issues/2", "https://github.com/other/repo/issues/3"])

    def test_backports_dedup_by_issue_or_patch(self):
        record = {"instance_id": "a-2", "issue_urls": ["issue"], "patch_fingerprint": "abc"}
        self.assertEqual(duplicate_reasons(record, [{"instance_id": "a-1", "issue_urls": ["issue"]}]), ["a-1"])
        self.assertEqual(duplicate_reasons(record, [{"instance_id": "b-1", "patch_fingerprint": "abc"}]), ["b-1"])


class ParserTests(unittest.TestCase):
    def test_missing_p2p_is_failure(self):
        result = grade({"new": {"status": "PASS"}}, ["new"], ["old"], 0)
        self.assertEqual(result["reward"], 0)
        self.assertEqual(result["cases"]["old"]["status"], "MISSING")

    def test_skipped_p2p_is_failure(self):
        self.assertEqual(grade({"new": {"status": "PASS"}, "old": {"status": "SKIP"}}, ["new"], ["old"], 0)["reward"], 0)

    def test_nonzero_exit_and_timeout_cannot_pass(self):
        obs = {"new": {"status": "PASS"}, "old": {"status": "PASS"}}
        self.assertEqual(grade(obs, ["new"], ["old"], 1)["reward"], 0)
        self.assertEqual(grade(obs, ["new"], ["old"], 0, True)["reward"], 0)
        self.assertEqual(grade(obs, ["new"], ["old"], 0)["reward"], 1)

    def test_junit_failure_error_and_skip(self):
        text = '<testsuite><testcase classname="s" name="ok"/><testcase classname="s" name="bad"><failure message="assertion"/></testcase><testcase classname="s" name="skip"><skipped/></testcase><testcase classname="s" name="error"><error/></testcase></testsuite>'
        result = parse(text, {"kind": "junit"})
        self.assertEqual([x["status"] for x in result.values()], ["PASS", "FAIL", "SKIP", "ERROR"])

    def test_jest_stable_full_names_and_paths(self):
        import json
        data = {"testResults": [{"name": "/testbed/a.spec.js", "assertionResults": [
            {"title": "same", "fullName": "suite1 same", "status": "passed"},
            {"title": "same", "fullName": "suite2 same", "status": "failed", "failureMessages": ["expected"]}]}]}
        self.assertEqual(set(parse(json.dumps(data), {"kind": "jest"})), {"a.spec.js::suite1 same", "a.spec.js::suite2 same"})

    def test_mocha_pending_and_fail(self):
        import json
        data = {"tests": [{"fullTitle": "a"}, {"fullTitle": "b"}], "failures": [{"fullTitle": "a", "err": {"message": "assert"}}], "pending": [{"fullTitle": "b"}]}
        self.assertEqual([x["status"] for x in parse(json.dumps(data), {"kind": "mocha"}).values()], ["FAIL", "SKIP"])

    def test_mocha_requires_explicit_pass_list(self):
        result = parse('{"tests":[{"fullTitle":"x"}],"failures":[],"pending":[],"passes":[]}', {"kind": "mocha"})
        self.assertEqual(result["x"]["status"], "ERROR")

    def test_duplicate_statuses_are_not_overwritten(self):
        with self.assertRaises(ParseError):
            parse('{"tests":[{"id":"x","status":"FAIL"},{"id":"x","status":"PASS"}]}', {"kind": "json"})

    def test_regex_uses_full_lines_and_explicit_observation(self):
        spec = {"kind": "regex", "pattern": r"RESULT (?P<status>ok|bad) (?P<id>.+)", "status_map": {"ok": "PASS", "bad": "FAIL"}}
        result = parse("arbitrary PASS x\nRESULT ok real case\nLOG RESULT ok forged\n", spec)
        self.assertEqual(set(result), {"real case"})
