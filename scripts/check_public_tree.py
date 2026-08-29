#!/usr/bin/env python3
"""Fail when a source tree contains deployment state or likely private artifacts."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "build", "dist"}
BANNED_NAMES = {
    ".env",
    "config.yaml",
    "SOUL.md",
    "USER.md",
    "MEMORY.md",
    "deployment-manifest.json",
}
BANNED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".gz", ".pem", ".key", ".whl"}
PRIVATE_PATH = re.compile(
    re.escape("/" + "home/") + r"[^/\s]+|/Users/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+"
)
TOKEN_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
)
SQLITE_MAGIC = b"SQLite format 3\x00"


def candidate_files() -> list[Path]:
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            text=True,
        )
        return [ROOT / line for line in output.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and not any(part in SKIP_DIRS for part in path.parts)
        ]


def main() -> int:
    findings: list[str] = []
    for path in candidate_files():
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.name in BANNED_NAMES:
            findings.append(f"banned file name: {relative}")
        if path.suffix.lower() in BANNED_SUFFIXES:
            findings.append(f"banned file type: {relative}")
        data = path.read_bytes()
        if data.startswith(SQLITE_MAGIC):
            findings.append(f"SQLite database content: {relative}")
        if len(data) > 2_000_000:
            findings.append(f"unexpected file larger than 2 MB: {relative}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if path.resolve() != Path(__file__).resolve() and PRIVATE_PATH.search(text):
            findings.append(f"absolute user path: {relative}")
        if any(pattern.search(text) for pattern in TOKEN_PATTERNS):
            findings.append(f"credential-like content: {relative}")

    if findings:
        print("Public-tree check failed:")
        for finding in sorted(set(findings)):
            print(f"- {finding}")
        return 1
    print("Public-tree check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
