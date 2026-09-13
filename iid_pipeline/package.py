"""Build and statically validate the required five-entry task directory."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tomllib
import uuid

from .common import (canonical_hash, file_hash, inside, read_json, relative_path, require,
                     run, tree_hash, utc, write_json, write_text)
from .policy import validate_source, patch_fingerprint
from .prepare import image_info, review_subject, image_urls, reproduction_urls

REVIEW_CHECKS = {"real_issue_pr", "pre_fix_context", "atomic_production_fix", "unambiguous_requirements",
                 "visual_information_gain", "p2p_preserved", "dedup_reviewed", "gold_matches_upstream"}
RUNTIME_DIR = Path(__file__).with_name("runtime")


def patch_paths(path):
    data = Path(path).read_bytes()
    require(data.startswith(b"diff --git "), f"Not a Git diff: {path}")
    require(b"Binary files " not in data, "Binary fixture missing payload; generate with git diff --binary --full-index")
    raw = run(["git", "apply", "--numstat", "-z", str(Path(path).resolve())])
    fields, paths, index = raw.split(b"\0"), set(), 0
    while index < len(fields) and fields[index]:
        record = fields[index].split(b"\t", 2)
        require(len(record) == 3, "Unrecognized git numstat output")
        if record[2]:
            paths.add(relative_path(record[2].decode("utf-8")))
        else:
            index += 1
            paths.add(relative_path(fields[index].decode("utf-8")))
            index += 1
            paths.add(relative_path(fields[index].decode("utf-8")))
        index += 1
    require(paths, "Empty patch")
    return paths


def archive_files(path):
    files = {}
    links = set()
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        for member in members:
            name = relative_path(member.name.rstrip("/"))
            require(not (member.isdev() or member.isfifo() or member.islnk()), f"Unsupported archive member: {name}")
            if member.issym():
                require(not PurePosixPath(member.linkname).is_absolute(), "Absolute archive symlink")
                parts = list(PurePosixPath(name).parent.parts)
                for part in PurePosixPath(member.linkname).parts:
                    if part == "..":
                        require(parts, "Archive symlink escapes root")
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
                links.add(name)
            elif member.isfile():
                files[name] = archive.extractfile(member).read()
        for name in files:
            require(not any(parent.as_posix() in links for parent in PurePosixPath(name).parents),
                    "Archive writes through a symlink")
    require(files, "Empty source archive")
    return files


def validate_parser(spec):
    require(spec["kind"] in {"json", "junit", "jest", "mocha", "regex"}, "Unsupported log parser")
    if spec.get("report_path"):
        path = relative_path(spec["report_path"])
        require(path.startswith(".iid-results/"), "Reports must live under .iid-results/ to avoid overwriting source")
    if spec["kind"] != "regex":
        require(spec.get("report_path"), "Structured parser must read a separate fresh reporter file")
    else:
        pattern = re.compile(spec["pattern"])
        require({"id", "status"} <= set(pattern.groupindex), "Regex needs id and status named groups")
        require(set(spec["status_map"].values()) <= {"PASS", "FAIL", "ERROR", "SKIP", "TIMEOUT"}, "Invalid status map")
        require("PASS" in spec["status_map"].values() and "FAIL" in spec["status_map"].values(),
                "Failure-only parsers are forbidden")


def validate_manifest(path):
    path = Path(path).resolve()
    data = read_json(path)
    require(data["schema_version"] == 1, "Unsupported manifest schema")
    iid = validate_source(data["source"])
    review = data["review"]
    require(review.get("approved") is True and review.get("reviewer"), "Independent review has not approved this task")
    utc(review["reviewed_at"])
    require(REVIEW_CHECKS <= set(review["checks"]), "Independent review checks are incomplete")
    require(review["subject_sha256"] == review_subject(path), "Files/manifest changed since independent review")
    provenance = read_json(path.parent / "source.json")
    require(provenance["source"] == data["source"], "Source differs from archived discovery record")
    require(provenance["archive_sha256"] == file_hash(inside(path.parent, data["source_archive"])), "Base archive changed")
    files = archive_files(inside(path.parent, data["source_archive"]))
    test_paths = patch_paths(inside(path.parent, data["test_patch"]))
    gold_paths = patch_paths(inside(path.parent, data["gold_patch"]))
    tests = data["tests"]
    f2p, p2p = tests["FAIL_TO_PASS"], tests["PASS_TO_PASS"]
    require(isinstance(f2p, list) and isinstance(p2p, list) and f2p and p2p, "F2P and P2P must both be nonempty lists")
    require(all(isinstance(x, str) and x.strip() for x in f2p + p2p), "Invalid test id")
    require(len(set(f2p + p2p)) == len(f2p + p2p), "Duplicate or overlapping F2P/P2P ids")
    protected = {relative_path(x) for x in tests["protected_paths"]}
    p2p_files = {relative_path(x) for x in tests["p2p_files"]}
    require(p2p_files and all(x in files for x in p2p_files), "P2P must refer to existing base test files")
    require(test_paths <= protected, "test.patch touches undeclared protected test paths")
    require(not (gold_paths & (protected | p2p_files)), "Gold must not modify protected tests/config")
    require(not (test_paths & p2p_files), "Put F2P in separate tests; test.patch may not rewrite existing P2P files")
    require(any(PurePosixPath(x).suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".sass", ".less", ".html"}
                for x in gold_paths), "Gold needs a production frontend code change")
    require(set(tests["f2p_cases"]) == set(f2p), "Each F2P needs a requirement and target failure matcher")
    for case in tests["f2p_cases"].values():
        require(relative_path(case["file"]) in test_paths, "F2P file must be in new test patch")
        require(case.get("requirement", "").strip() and case.get("failure_regex", "").strip(), "Missing F2P requirement/failure regex")
        require(case["failure_regex"] not in {".*", ".+", "^", "$"}, "Target failure matcher cannot match everything")
        re.compile(case["failure_regex"])
    for key in ("command", "baseline_command"):
        require(isinstance(tests[key], list) and tests[key] and all(isinstance(x, str) for x in tests[key]),
                f"{key} must be a nonempty argv array")
    validate_parser(tests["log_parser"])
    validate_parser(tests.get("baseline_log_parser", tests["log_parser"]))
    require(0 < tests["test_timeout_sec"] < data["verifier_timeout_sec"], "Leave verifier time for patching and grading")
    for name, digest in tests.get("fixture_sha256", {}).items():
        require(relative_path(name) in test_paths and re.fullmatch(r"[0-9a-f]{64}", digest), "Invalid fixture hash/path")
    binary_paths = {line.split(b"\t", 2)[2].decode() for line in
                    run(["git", "apply", "--numstat", "-z", str(inside(path.parent, data["test_patch"]))]).split(b"\0")
                    if line.startswith(b"-\t-\t") and line.split(b"\t", 2)[2]}
    require(binary_paths <= set(tests.get("fixture_sha256", {})), "Every binary test fixture needs an expected SHA256")
    instruction = inside(path.parent, data["instruction"]).read_text(encoding="utf-8")
    require("/testbed" in instruction and data["images"], "Instruction must reference /testbed and at least one image")
    require(not image_urls(instruction), "Instruction still references a remote image; archive it or remove reviewed irrelevant context")
    require(not reproduction_urls(instruction), "Instruction still requires an online IDE; export it for offline use")
    seen = set()
    for asset in data["images"]:
        asset_path = relative_path(asset["repo_path"])
        require(asset_path.startswith(f"assets/{iid}/"), "Problem images must be under assets/<instance_id>/")
        require(asset_path not in files and asset_path not in seen, "Image collides with source/another image")
        seen.add(asset_path)
        info = image_info(inside(path.parent, asset["file"]).read_bytes())
        require(info["sha256"] == asset["sha256"], "Image hash mismatch")
        require("/testbed/" + asset_path in instruction, "Image path not referenced in instruction")
        for key in ("source_url", "role", "visual_evidence", "information_gain", "without_image"):
            require(asset.get(key, "").strip(), f"Missing image evidence field: {key}")
        require(asset["linked_test_ids"] and set(asset["linked_test_ids"]) <= set(f2p), "Image must link to F2P requirements")
    archived_urls = set()
    for resource in data.get("resources", []):
        repo_path = relative_path(resource["repo_path"])
        require(repo_path.startswith(f"assets/{iid}/") and repo_path not in files and repo_path not in seen,
                "Reproduction resource must have a unique per-task assets path")
        seen.add(repo_path)
        require(file_hash(inside(path.parent, resource["file"])) == resource["sha256"], "Reproduction asset hash mismatch")
        require(resource.get("instructions", "").strip(), "Reproduction resource needs offline usage instructions")
        require("/testbed/" + repo_path in instruction, "Reproduction resource not referenced in instruction")
        archived_urls.add(resource["source_url"])
    required_urls = set()
    for issue in provenance.get("issues", []):
        required_urls.update(reproduction_urls(issue.get("body", "")))
    require(required_urls <= archived_urls, "Not all issue reproduction links have offline resource exports")
    require(all(data["tags"].get(x) for x in ("framework", "renderer", "defect", "difficulty")), "Missing distribution/difficulty tags")
    require(data.get("negative_patches"), "At least one plausible wrong patch is required")
    for name in data["negative_patches"]:
        require(not patch_paths(inside(path.parent, name)) & (protected | p2p_files), "Negative patch may not tamper with tests")
    env = data["environment"]
    require(bool(re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", env["base_image"])), "base_image must be digest-pinned")
    for key in ("cpus", "memory_mb", "storage_mb", "build_timeout_sec"):
        require(isinstance(env[key], (int, float)) and env[key] > 0, f"Invalid environment.{key}")
    require(data["agent_timeout_sec"] > 0, "Invalid agent timeout")
    inside(path.parent, env["setup_script"]).read_text(encoding="utf-8")
    if env.get("bootstrap_script"):
        inside(path.parent, env["bootstrap_script"]).read_text(encoding="utf-8")
    return data, files, protected | p2p_files


def build(manifest_path, output):
    manifest_path = Path(manifest_path).resolve()
    data, files, protected = validate_manifest(manifest_path)
    iid = validate_source(data["source"])
    parent = Path(output).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / iid
    require(not target.exists(), f"Task exists: {target}")
    stage = parent / (".building-" + uuid.uuid4().hex)
    stage.mkdir()
    for folder in ("environment", "tests", "solution"):
        (stage / folder).mkdir()
    shutil.copyfile(inside(manifest_path.parent, data["instruction"]), stage / "instruction.md")
    shutil.copyfile(inside(manifest_path.parent, data["source_archive"]), stage / "environment/base.tar")
    setup = inside(manifest_path.parent, data["environment"]["setup_script"]).read_text(encoding="utf-8")
    write_text(stage / "environment/setup.sh", setup)
    if data["environment"].get("bootstrap_script"):
        write_text(stage / "environment/bootstrap.sh",
                   inside(manifest_path.parent, data["environment"]["bootstrap_script"]).read_text(encoding="utf-8"))
    images = []
    for image in data["images"]:
        destination = stage / "environment" / image["repo_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(manifest_path.parent, image["file"]), destination)
        images.append({k: v for k, v in image.items() if k != "file"})
    resources = []
    for resource in data.get("resources", []):
        destination = stage / "environment" / resource["repo_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(manifest_path.parent, resource["file"]), destination)
        resources.append({k: v for k, v in resource.items() if k != "file"})
    shutil.copyfile(inside(manifest_path.parent, data["gold_patch"]), stage / "solution/gold.patch")
    shutil.copyfile(inside(manifest_path.parent, data["test_patch"]), stage / "tests/test.patch")
    for filename in ("sweb_grade.py", "logparsers.py"):
        shutil.copyfile(RUNTIME_DIR / filename, stage / "tests" / filename)
    for index, name in enumerate(data["negative_patches"]):
        dest = stage / "tests/negative_patches" / f"{index:03}.patch"
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(inside(manifest_path.parent, name), dest)
    config = {**data["tests"], "repo": data["source"]["repo"], "instance_id": iid,
              "base_commit": data["source"]["base_commit"], "images": images, "resources": resources,
              "protected_files": {name: __import__("hashlib").sha256(files[name]).hexdigest() if name in files else None
                                  for name in sorted(protected)},
              "source": data["source"], "review": data["review"], "tags": data["tags"],
              "patch_fingerprint": patch_fingerprint((stage / "solution/gold.patch").read_text(encoding="utf-8"))}
    write_json(stage / "tests/config.json", config)
    write_text(stage / "tests/test.sh", TEST_SH)
    write_text(stage / "solution/solve.sh", SOLVE_SH)
    write_text(stage / "environment/Dockerfile", dockerfile(data))
    write_text(stage / "task.toml", task_toml(data, iid))
    validate_bundle(stage, expected_iid=iid)
    stage.rename(target)
    return target


TEST_SH = '''#!/bin/bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt
exec /usr/bin/python3 -I -B "$HERE/sweb_grade.py" --config "$HERE/config.json" "$@"
'''

SOLVE_SH = '''#!/bin/bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
git -C /testbed apply --check "$HERE/gold.patch"
git -C /testbed apply "$HERE/gold.patch"
'''


def dockerfile(data):
    iid = validate_source(data["source"])
    bootstrap = 'COPY bootstrap.sh /tmp/iid-bootstrap.sh\nRUN bash /tmp/iid-bootstrap.sh && rm /tmp/iid-bootstrap.sh\n' if data['environment'].get('bootstrap_script') else ''
    return f'''FROM {data["environment"]["base_image"]}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends git bash python3 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python3 -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required by the verifier'"
ENV TZ=UTC LANG=C.UTF-8 CI=true
{bootstrap}WORKDIR /testbed
COPY base.tar /tmp/iid-base.tar
RUN tar -xf /tmp/iid-base.tar -C /testbed && rm /tmp/iid-base.tar && git init && git config core.autocrlf false && git add --force . && git -c user.name=IID -c user.email=iid@example.invalid commit -m "base snapshot"
COPY setup.sh /tmp/iid-setup.sh
RUN bash /tmp/iid-setup.sh && git restore --worktree --staged . && rm /tmp/iid-setup.sh
COPY assets/{iid}/ /testbed/assets/{iid}/
RUN printf '\\n/assets/{iid}/\\n/.iid-results/\\n' >> /testbed/.git/info/exclude
WORKDIR /testbed
'''


def task_toml(data, iid):
    q = lambda s: json.dumps(s, ensure_ascii=False)
    env = data["environment"]
    return f'''schema_version = "1.2"

[task]
name = {q("swe-bench-multimodal/" + iid)}
description = {q("Resolve the visual GitHub issue in /testbed: " + iid)}
authors = [{{ name = {q(data["review"]["reviewer"])} }}]
keywords = ["swe-bench", "swe-bench-multimodal", "javascript", "agentic"]

[metadata]
benchmark = "SWE-bench Multimodal"
repo = {q(data["source"]["repo"])}
instance_id = {q(iid)}

[environment]
build_timeout_sec = {float(env["build_timeout_sec"])}
cpus = {env["cpus"]}
memory_mb = {env["memory_mb"]}
storage_mb = {env["storage_mb"]}
allow_internet = false

[agent]
timeout_sec = {float(data["agent_timeout_sec"])}
override_setup_timeout_sec = 1800.0

[verifier]
timeout_sec = {float(data["verifier_timeout_sec"])}
'''


def validate_bundle(task, expected_iid=None):
    task = Path(task)
    require({x.name for x in task.iterdir()} == {"instruction.md", "task.toml", "environment", "tests", "solution"},
            "Task root must have exactly instruction.md, task.toml, environment/, tests/, solution/")
    for filename in ("environment/Dockerfile", "environment/base.tar", "environment/setup.sh", "tests/test.sh",
                     "tests/config.json", "tests/sweb_grade.py", "tests/logparsers.py", "tests/test.patch",
                     "solution/solve.sh", "solution/gold.patch"):
        require((task / filename).is_file(), f"Missing required task file: {filename}")
    meta = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
    require(meta["schema_version"] == "1.2", "task.toml schema_version must be 1.2")
    require(meta["environment"]["allow_internet"] is False, "Agent environment must be offline")
    for field in ("cpus", "memory_mb", "storage_mb", "build_timeout_sec"):
        require(meta["environment"][field] > 0, f"Invalid resource: {field}")
    require(meta["agent"]["timeout_sec"] > 0 and meta["verifier"]["timeout_sec"] > 0, "Missing timeouts")
    config = read_json(task / "tests/config.json")
    iid = validate_source(config["source"])
    require(iid == config["instance_id"] == meta["metadata"]["instance_id"], "Inconsistent task identity")
    require(iid == (expected_iid or task.name), "Invalid <owner>__<repo>-<number> directory name")
    require(config["FAIL_TO_PASS"] and config["PASS_TO_PASS"], "Empty test manifest")
    require(len(set(config["FAIL_TO_PASS"] + config["PASS_TO_PASS"])) == len(config["FAIL_TO_PASS"] + config["PASS_TO_PASS"]), "Duplicate test ids")
    require((task / "tests/test.sh").read_text(encoding="utf-8") == TEST_SH, "Verifier entry changed")
    require((task / "solution/solve.sh").read_text(encoding="utf-8") == SOLVE_SH, "Oracle entry changed")
    for filename in ("sweb_grade.py", "logparsers.py"):
        require(file_hash(task / "tests" / filename) == file_hash(RUNTIME_DIR / filename), "Vendored judge version mismatch")
    require(config["images"], "Missing problem image")
    prompt = (task / "instruction.md").read_text(encoding="utf-8")
    for asset in config["images"]:
        path = inside(task / "environment", asset["repo_path"])
        require(image_info(path.read_bytes())["sha256"] == asset["sha256"], "Image corrupt/changed")
        require("/testbed/" + asset["repo_path"] in prompt, "Image not referenced")
    for resource in config.get("resources", []):
        require(file_hash(inside(task / "environment", resource["repo_path"])) == resource["sha256"], "Reproduction resource changed")
        require("/testbed/" + resource["repo_path"] in prompt, "Reproduction resource not referenced")
    return {"instance_id": iid, "task_sha256": tree_hash(task), "metadata": meta}
