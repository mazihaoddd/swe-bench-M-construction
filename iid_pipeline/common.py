from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from datetime import datetime, timezone


class PipelineError(Exception):
    """An actionable input, environment or qualification failure."""


def require(condition, message):
    if not condition:
        raise PipelineError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def write_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_hash(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def tree_hash(root):
    root = Path(root).resolve()
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Symlinks not allowed in task package: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_hash(path)
    return canonical_hash(files)


def relative_path(value):
    require(isinstance(value, str) and value and "\\" not in value and "\x00" not in value,
            f"Invalid relative POSIX path: {value!r}")
    path = PurePosixPath(value)
    require(not path.is_absolute() and ".." not in path.parts and ":" not in value,
            f"Path escapes task: {value!r}")
    require(path.parts and path.parts[0] != ".git", f"Protected path: {value!r}")
    return path.as_posix()


def inside(root, name):
    root = Path(root).resolve()
    path = (root / relative_path(name)).resolve()
    require(path.is_relative_to(root), f"Path escapes root: {name}")
    return path


def utc(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(dt.tzinfo is not None, "Timestamps must include a timezone")
    return dt.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc).isoformat()


def run(argv, *, cwd=None, timeout=3600, input=None):
    try:
        result = subprocess.run([str(a) for a in argv], cwd=cwd, input=input,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PipelineError(f"Cannot run {argv[0]}: {exc}") from exc
    if result.returncode:
        raise PipelineError(f"Command failed ({result.returncode}): {argv[0]}\n"
                            + result.stderr.decode("utf-8", "replace")[-4000:])
    return result.stdout


def load_records(path):
    path = Path(path)
    if path.suffix == ".jsonl":
        # JSON strings may contain literal Unicode line/paragraph separators.
        # JSONL records are separated by LF, not every str.splitlines delimiter.
        return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    data = read_json(path)
    return data if isinstance(data, list) else data["records"]
