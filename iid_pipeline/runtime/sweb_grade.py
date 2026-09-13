"""Task-local OJ verifier. A grader/infrastructure crash never creates reward.txt."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import sys
import time

# Explicit sibling import works with python -I and ignores agent PYTHONPATH.
_spec = importlib.util.spec_from_file_location("iid_trusted_logparsers", Path(__file__).with_name("logparsers.py"))
_parsers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_parsers)


class InfrastructureError(RuntimeError):
    pass


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_path(root, value):
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts or "\\" in value or ":" in value:
        raise InfrastructureError(f"Unsafe task path: {value}")
    root = Path(root).resolve()
    target = root / value
    if not target.resolve().is_relative_to(root):
        raise InfrastructureError(f"Path escapes repository: {value}")
    return target


def digest(path):
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        return "NON_REGULAR_FILE"
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def execute(command, workdir, output, timeout):
    env = dict(os.environ)
    env.update({"CI": "true", "TZ": "UTC", "LANG": "C.UTF-8", "PYTHONPATH": ""})
    started = time.monotonic()
    with output.open("wb") as stream:
        try:
            process = subprocess.Popen(command, cwd=workdir, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        except OSError as exc:
            raise InfrastructureError(f"Test command could not start: {exc}") from exc
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
    return process.returncode, timed_out, time.monotonic() - started


def judge(config_path, workdir, logs, phase="candidate", candidate_patch=None):
    logs.mkdir(parents=True, exist_ok=True)
    # Remove stale output BEFORE reading config or importing a parser result.
    for name in ("reward.txt", "result.json", "error.json"):
        (logs / name).unlink(missing_ok=True)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if phase not in {"candidate", "base", "baseline"}:
        raise InfrastructureError("Unknown verification phase")
    f2p = [] if phase == "baseline" else config["FAIL_TO_PASS"]
    p2p = config["PASS_TO_PASS"]

    def finish(result):
        result["phase"] = phase
        write_json(logs / "result.json", result)
        (logs / "reward.txt").write_text(str(result["reward"]) + "\n", encoding="ascii")
        return result

    def rejected(reason):
        result = _parsers.grade({}, f2p, p2p, 1)
        result["failure_reason"] = reason
        return finish(result)

    if candidate_patch is not None:
        for args in (["--check"], []):
            proc = subprocess.run(["git", "-C", str(workdir), "apply", *args, str(candidate_patch)],
                                  capture_output=True, timeout=120)
            if proc.returncode:
                (logs / "candidate_apply.txt").write_bytes(proc.stdout + proc.stderr)
                return rejected("Candidate patch could not be applied")

    # Restore-only-test-files would conceal attempted test tampering. Detect it instead.
    for name, expected in config["protected_files"].items():
        if digest(safe_path(workdir, name)) != expected:
            return rejected(f"Protected test/config file changed: {name}")
    for asset in config["images"]:
        if digest(safe_path(workdir, asset["repo_path"])) != asset["sha256"]:
            return rejected(f"Problem image missing/changed: {asset['repo_path']}")
    for resource in config.get("resources", []):
        if digest(safe_path(workdir, resource["repo_path"])) != resource["sha256"]:
            return rejected(f"Offline reproduction resource missing/changed: {resource['repo_path']}")

    if phase != "baseline":
        patch = config_path.parent / "test.patch"
        # git apply is the only patch engine, including Git binary patches.
        for args in (["--check"], []):
            proc = subprocess.run(["git", "-C", str(workdir), "apply", *args, str(patch)],
                                  capture_output=True, timeout=120)
            with (logs / "patch_output.txt").open("ab") as stream:
                stream.write(proc.stdout + proc.stderr)
            if proc.returncode:
                return rejected("test_patch could not be applied")
        for name, expected in config.get("fixture_sha256", {}).items():
            if digest(safe_path(workdir, name)) != expected:
                return rejected(f"Test fixture missing/changed: {name}")

    parser = config.get("baseline_log_parser", config["log_parser"]) if phase == "baseline" else config["log_parser"]
    command = config["baseline_command"] if phase == "baseline" else config["command"]
    report_path = safe_path(workdir, parser["report_path"]) if parser.get("report_path") else None
    if report_path:
        # Never consume stale reports left by a candidate or previous run.
        report_path.unlink(missing_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
    raw_log = logs / "test_output.txt"
    exit_code, timed_out, seconds = execute(command, workdir, raw_log, config["test_timeout_sec"])
    if timed_out:
        result = _parsers.grade({}, f2p, p2p, exit_code, True)
    elif report_path and not report_path.exists():
        # Failed test discovery is a candidate failure; an unexplained absent
        # successful reporter is a broken judge contract, not a silent zero.
        if exit_code == 0:
            raise InfrastructureError("Test process succeeded but structured report is missing")
        result = _parsers.grade({}, f2p, p2p, exit_code)
    else:
        text = (report_path or raw_log).read_text(encoding="utf-8", errors="replace")
        if report_path:
            (logs / "runner_report.txt").write_text(text, encoding="utf-8")
        observed = _parsers.parse(text, parser)
        result = _parsers.grade(observed, f2p, p2p, exit_code)
    result["runtime_sec"] = seconds
    return finish(result)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--workdir", type=Path, default=Path("/testbed"))
    parser.add_argument("--logs", type=Path, default=Path("/logs/verifier"))
    parser.add_argument("--phase", choices=["candidate", "base", "baseline"], default="candidate")
    parser.add_argument("--candidate-patch", type=Path)
    args = parser.parse_args(argv)
    try:
        judge(args.config, args.workdir, args.logs, args.phase, args.candidate_patch)
        return 0  # Completed judging, including a legitimate reward of zero.
    except Exception as exc:
        args.logs.mkdir(parents=True, exist_ok=True)
        (args.logs / "reward.txt").unlink(missing_ok=True)
        write_json(args.logs / "error.json", {"kind": "verifier_error", "error": str(exc),
                                              "type": type(exc).__name__})
        print(f"Verifier error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
