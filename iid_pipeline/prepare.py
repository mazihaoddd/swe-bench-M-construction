"""Archive source and images and prepare a task-design draft for review."""
from __future__ import annotations

from html import unescape
import io
from pathlib import Path
import re
from urllib.request import Request, urlopen

from .common import (PipelineError, canonical_hash, file_hash, inside, read_json,
                     require, run, sha256, write_json, write_text)
from .policy import validate_source


def image_urls(body):
    markdown = re.findall(r"!\[[^\]]*\]\(<?(https://[^\s)>]+)>?(?:\s+[^)]*)?\)", body or "")
    html = re.findall(r"<img\b[^>]*\bsrc\s*=\s*['\"](https://[^'\"]+)['\"]", body or "", re.I)
    raw = re.findall(r"https://[^\s<>\"')]+\.(?:png|jpe?g|gif|webp)(?:\?[^\s<>\"')]*)?", body or "", re.I)
    return list(dict.fromkeys(unescape(x) for x in markdown + html + raw))


def reproduction_urls(body):
    return sorted(set(re.findall(
        r"https?://(?:codesandbox\.io|jsfiddle\.net|codepen\.io|stackblitz\.com|editor\.p5js\.org)/[^\s<>\"')]+",
        body or "", flags=re.I)))


def image_info(data):
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            require(image.width >= 16 and image.height >= 16, "Image is too small")
            require(image.width * image.height <= 40_000_000, "Image exceeds pixel limit")
            require(image.format in {"PNG", "JPEG", "GIF", "WEBP"}, "Unsupported image format")
            require(getattr(image, "n_frames", 1) == 1, "Animated media needs a reviewed static frame export")
            image.load()
            return {"width": image.width, "height": image.height, "format": image.format,
                    "sha256": sha256(data)}
    except ImportError as exc:
        raise PipelineError("Image verification requires Pillow: pip install Pillow") from exc


def download_image(url, target, limit=25 * 1024 * 1024):
    require(url.startswith("https://"), "Images must use HTTPS")
    with urlopen(Request(url, headers={"User-Agent": "iid-pipeline"}), timeout=60) as response:
        data = response.read(limit + 1)
    require(len(data) <= limit, "Image exceeds byte limit")
    info = image_info(data)
    target = Path(target).with_suffix({"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "WEBP": ".webp"}[info["format"]])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target, info


def prepare(candidate, output, cache, local_repo=None):
    iid = validate_source(candidate["source"])
    output = Path(output).resolve() / iid
    require(not output.exists(), f"Draft exists; use its manifest or a new workspace: {output}")
    output.mkdir(parents=True)
    source = dict(candidate["source"])
    repo_dir = Path(local_repo).resolve() if local_repo else Path(cache).resolve() / source["repo"].replace("/", "__")
    if not local_repo:
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        if not repo_dir.exists():
            run(["git", "clone", "--bare", "--filter=blob:none", f'https://github.com/{source["repo"]}.git', repo_dir])
        run(["git", "-C", repo_dir, "fetch", "origin", source["base_commit"], source["head_commit"]])
    # PR base branches can advance. The merge base is the actual diff base.
    source["base_commit"] = run(["git", "-C", repo_dir, "merge-base", source["base_commit"],
                                 source["head_commit"]]).decode().strip()
    (output / "base.tar").write_bytes(run(["git", "-C", repo_dir, "archive", "--format=tar", source["base_commit"]]))
    (output / "upstream.patch").write_bytes(run(["git", "-C", repo_dir, "diff", "--binary", "--full-index",
                                               source["base_commit"], source["head_commit"]]))
    changed = run(["git", "-C", repo_dir, "diff", "--name-only", "-z", source["base_commit"],
                   source["head_commit"]]).decode().split("\x00")
    write_json(output / "source.json", {**candidate, "source": source, "changed_paths": [x for x in changed if x],
                                      "archive_sha256": file_hash(output / "base.tar")})
    bodies = [f'# {x["title"]}\n\n{x.get("body") or ""}' for x in candidate["issues"]]
    body = "\n\n".join(bodies)
    images, failures = [], []
    for n, url in enumerate(image_urls(body), 1):
        try:
            path, info = download_image(url, output / "images" / f"issue-{n:02}")
        except Exception as exc:
            failures.append({"url": url, "error": str(exc)})
            continue
        repo_path = f"assets/{iid}/{path.name}"
        body = body.replace(url, "/testbed/" + repo_path)
        images.append({"file": path.relative_to(output).as_posix(), "source_url": url,
                       "repo_path": repo_path, **info, "role": "",
                       "visual_evidence": "", "information_gain": "", "without_image": "", "linked_test_ids": []})
    write_text(output / "instruction.md", body + "\n\n请在 `/testbed` 中修改代码，修复上述问题并保持既有行为。\n")
    manifest = {
        "schema_version": 1, "source": source, "instruction": "instruction.md", "source_archive": "base.tar",
        "gold_patch": "gold.patch", "test_patch": "test.patch", "images": images, "resources": [],
        "environment": {"base_image": "", "setup_script": "setup.sh", "cpus": 4, "memory_mb": 8192,
                        "storage_mb": 20480, "build_timeout_sec": 1800},
        "agent_timeout_sec": 7200, "verifier_timeout_sec": 3600,
        "tests": {"FAIL_TO_PASS": [], "PASS_TO_PASS": [], "command": [], "baseline_command": [],
                  "log_parser": {"kind": "junit", "report_path": ".iid-results/junit.xml"},
                  "test_timeout_sec": 3000, "protected_paths": [], "p2p_files": [],
                  "f2p_cases": {}, "fixture_sha256": {}},
        "negative_patches": [], "tags": {"framework": "", "renderer": "", "defect": "", "difficulty": ""},
        "review": {"approved": False, "reviewer": "", "reviewed_at": "", "checks": [], "subject_sha256": ""},
    }
    write_json(output / "manifest.json", manifest)
    write_json(output / "asset_failures.json", failures)
    write_json(output / "reproduction_requests.json", {"required_source_urls": reproduction_urls(body),
                 "instructions": "Export runnable code/dependencies, declare resources in manifest and replace online IDE links with local paths."})
    write_text(output / "design_instructions.md", DESIGN_INSTRUCTIONS)
    return output


DESIGN_INSTRUCTIONS = """Read source.json, the base snapshot, upstream.patch, and actual image files.
Produce a task manifest and real files, not a mock: split the upstream fix into production-only
gold.patch and new tests in test.patch, preserving Git binary diffs. Design additional F2P tests
where needed. Specify nonempty P2P ids for EXISTING tests, baseline_command running those tests
without test.patch, and command running every F2P/P2P. Use JUnit/Jest/Mocha JSON or an explicit
per-case reporter. Each f2p_cases[id] needs file, requirement and failure_regex matching the target
assertion's failure detail (not compilation/discovery errors). test.patch may touch only declared
test paths and must not modify production code or the P2P source files. Gold must remain a real
production fix from the upstream PR; record any divergence in the independent review.
Supply setup.sh that installs exact offline runtime dependencies at Docker build time and a
digest-pinned Debian/Ubuntu-compatible base_image; git/bash/python3 are installed by the builder.
Do not put solution/test patch or hidden fixtures in the agent image. The judge itself has no
network. Add at least one plausible incomplete/wrong negative patch. Supply all per-image visual
evidence, information gain, no-image ambiguity and linked F2P ids. Preserve original issue text;
Export every reproduction_requests.json URL into resources entries (file, repo_path under
assets/<instance_id>/, source_url, sha256, instructions). Include its required HTML/JS/CSS/data
dependencies in those files or the image. Replace IDE URLs in instruction with local paths.
do not leak the solution through the prompt. Document any post-fix edited issue context for review.
Do not mark review.approved or invent human approval. An independent reviewer must attest
source linkage, pre-fix context, scope, requirements, visual necessity, P2P preservation and dedup.
Save the completed manifest.json and referenced files in this draft directory.
"""


def review_subject(manifest_path):
    path = Path(manifest_path)
    data = read_json(path)
    data.pop("review", None)
    refs = [data["instruction"], data["source_archive"], data["gold_patch"], data["test_patch"],
            data["environment"]["setup_script"], *data.get("negative_patches", [])]
    refs += [x["file"] for x in data["images"]]
    refs += [x["file"] for x in data.get("resources", [])]
    if data['environment'].get('bootstrap_script'):
        refs.append(data['environment']['bootstrap_script'])
    return canonical_hash({"manifest": data, "files": {name: file_hash(inside(path.parent, name)) for name in refs}})
