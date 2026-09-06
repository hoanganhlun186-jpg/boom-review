"""Transactional semantic-version bump for release builds."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION_FILE = ROOT / "version.txt"
STAGED_FILE = ROOT / ".build_version.txt"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_version(path: Path = VERSION_FILE) -> str:
    try:
        value = path.read_text(encoding="utf-8-sig").strip().splitlines()[0].strip()
    except Exception:
        value = "2.0.0"
    if not VERSION_RE.fullmatch(value):
        raise ValueError(f"Version khong hop le trong {path}: {value!r}")
    return value


def next_patch(version: str) -> str:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise ValueError(f"Version khong hop le: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


def prepare() -> str:
    version = next_patch(read_version())
    STAGED_FILE.write_text(version + "\n", encoding="utf-8")
    return version


def commit() -> str:
    version = read_version(STAGED_FILE)
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    STAGED_FILE.unlink(missing_ok=True)
    return version


def abort() -> None:
    STAGED_FILE.unlink(missing_ok=True)


def main() -> int:
    command = (sys.argv[1] if len(sys.argv) > 1 else "current").lower()
    if command == "current":
        print(read_version())
    elif command == "prepare":
        print(prepare())
    elif command == "commit":
        print(commit())
    elif command in {"abort", "rollback"}:
        abort()
    else:
        print("Usage: build_version.py current|prepare|commit|abort", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
