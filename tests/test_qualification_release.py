from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

from iid_pipeline.common import PipelineError, file_hash, read_json, write_json
from iid_pipeline.package import build, validate_bundle
from iid_pipeline.verify import qualify, load_qualification, publish, assert_qualification
from tests.helpers import WorkspaceTest, fixture, worktree, git


class LocalExecutionDouble:
    """Tests the orchestrator with REAL Git/Node, but DOES NOT claim Docker coverage.

    This double is only importable from tests; the production CLI has no bypass
    for Docker qualification.
    """
    def __init__(self, root, source):
        self.root, self.source = root, source

    def check(self):
        pass

    def build(self, task, output):
        return "sha256:" + "d" * 64

    def evaluate(self, task, image_id, logs, phase="candidate", gold=False, candidate=None):
        root = self.root / ("execution-" + uuid.uuid4().hex)
        root.mkdir()
        work = worktree(root, self.source, Path(task))
        if gold:
            git(work, "apply", str(Path(task) / "solution/gold.patch"))
        argv = [sys.executable, "-I", "-B", str(Path(task) / "tests/sweb_grade.py"),
                "--workdir", str(work), "--logs", str(logs), "--phase", phase]
        if candidate:
            argv += ["--candidate-patch", str(candidate)]
        process = subprocess.run(argv, capture_output=True, timeout=45)
        if process.returncode:
            raise PipelineError(process.stderr.decode())
        return read_json(Path(logs) / "result.json")


class QualificationReleaseTests(WorkspaceTest):
    def prepare(self):
        import shutil
        if not shutil.which("node"):
            self.skipTest("Node integration required")
        source, manifest = fixture(self.root)
        task = build(manifest, self.root / "staging")
        docker = LocalExecutionDouble(self.root, source)
        evidence = self.root / "qualification"
        receipt = qualify(task, evidence, runs=2, docker=docker)
        return task, docker, evidence, receipt

    def test_real_local_double_state_evidence_and_stale_task_rejection(self):
        task, _, evidence, receipt = self.prepare()
        self.assertEqual(receipt["status"], "qualified")
        self.assertEqual(len(receipt["results"]), 7)
        load_qualification(task, evidence / "qualification.json")
        with (task / "instruction.md").open("a") as stream:
            stream.write("modified after validation")
        with self.assertRaisesRegex(PipelineError, "stale"):
            load_qualification(task, evidence / "qualification.json")

    def test_changed_evidence_is_rejected(self):
        task, _, evidence, _ = self.prepare()
        (evidence / "gold-0/test_output.txt").write_text("changed")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            load_qualification(task, evidence / "qualification.json")

    def test_release_gate_and_strict_output_tree(self):
        task, _, evidence, receipt = self.prepare()
        iid = receipt["instance_id"]
        exclusions = self.root / "exclusions.json"
        write_json(exclusions, [{"instance_id": "synthetic-official-reference-1"}])
        batch = [(task, evidence / "qualification.json")]
        output = publish(batch, exclusions, self.root / "release")
        self.assertEqual({x.name for x in output.iterdir()}, {iid})
        self.assertEqual(validate_bundle(output / iid)["task_sha256"], receipt["task_sha256"])
        ledger = read_json(self.root / "release.release.json")
        self.assertEqual(set(ledger), {"created_at", "tasks", "qualifications", "exclusions_sha256"})
        self.assertEqual(ledger["qualifications"][0]["receipt_sha256"],
                         file_hash(evidence / "qualification.json"))
        with self.assertRaisesRegex(PipelineError, "already exists"):
            publish(batch, exclusions, output)

        write_json(exclusions, [{"instance_id": iid}])
        with self.assertRaisesRegex(PipelineError, "Duplicate"):
            publish(batch, exclusions, self.root / "rejected-release")
        self.assertFalse((self.root / "rejected-release").exists())

    def test_publish_rejects_stale_and_missing_qualification(self):
        task, _, evidence, _ = self.prepare()
        exclusions = self.root / "exclusions.json"
        write_json(exclusions, [{"instance_id": "synthetic-reference"}])
        target = self.root / "release"
        with self.assertRaises(FileNotFoundError):
            publish([(task, self.root / "missing.json")], exclusions, target)
        (evidence / "gold-0/test_output.txt").write_text("changed")
        with self.assertRaisesRegex(PipelineError, "evidence changed"):
            publish([(task, evidence / "qualification.json")], exclusions, target)
        self.assertFalse(target.exists())

    def test_publish_rejects_empty_task_set(self):
        exclusions = self.root / "exclusions.json"
        write_json(exclusions, [{"instance_id": "synthetic-reference"}])
        with self.assertRaisesRegex(PipelineError, "empty task set"):
            publish([], exclusions, self.root / "release")
        self.assertFalse((self.root / "release").exists())

    def test_batch_runs_from_reviewed_manifest_to_release(self):
        import shutil
        from iid_pipeline.batch import produce
        if not shutil.which("node"):
            self.skipTest("Node integration required")
        source, manifest = fixture(self.root)
        write_json(self.root / "exclusions.json", [{"instance_id": "synthetic-reference"}])
        plan = self.root / "plan.json"
        write_json(plan, {"workspace": "batch-work", "release": "release",
                          "manifests": [str(manifest)], "exclusions": "exclusions.json",
                          "jobs": 1, "runs": 2})
        with patch("iid_pipeline.batch.Docker", return_value=LocalExecutionDouble(self.root, source)):
            output = produce(plan)
        self.assertTrue(output.is_dir())
        batch = read_json(self.root / "batch-work/batch.json")
        self.assertEqual(len(batch["tasks"]), 1)
        self.assertEqual(read_json(batch["tasks"][0]["qualification"])["status"], "qualified")

    def test_missing_base_case_cannot_qualify(self):
        config = {"PASS_TO_PASS": ["old"], "FAIL_TO_PASS": ["new"]}
        results = [{"phase": "baseline", "result": {"missing": ["old"], "timed_out": False}}] * 2
        with self.assertRaisesRegex(PipelineError, "missing"):
            assert_qualification(config, results, 2)
