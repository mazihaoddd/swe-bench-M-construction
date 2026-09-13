"""Offline Docker qualification and evidence-bound release."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import uuid

from .common import (PipelineError, file_hash, load_records, now, read_json,
                     require, run, tree_hash, write_json)
from .package import validate_bundle
from .policy import duplicate_reasons


def mount(source, destination, readonly=True):
    source = str(Path(source).resolve())
    require("," not in source, "Docker bind mount source cannot contain a comma")
    return "type=bind,src=" + source + ",dst=" + destination + (",readonly" if readonly else "")


class Docker:
    def __init__(self, executable="docker"):
        self.executable = executable

    def check(self):
        os_type = run([self.executable, "info", "--format", "{{.OSType}}"], timeout=30).decode().strip()
        require(os_type == "linux", "Docker must be running Linux containers")

    def build(self, task, output):
        task, output = Path(task).resolve(), Path(output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        meta = validate_bundle(task)["metadata"]
        iidfile = output / ("image-" + uuid.uuid4().hex + ".txt")
        run([self.executable, "build", "--iidfile", iidfile, task / "environment"],
            timeout=meta["environment"]["build_timeout_sec"])
        image_id = iidfile.read_text().strip()
        require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)), "Docker did not return an immutable image id")
        return image_id

    def limits(self, task):
        env = validate_bundle(task)["metadata"]["environment"]
        return ["--network", "none", "--cpus", str(env["cpus"]), "--memory", f'{env["memory_mb"]}m',
                "--pids-limit", "512"]

    def evaluate(self, task, image_id, logs, *, phase="candidate", gold=False, candidate=None):
        task, logs = Path(task).resolve(), Path(logs).resolve()
        require(not logs.exists(), f"Evaluation logs already exist: {logs}")
        logs.mkdir(parents=True)
        name = "iid-judge-" + uuid.uuid4().hex
        argv = [self.executable, "run", "--rm", "--name", name, *self.limits(task),
                "--mount", mount(task / "tests", "/tests"),
                "--mount", mount(logs, "/logs/verifier", False)]
        if gold:
            argv += ["--mount", mount(task / "solution", "/solution")]
        if candidate:
            argv += ["--mount", mount(candidate, "/candidate.patch")]
        if gold:
            command = ["/bin/bash", "-c", "bash /solution/solve.sh && bash /tests/test.sh"]
        else:
            command = ["/bin/bash", "/tests/test.sh", "--phase", phase]
            if candidate:
                command += ["--candidate-patch", "/candidate.patch"]
        try:
            output = run(argv + [image_id] + command,
                         timeout=validate_bundle(task)["metadata"]["verifier"]["timeout_sec"] + 30)
            (logs / "docker_output.txt").write_bytes(output)
        except PipelineError:
            # A timed-out host docker client may leave the container running.
            subprocess.run([self.executable, "rm", "-f", name], capture_output=True, timeout=30)
            raise
        require((logs / "reward.txt").is_file() and (logs / "result.json").is_file(),
                f"Verifier produced no reward (infrastructure/grader failure): {logs}")
        result = read_json(logs / "result.json")
        require((logs / "reward.txt").read_text().strip() == str(result["reward"]), "Reward/report disagreement")
        return result


def assert_qualification(config, results, runs):
    require(runs >= 2, "Qualification requires at least two independent runs per state")
    for phase in ("baseline", "base", "gold"):
        observed = [x["result"] for x in results if x["phase"] == phase]
        require(len(observed) == runs, f"Incomplete {phase} repetitions")
        stable = None
        for result in observed:
            require(not result.get("timed_out") and not result.get("missing"), f"{phase}: missing cases/timeout")
            expected_ids = config["PASS_TO_PASS"] + ([] if phase == "baseline" else config["FAIL_TO_PASS"])
            require(set(result["cases"]) == set(expected_ids), f"{phase}: case manifest mismatch")
            require(all(result["cases"][x]["status"] == "PASS" for x in config["PASS_TO_PASS"]), f"{phase}: P2P regression")
            if phase == "base":
                require(result["reward"] == 0 and result["exit_code"] != 0, "Base must fail through a test assertion")
                for name in config["FAIL_TO_PASS"]:
                    case = result["cases"][name]
                    require(case["status"] == "FAIL", f"F2P not an assertion failure on base: {name}")
                    require(re.search(config["f2p_cases"][name]["failure_regex"], case["detail"]),
                            f"F2P fails for the wrong reason: {name}")
            else:
                require(result["reward"] == 1 and result["exit_code"] == 0, f"{phase}: must pass")
                require(all(result["cases"][name]["status"] == "PASS" for name in expected_ids),
                        f"{phase}: report reward contradicts individual test results")
            signature = {"statuses": {name: x["status"] for name, x in result["cases"].items()},
                         "observed_ids": result["observed_ids"], "exit_code": result["exit_code"]}
            require(stable is None or stable == signature, f"Flaky test collection/status in {phase}")
            stable = signature
    negatives = [x for x in results if x["phase"] == "negative"]
    require(negatives, "No negative-patch validation")
    for entry in negatives:
        result = entry["result"]
        require(result["reward"] == 0 and not result["missing"] and not result.get("timed_out"),
                "Negative patch must execute the tests and be rejected, not fail to apply/start")
        require(any(x["status"] == "FAIL" for x in result["cases"].values()), "Negative patch lacks a real assertion failure")


def qualify(task, output, runs=5, docker=None):
    task, output = Path(task).resolve(), Path(output).resolve()
    info = validate_bundle(task)
    require(not output.exists(), "Qualification directory exists; use a new run directory")
    output.mkdir(parents=True)
    docker = docker or Docker()
    docker.check()
    image_id = docker.build(task, output)
    results = []
    for phase in ("baseline", "base", "gold"):
        for index in range(runs):
            log_dir = output / f"{phase}-{index}"
            result = docker.evaluate(task, image_id, log_dir, phase="base" if phase == "base" else "baseline" if phase == "baseline" else "candidate",
                                     gold=phase == "gold")
            results.append({"phase": phase, "logs": log_dir.name, "result": result})
            write_json(output / "progress.json", results)
    for index, patch in enumerate(sorted((task / "tests/negative_patches").glob("*.patch"))):
        log_dir = output / f"negative-{index}"
        result = docker.evaluate(task, image_id, log_dir, candidate=patch)
        results.append({"phase": "negative", "logs": log_dir.name, "patch_sha256": file_hash(patch), "result": result})
    config = read_json(task / "tests/config.json")
    assert_qualification(config, results, runs)
    require(tree_hash(task) == info["task_sha256"], "Task changed during qualification")
    receipt = {"version": 1, "status": "qualified", "instance_id": info["instance_id"], "task_sha256": info["task_sha256"],
               "image_id": image_id, "runs": runs, "created_at": now(), "results": results}
    receipt["evidence_files"] = {p.relative_to(output).as_posix(): file_hash(p) for p in output.rglob("*") if p.is_file()}
    write_json(output / "qualification.json", receipt)
    return receipt


def load_qualification(task, receipt_path):
    task, receipt_path = Path(task), Path(receipt_path)
    info = validate_bundle(task)
    receipt = read_json(receipt_path)
    require(receipt["status"] == "qualified" and receipt["task_sha256"] == info["task_sha256"], "Missing/stale qualification")
    require(receipt["instance_id"] == info["instance_id"], "Qualification task mismatch")
    for name, expected in receipt["evidence_files"].items():
        from .common import inside
        require(file_hash(inside(receipt_path.parent, name)) == expected, "Qualification evidence changed")
    for entry in receipt["results"]:
        from .common import inside
        require(read_json(inside(receipt_path.parent, entry["logs"] + "/result.json")) == entry["result"], "Result differs from evidence")
    assert_qualification(read_json(task / "tests/config.json"), receipt["results"], receipt["runs"])
    expected_negatives = sorted(file_hash(p) for p in (task / "tests/negative_patches").glob("*.patch"))
    require(sorted(x["patch_sha256"] for x in receipt["results"] if x["phase"] == "negative") == expected_negatives,
            "Not all negative patches were verified")
    return receipt


def publish(task_receipts, exclusions_path, output):
    """Release tasks only after execution qualification, review and deduplication."""
    output = Path(output).resolve()
    require(not output.exists(), "Release directory already exists")
    exclusions = load_records(exclusions_path)
    require(exclusions, "Supply a nonempty official task exclusion registry")
    checked, records, qualifications = [], [], []
    for task, receipt_path in task_receipts:
        task = Path(task).resolve()
        receipt = load_qualification(task, receipt_path)
        config = read_json(task / "tests/config.json")
        record = {**config["source"], "instance_id": receipt["instance_id"],
                  "patch_fingerprint": config["patch_fingerprint"]}
        require(not duplicate_reasons(record, exclusions + records),
                f"Duplicate root/PR/issue/patch: {record['instance_id']}")
        records.append(record)
        checked.append((task, receipt))
        qualifications.append({"instance_id": receipt["instance_id"],
                               "task_sha256": receipt["task_sha256"],
                               "image_id": receipt["image_id"],
                               "receipt_sha256": file_hash(receipt_path)})
    require(checked, "Cannot publish an empty task set")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / (".release-" + uuid.uuid4().hex)
    staging.mkdir()
    for task, receipt in checked:
        dest = staging / receipt["instance_id"]
        shutil.copytree(task, dest)
        require(validate_bundle(dest)["task_sha256"] == receipt["task_sha256"], "Copy changed task")
    staging.rename(output)
    # Release ledger stays OUTSIDE the strict task-directory tree.
    write_json(output.with_name(output.name + ".release.json"), {
        "created_at": now(), "tasks": records, "qualifications": qualifications,
        "exclusions_sha256": file_hash(exclusions_path)})
    return output
