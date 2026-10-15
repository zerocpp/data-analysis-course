#!/usr/bin/env python3
"""Print the CodeSetArena version — the single source of truth for shell tooling.

Used by ``scripts/build_student_docker_release.sh`` and any CI step that needs
the version without importing the whole package.

Usage::

    python scripts/docker/read_version.py            # prints e.g. 9.0.0
    python scripts/docker/read_version.py --tag      # prints e.g. v9.0.0

Reads ``VERSION`` at the repository root.  Exits non-zero (never guesses) if
the file is missing, empty, or does not look like a version.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parent.parent.parent  # scripts/docker -> scripts -> repo root
VERSION_FILE = _REPO_ROOT / "VERSION"

# PEP 440-ish: digits with optional .post/.dev/.rc suffixes. Keep it permissive
# but reject whitespace/garbage so a typo cannot silently become a tag.
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:[-.a-zA-Z0-9+]*)?$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the CodeSetArena version.")
    parser.add_argument(
        "--tag", action="store_true",
        help="prefix with 'v' for Docker image tags / release directory names",
    )
    parser.add_argument("--version-file", default=str(VERSION_FILE),
                        help="override VERSION file path (testing)")
    args = parser.parse_args(argv)

    path = Path(args.version_file)
    if not path.is_file():
        print(f"[read_version] VERSION file not found: {path}", file=sys.stderr)
        return 1
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        print(f"[read_version] VERSION file is empty: {path}", file=sys.stderr)
        return 1
    if not _VERSION_RE.match(value):
        print(f"[read_version] malformed version {value!r} in {path}", file=sys.stderr)
        return 1

    print(f"v{value}" if args.tag else value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
