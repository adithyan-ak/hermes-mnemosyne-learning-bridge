from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

_SCP_REMOTE = re.compile(r"^(?:[^@/:]+@)?(?P<host>\[[^\]]+\]|[^:/]+):(?P<path>.*)$")
_DEFAULT_PORTS = {"http": 80, "https": 443, "ssh": 22, "git": 9418}


def _strip_git_suffix(path: str) -> str:
    return path[:-4] if path.endswith(".git") else path


def _canonical_remote(remote: str) -> str:
    if "://" in remote:
        parsed = urlsplit(remote)
        if parsed.scheme == "file":
            return f"file://{_strip_git_suffix(parsed.path)}"
        if parsed.hostname:
            host = parsed.hostname.lower()
            port = parsed.port
            authority = (
                f"{host}:{port}"
                if port is not None and port != _DEFAULT_PORTS.get(parsed.scheme.lower())
                else host
            )
            path = _strip_git_suffix(parsed.path.lstrip("/"))
            suffix = f"?{parsed.query}" if parsed.query else ""
            if parsed.fragment:
                suffix += f"#{parsed.fragment}"
            return f"{authority}/{path}{suffix}"
    scp = _SCP_REMOTE.fullmatch(remote)
    if scp:
        host = scp.group("host").lower()
        path = _strip_git_suffix(scp.group("path"))
        return f"{host}:{path}" if path.startswith("/") else f"{host}/{path}"
    return f"local:{_strip_git_suffix(remote)}"


def _remote_identity(remote: str) -> str:
    value = remote or ""
    if not value:
        return ""
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("git remote contains control characters")
    if value != value.strip():
        raise ValueError("git remote contains boundary whitespace")
    canonical = _canonical_remote(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"remote:sha256:{digest}"


def normalize_project_id(*, workdir: Path, git_remote: str | None = None) -> str:
    """Return a stable non-secret project namespace."""
    remote_id = _remote_identity(git_remote or "")
    if remote_id:
        return remote_id
    resolved = Path(workdir).expanduser().resolve(strict=False)
    slug = re.sub(r"[^a-z0-9._-]+", "-", resolved.name.lower()).strip("-") or "root"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"workspace:{slug}:{digest}"


def normalize_project_reference(value: str) -> str:
    """Normalize an explicit remote-like project reference or reject it."""
    return _remote_identity(value)
