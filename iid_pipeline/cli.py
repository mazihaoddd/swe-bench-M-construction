from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .common import PipelineError, load_records, read_json, require, write_json, now


def pairs(path):
    """Resolve task/receipt paths relative to a batch file, never the shell cwd."""
    path = Path(path).resolve()
    return [(str((path.parent / row["task"]).resolve()), str((path.parent / row["qualification"]).resolve()))
            for row in read_json(path)["tasks"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description="SWE-bench Multimodal IID production pipeline")
    commands = parser.add_subparsers(dest="stage", required=True)
    collect = commands.add_parser("collect", help="Collect real merged PRs and reverse-linked issues")
    collect.add_argument("--output", required=True, type=Path)
    collect.add_argument("--cache", required=True, type=Path)
    collect.add_argument("--cutoff", required=True, help="ISO timestamp with timezone; freezes this collection batch")
    collect.add_argument("--repo", action="append")
    collect.add_argument("--max-prs", type=int)
    prepare = commands.add_parser("prepare", help="Archive base source/images and create an UNAPPROVED authoring draft")
    prepare.add_argument("--candidates", required=True, type=Path)
    prepare.add_argument("--instance", required=True)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--cache", required=True, type=Path)
    prepare.add_argument("--local-repo", type=Path)
    review = commands.add_parser("review", help="Record an explicit independent review of an authored task")
    review.add_argument("--manifest", required=True, type=Path)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--check", action="append", required=True, help="Repeat for each reviewed checklist item; see src/README.md")
    build = commands.add_parser("build", help="Validate reviewed inputs and create a STAGING task directory")
    build.add_argument("--manifest", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("check", help="Check required directory structure, TOML, assets and judge contract")
    check.add_argument("task", type=Path)
    qualify = commands.add_parser("qualify", help="Build Docker image, verify offline baseline/base/gold and negative patches")
    qualify.add_argument("task", type=Path)
    qualify.add_argument("--output", required=True, type=Path)
    qualify.add_argument("--runs", type=int, default=5)
    qualify.add_argument("--docker", default="docker")
    publish = commands.add_parser("publish", help="Release ONLY execution-qualified, reviewed and deduplicated tasks")
    publish.add_argument("--batch", required=True, type=Path)
    publish.add_argument("--exclusions", required=True, type=Path)
    publish.add_argument("--output", required=True, type=Path)
    index = commands.add_parser("index-exclusions", help="Index an official JSON/JSONL reference dataset for deduplication")
    index.add_argument("--input", required=True, type=Path)
    index.add_argument("--output", required=True, type=Path)
    batch = commands.add_parser("run", help="Build, qualify and release a batch of independently reviewed manifests")
    batch.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.stage == "collect":
            from .github import collect
            require(args.max_prs is None or args.max_prs > 0, "max-prs must be positive")
            result = collect(args.output, args.cache, args.cutoff, args.repo, args.max_prs)
        elif args.stage == "prepare":
            from .prepare import prepare
            candidates = [x for x in load_records(args.candidates) if x["instance_id"] == args.instance]
            require(len(candidates) == 1, "Expected exactly one matching candidate")
            result = str(prepare(candidates[0], args.output, args.cache, args.local_repo))
        elif args.stage == "review":
            from .package import REVIEW_CHECKS
            from .prepare import review_subject
            require(REVIEW_CHECKS == set(args.check), "Explicitly review each item: " + ", ".join(sorted(REVIEW_CHECKS)))
            require(args.reviewer.strip(), "Reviewer identity required")
            value = read_json(args.manifest)
            value["review"] = {"approved": True, "reviewer": args.reviewer, "reviewed_at": now(),
                               "checks": sorted(REVIEW_CHECKS), "subject_sha256": review_subject(args.manifest)}
            write_json(args.manifest, value)
            result = {"review_subject": value["review"]["subject_sha256"], "status": "reviewed, not yet execution-qualified"}
        elif args.stage == "build":
            from .package import build
            result = str(build(args.manifest, args.output))
        elif args.stage == "check":
            from .package import validate_bundle
            result = validate_bundle(args.task)
        elif args.stage == "qualify":
            from .verify import qualify, Docker
            require(args.runs >= 2, "At least two repetitions per state are required")
            receipt = qualify(args.task, args.output, args.runs, Docker(args.docker))
            result = {"status": receipt["status"], "receipt": str(args.output / "qualification.json")}
        elif args.stage == "index-exclusions":
            from .batch import index_exclusions
            result = index_exclusions(args.input, args.output)
        elif args.stage == "run":
            from .batch import produce
            result = str(produce(args.plan))
        else:
            from .verify import publish
            result = str(publish(pairs(args.batch), args.exclusions, args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, KeyError, ValueError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
