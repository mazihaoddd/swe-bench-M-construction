"""Bounded task-level concurrency and evidence-aware restart of a production batch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .common import PipelineError, file_hash, load_records, read_json, require, write_json
from .package import build, validate_bundle, validate_manifest
from .policy import duplicate_reasons, patch_fingerprint, validate_source
from .verify import Docker, load_qualification, publish, qualify


def index_exclusions(input_path, output):
    records = []
    for row in load_records(input_path):
        source = row.get("source", row)
        iid = row.get("instance_id", source.get("instance_id"))
        require(iid, "Every reference record needs instance_id")
        record = {"instance_id": iid, "repo": source.get("repo"),
                  "root_task_id": source.get("root_task_id"), "issue_urls": source.get("issue_urls", [])}
        if row.get("patch"):
            record["patch_fingerprint"] = patch_fingerprint(row["patch"])
        if row.get("patch_fingerprint"):
            record["patch_fingerprint"] = row["patch_fingerprint"]
        records.append(record)
    require(records, "Empty official reference dataset")
    write_json(output, {"source_file_sha256": file_hash(input_path), "records": records})
    return {"records": len(records), "output": str(output)}


def produce(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = read_json(plan_path)
    resolve = lambda name: (plan_path.parent / name).resolve()
    workspace = resolve(plan["workspace"])
    manifests = [resolve(x) for x in plan["manifests"]]
    require(manifests, "The batch has no authored manifests")
    jobs = plan.get("jobs", 2)
    require(isinstance(jobs, int) and 1 <= jobs <= 16, "jobs must be between 1 and 16")
    exclusions_path = resolve(plan["exclusions"])
    exclusions = load_records(exclusions_path)
    require(exclusions, "A nonempty official exclusion registry is required")
    workspace.mkdir(parents=True, exist_ok=True)
    # Fail cheaply before image builds if input/review/dedup is incomplete.
    records = []
    for path in manifests:
        data, _, _ = validate_manifest(path)
        iid = validate_source(data["source"])
        record = {**data["source"], "instance_id": iid,
                  "patch_fingerprint": patch_fingerprint((path.parent / data["gold_patch"]).read_text(encoding="utf-8"))}
        require(not duplicate_reasons(record, exclusions + records), f"Duplicate task: {iid}")
        records.append(record)
    docker = Docker(plan.get("docker", "docker"))
    docker.check()

    def process(path):
        data = read_json(path)
        iid = validate_source(data["source"])
        task = workspace / "staging" / iid
        if task.exists():
            validate_bundle(task)
            require(read_json(task / "tests/config.json")["review"] == data["review"],
                    "Staged input changed; use a fresh workspace")
        else:
            build(path, workspace / "staging")
        evidence = workspace / "qualification" / iid
        receipt = evidence / "qualification.json"
        if receipt.exists():
            load_qualification(task, receipt)
        else:
            # Preserve failed logs, never erase them or trust a partial run.
            if evidence.exists():
                import uuid
                evidence = evidence.with_name(iid + "-retry-" + uuid.uuid4().hex[:8])
            qualify(task, evidence, plan.get("runs", 5), docker)
            receipt = evidence / "qualification.json"
        return (str(task), str(receipt))

    completed, errors = [], []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(process, path): path for path in manifests}
        for future in as_completed(futures):
            try:
                completed.append(future.result())
            except Exception as exc:
                errors.append({"manifest": str(futures[future]), "error": str(exc)})
            write_json(workspace / "batch-status.json", {"qualified": completed, "errors": errors})
    require(not errors, f"{len(errors)} task(s) failed qualification; see {workspace / 'batch-status.json'}")
    completed.sort()
    write_json(workspace / "batch.json", {"tasks": [{"task": t, "qualification": r} for t, r in completed]})
    return publish(completed, exclusions_path, resolve(plan["release"]))
