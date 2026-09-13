import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import io

from iid_pipeline.common import PipelineError, read_json, write_json, write_text
from iid_pipeline.package import build, validate_bundle, validate_manifest, archive_files, patch_paths
from iid_pipeline.verify import assert_qualification
from tests.helpers import WorkspaceTest, fixture, seal, worktree, git


class PackageTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.repo, self.manifest = fixture(self.root)

    def test_exact_directory_schema_assets_and_binary_patch(self):
        task = build(self.manifest, self.root / "staging")
        self.assertEqual(set(x.name for x in task.iterdir()), {"instruction.md", "task.toml", "environment", "tests", "solution"})
        info = validate_bundle(task)
        self.assertFalse(info["metadata"]["environment"]["allow_internet"])
        self.assertEqual(info["metadata"]["schema_version"], "1.2")
        self.assertIn(b"GIT binary patch", (task / "tests/test.patch").read_bytes())
        dockerfile = (task / "environment/Dockerfile").read_text()
        self.assertNotIn("gold.patch", dockerfile)
        self.assertNotIn("test.patch", dockerfile)

    def test_review_is_bound_to_file_contents(self):
        write_text(self.manifest.parent / "instruction.md", "changed /testbed prompt")
        with self.assertRaisesRegex(PipelineError, "changed since"):
            build(self.manifest, self.root / "staging")

    def test_shared_bootstrap_is_packaged_before_source_and_review_bound(self):
        data = read_json(self.manifest)
        data["environment"]["bootstrap_script"] = "bootstrap.sh"
        write_text(self.manifest.parent / "bootstrap.sh", "#!/bin/bash\nset -eu\necho dependencies\n")
        write_json(self.manifest, data)
        seal(self.manifest)
        task = build(self.manifest, self.root / "staging")
        dockerfile = (task / "environment/Dockerfile").read_text()
        self.assertLess(dockerfile.index("COPY bootstrap.sh"), dockerfile.index("COPY base.tar"))
        self.assertEqual((task / "environment/bootstrap.sh").read_bytes(), (self.manifest.parent / "bootstrap.sh").read_bytes())
        write_text(self.manifest.parent / "bootstrap.sh", "changed dependency versions\n")
        with self.assertRaisesRegex(PipelineError, "changed since"):
            validate_manifest(self.manifest)

    def test_empty_p2p_rejected_even_if_reference_example_allows_it(self):
        data = read_json(self.manifest)
        data["tests"]["PASS_TO_PASS"] = []
        write_json(self.manifest, data)
        seal(self.manifest)
        with self.assertRaisesRegex(PipelineError, "nonempty"):
            validate_manifest(self.manifest)

    def test_visual_explanation_required_per_image(self):
        data = read_json(self.manifest)
        data["images"][0]["information_gain"] = ""
        write_json(self.manifest, data)
        seal(self.manifest)
        with self.assertRaisesRegex(PipelineError, "information_gain"):
            validate_manifest(self.manifest)

    def test_patch_without_binary_payload_is_rejected(self):
        patch = self.root / "bad.patch"
        patch.write_text("diff --git a/a.png b/a.png\nBinary files a/a.png and b/a.png differ\n")
        with self.assertRaisesRegex(PipelineError, "payload"):
            patch_paths(patch)

    def test_archive_traversal_is_rejected(self):
        path = self.root / "evil.tar"
        with tarfile.open(path, "w") as archive:
            member = tarfile.TarInfo("../../outside")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        with self.assertRaises(PipelineError):
            archive_files(path)

    def test_no_overwrite_of_existing_task(self):
        task = build(self.manifest, self.root / "staging")
        before = (task / "instruction.md").read_bytes()
        with self.assertRaisesRegex(PipelineError, "exists"):
            build(self.manifest, self.root / "staging")
        self.assertEqual((task / "instruction.md").read_bytes(), before)


class RuntimeTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        if not shutil.which("node"):
            self.skipTest("Node is required for actual JS runner integration")
        self.repo, manifest = fixture(self.root)
        self.task = build(manifest, self.root / "staging")
        self.work = worktree(self.root, self.repo, self.task)
        self.logs = self.root / "logs"

    def invoke(self, phase="candidate", candidate=None, config=None):
        argv = [sys.executable, "-I", "-B", str(self.task / "tests/sweb_grade.py"),
                "--config", str(config or self.task / "tests/config.json"), "--workdir", str(self.work),
                "--logs", str(self.logs), "--phase", phase]
        if candidate:
            argv += ["--candidate-patch", str(candidate)]
        return subprocess.run(argv, capture_output=True, timeout=45)

    def test_base_fails_target_assertion_and_binary_fixture_is_applied(self):
        process = self.invoke("base")
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        result = read_json(self.logs / "result.json")
        self.assertEqual(result["cases"]["new"]["status"], "FAIL")
        self.assertEqual(result["cases"]["existing"]["status"], "PASS")
        self.assertIn("expected right boundary", result["cases"]["new"]["detail"])
        self.assertEqual((self.logs / "reward.txt").read_text().strip(), "0")
        self.assertEqual((self.work / "fixture.png").read_bytes()[0], 137)

    def test_gold_survives_test_patch_application_and_passes(self):
        git(self.work, "apply", str(self.task / "solution/gold.patch"))
        process = self.invoke()
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        self.assertEqual(read_json(self.logs / "result.json")["reward"], 1)
        self.assertIn("n => n", (self.work / "layout.js").read_text())

    def test_baseline_checks_existing_p2p_without_adding_f2p(self):
        self.assertEqual(self.invoke("baseline").returncode, 0)
        result = read_json(self.logs / "result.json")
        self.assertEqual(result["reward"], 1)
        self.assertEqual(set(result["cases"]), {"existing"})
        self.assertFalse((self.work / "new.test.js").exists())

    def test_candidate_patching_real_production_fix(self):
        self.assertEqual(self.invoke(candidate=self.task / "solution/gold.patch").returncode, 0)
        self.assertEqual(read_json(self.logs / "result.json")["reward"], 1)

    def test_noop_patch_is_a_candidate_failure(self):
        path = self.root / "empty.patch"
        path.write_bytes(b"")
        self.assertEqual(self.invoke(candidate=path).returncode, 0)
        self.assertEqual(read_json(self.logs / "result.json")["reward"], 0)

    def test_tampered_p2p_is_rejected_without_resetting_candidate_code(self):
        write_text(self.work / "existing.test.js", "module.exports = () => {};\n")
        self.assertEqual(self.invoke().returncode, 0)
        result = read_json(self.logs / "result.json")
        self.assertEqual(result["reward"], 0)
        self.assertIn("Protected", result["failure_reason"])

    def test_missing_case_is_zero_not_silent_success(self):
        config = read_json(self.task / "tests/config.json")
        config["command"] = config["baseline_command"]
        write_json(self.task / "tests/config.json", config)
        self.assertEqual(self.invoke().returncode, 0)
        result = read_json(self.logs / "result.json")
        self.assertEqual(result["cases"]["new"]["status"], "MISSING")
        self.assertEqual(result["reward"], 0)

    def test_grader_crash_removes_stale_reward_and_reports_error(self):
        self.logs.mkdir()
        write_text(self.logs / "reward.txt", "1\n")
        write_text(self.task / "tests/config.json", "not valid json")
        process = self.invoke()
        self.assertEqual(process.returncode, 2)
        self.assertFalse((self.logs / "reward.txt").exists())
        self.assertTrue((self.logs / "error.json").exists())

    def test_malformed_report_is_not_mislabeled_as_candidate_failure(self):
        config = read_json(self.task / "tests/config.json")
        config["log_parser"]["kind"] = "junit"  # actual runner emits JSON
        write_json(self.task / "tests/config.json", config)
        self.assertEqual(self.invoke().returncode, 2)
        self.assertFalse((self.logs / "reward.txt").exists())

    def test_missing_executable_is_infrastructure_failure(self):
        config = read_json(self.task / "tests/config.json")
        config["command"] = ["iid-no-such-executable-123"]
        write_json(self.task / "tests/config.json", config)
        self.assertEqual(self.invoke().returncode, 2)
        self.assertFalse((self.logs / "reward.txt").exists())
