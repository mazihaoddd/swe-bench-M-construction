"""Synthetic local Git/Node fixtures; never represented as real benchmark tasks."""
from pathlib import Path
import io
import shutil
import tempfile
import unittest

from PIL import Image

from iid_pipeline.common import file_hash, read_json, run, write_json, write_text
from iid_pipeline.package import REVIEW_CHECKS
from iid_pipeline.prepare import review_subject


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(__file__).resolve().parents[2] / "tmp" / "iid-tests"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="test-", dir=self.scratch)).resolve()

    def tearDown(self):
        # Only ever remove this test's verified, generated scratch directory.
        assert self.root.is_relative_to(self.scratch.resolve()) and self.root.name.startswith("test-")
        shutil.rmtree(self.root, onerror=lambda func, path, exc: (Path(path).chmod(0o700), func(path)))


def git(repo, *args):
    return run(["git", "-C", repo, *args])


def fixture(root):
    repo, draft = root / "repo", root / "draft"
    repo.mkdir()
    draft.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Synthetic test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    write_text(repo / "layout.js", "exports.position = n => 0;\nexports.identity = n => n;\n")
    write_text(repo / "existing.test.js", "module.exports = () => { if(require('./layout').identity(4)!==4) throw Error('identity regression'); };\n")
    write_text(repo / "runner.js", '''const fs = require('fs');
const tests = [];
for (const name of process.argv.slice(2)) {
  try { require('./' + name + '.test.js')(); tests.push({id:name,status:'PASS',detail:''}); }
  catch(e) { tests.push({id:name,status:e.code === 'MODULE_NOT_FOUND' ? 'ERROR':'FAIL',detail:e.message}); }
}
fs.mkdirSync('.iid-results',{recursive:true});
fs.writeFileSync('.iid-results/results.json',JSON.stringify({tests}));
process.exit(tests.some(t=>t.status!=='PASS')?1:0);
''')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "synthetic base")
    base = git(repo, "rev-parse", "HEAD").decode().strip()
    (draft / "base.tar").write_bytes(git(repo, "archive", "--format=tar", "HEAD"))
    write_text(repo / "layout.js", "exports.position = n => n;\nexports.identity = n => n;\n")
    (draft / "gold.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", "--", "layout.js"))
    write_text(repo / "layout.js", "exports.position = n => -1;\nexports.identity = n => n;\n")
    (draft / "negative.patch").write_bytes(git(repo, "diff", "--binary", "--full-index", "--", "layout.js"))
    git(repo, "restore", "layout.js")
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(buf, format="PNG")
    (repo / "fixture.png").write_bytes(buf.getvalue())
    write_text(repo / "new.test.js", '''module.exports = () => {
 if(require('fs').readFileSync('fixture.png')[0]!==137) throw Error('bad fixture');
 if(require('./layout').position(8)!==8) throw Error('expected right boundary');
};
''')
    git(repo, "add", "--intent-to-add", "new.test.js", "fixture.png")
    (draft / "test.patch").write_bytes(git(repo, "diff", "--binary", "--full-index"))
    (draft / "issue.png").write_bytes(buf.getvalue())
    write_text(draft / "setup.sh", "#!/bin/bash\nset -euo pipefail\nnode --version\n")
    iid = "chartjs__Chart.js-99999999"
    write_text(draft / "instruction.md", f"Synthetic fixture only. Modify /testbed. See /testbed/assets/{iid}/issue.png.\n")
    source = {"repo": "chartjs/Chart.js", "pr_number": 99999999, "base_commit": base, "head_commit": "a" * 40,
              "created_at": "2024-01-01T00:00:00Z", "merged_at": "2024-01-02T00:00:00Z", "cutoff": "2026-09-12T00:00:00Z",
              "pr_url": "https://github.com/chartjs/Chart.js/pull/99999999",
              "issue_urls": ["https://github.com/chartjs/Chart.js/issues/99999998"], "root_task_id": "synthetic-unit-test"}
    data = {"schema_version": 1, "source": source, "instruction": "instruction.md", "source_archive": "base.tar",
            "gold_patch": "gold.patch", "test_patch": "test.patch", "negative_patches": ["negative.patch"],
            "environment": {"base_image": "node:20-bookworm@sha256:" + "0" * 64, "setup_script": "setup.sh",
                            "cpus": 2, "memory_mb": 4096, "storage_mb": 8192, "build_timeout_sec": 1800},
            "agent_timeout_sec": 60, "verifier_timeout_sec": 120,
            "tests": {"FAIL_TO_PASS": ["new"], "PASS_TO_PASS": ["existing"],
                      "command": ["node", "runner.js", "existing", "new"], "baseline_command": ["node", "runner.js", "existing"],
                      "log_parser": {"kind": "json", "report_path": ".iid-results/results.json"}, "test_timeout_sec": 30,
                      "protected_paths": ["new.test.js", "fixture.png", "runner.js"], "p2p_files": ["existing.test.js"],
                      "f2p_cases": {"new": {"file": "new.test.js", "requirement": "Right boundary follows width", "failure_regex": "expected right boundary"}},
                      "fixture_sha256": {"fixture.png": file_hash(repo / "fixture.png")}},
            "images": [{"file": "issue.png", "source_url": "https://example.invalid/test.png", "repo_path": f"assets/{iid}/issue.png",
                        "sha256": file_hash(draft / "issue.png"), "role": "mockup", "visual_evidence": "right edge",
                        "information_gain": "edge location", "without_image": "anchor ambiguous", "linked_test_ids": ["new"]}],
            "tags": {"framework": "synthetic", "renderer": "synthetic", "defect": "position", "difficulty": "test"},
            "review": {"approved": True, "reviewer": "synthetic-fixture", "reviewed_at": "2026-09-12T00:00:00Z",
                       "checks": sorted(REVIEW_CHECKS), "subject_sha256": ""}}
    write_json(draft / "source.json", {"source": source, "archive_sha256": file_hash(draft / "base.tar")})
    write_json(draft / "manifest.json", data)
    seal(draft / "manifest.json")
    return repo, draft / "manifest.json"


def seal(path):
    data = read_json(path)
    data["review"]["subject_sha256"] = review_subject(path)
    write_json(path, data)


def worktree(root, source, task):
    git(root, "clone", "--no-hardlinks", str(source), "work")
    work = root / "work"
    git(work, "config", "core.autocrlf", "false")
    shutil.copytree(task / "environment/assets", work / "assets")
    return work
