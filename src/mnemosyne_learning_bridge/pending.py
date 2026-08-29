from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from .evidence import contains_secret

PROVIDER_NAME = "mnemosyne-learning-bridge"
PENDING_TTL_SECONDS = 7 * 24 * 60 * 60


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_material(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": record.get("schema_version"),
        "subsystem": record.get("subsystem"),
        "provider": record.get("provider"),
        "tool": record.get("tool"),
        "session_id": record.get("session_id"),
        "project_id": record.get("project_id"),
        "payload": record.get("payload"),
        "summary": record.get("summary"),
        "created_at": record.get("created_at"),
    }


def pending_directory(hermes_home: str | Path) -> Path:
    root = Path(hermes_home).expanduser().resolve() / "pending" / "memory"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    cutoff = time.time() - PENDING_TTL_SECONDS
    for claimed in root.glob(".*.claimed"):
        try:
            if claimed.stat().st_mtime < cutoff:
                claimed.unlink()
        except OSError:
            continue
    return root


def stage_mutation(
    *,
    hermes_home: str | Path,
    tool: str,
    payload: dict[str, Any],
    session_id: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    if contains_secret(payload):
        return {"status": "rejected", "error": "payload failed secret-safety gate"}
    canonical_payload = _canonical_json(payload)
    record = {
        "schema_version": 1,
        "subsystem": "memory",
        "provider": PROVIDER_NAME,
        "tool": tool,
        "session_id": str(session_id or ""),
        "project_id": str(project_id or ""),
        "payload": payload,
        "payload_sha256": hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        "summary": f"Staged {tool} mutation",
        "created_at": time.time(),
    }
    record_sha256 = hashlib.sha256(
        _canonical_json(_record_material(record)).encode("utf-8")
    ).hexdigest()
    pending_id = f"{record_sha256[:32]}{uuid.uuid4().hex[:16]}"
    record["id"] = pending_id
    record["record_sha256"] = record_sha256
    directory = pending_directory(hermes_home)
    target = directory / f"{pending_id}.json"
    temporary = directory / f".{pending_id}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "staged",
        "pending_id": pending_id,
        "tool": tool,
        "review": {
            "tool": tool,
            "payload": payload,
            "payload_sha256": record["payload_sha256"],
        },
        "message": f"Mutation staged. Apply only after the user explicitly says APPLY {pending_id}.",
    }


def load_pending(
    *,
    hermes_home: str | Path,
    pending_id: str,
    expected_session_id: str | None = None,
    expected_project_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    identifier = str(pending_id or "").strip()
    if (
        not identifier
        or len(identifier) > 64
        or not identifier.isascii()
        or not identifier.isalnum()
    ):
        raise ValueError("invalid pending id")
    directory = pending_directory(hermes_home)
    path = (directory / f"{identifier}.json").resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ValueError("pending record not found")
    file_stat = path.stat()
    if stat.S_IMODE(file_stat.st_mode) != 0o600 or file_stat.st_uid != os.getuid():
        raise ValueError("pending record permissions or ownership are invalid")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (
        record.get("schema_version") != 1
        or record.get("subsystem") != "memory"
        or record.get("provider") != PROVIDER_NAME
        or record.get("id") != identifier
        or not str(record.get("tool") or "").strip()
    ):
        raise ValueError("pending record identity mismatch")
    created_at = float(record.get("created_at") or 0.0)
    age = time.time() - created_at
    if age < -300:
        path.unlink(missing_ok=True)
        raise ValueError("pending record timestamp is in the future")
    if created_at <= 0 or age > PENDING_TTL_SECONDS:
        path.unlink(missing_ok=True)
        raise ValueError("pending record expired")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise TypeError("pending payload is invalid")
    if contains_secret(payload):
        raise ValueError("pending payload failed secret-safety gate")
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    if digest != record.get("payload_sha256"):
        raise ValueError("pending payload integrity check failed")
    record_digest = hashlib.sha256(
        _canonical_json(_record_material(record)).encode("utf-8")
    ).hexdigest()
    if record_digest != record.get("record_sha256") or not identifier.startswith(
        record_digest[:32]
    ):
        raise ValueError("pending record integrity check failed")
    if expected_session_id is not None and record.get("session_id") != expected_session_id:
        raise ValueError("pending record belongs to a different session")
    if expected_project_id is not None and record.get("project_id") != expected_project_id:
        raise ValueError("pending record belongs to a different project")
    return record, path


def claim_pending(
    *,
    hermes_home: str | Path,
    pending_id: str,
    expected_session_id: str | None = None,
    expected_project_id: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Atomically make a validated pending record unavailable for replay."""
    record, path = load_pending(
        hermes_home=hermes_home,
        pending_id=pending_id,
        expected_session_id=expected_session_id,
        expected_project_id=expected_project_id,
    )
    claimed = path.with_name(f".{path.stem}.claimed")
    try:
        os.link(path, claimed)
    except FileExistsError as exc:
        raise ValueError("pending record already claimed") from exc
    except FileNotFoundError as exc:
        raise ValueError("pending record not found") from exc
    try:
        path.unlink()
        claimed_record = json.loads(claimed.read_text(encoding="utf-8"))
        if _canonical_json(claimed_record) != _canonical_json(record):
            raise ValueError("pending record changed during claim")
        return record, claimed
    except Exception:
        claimed.unlink(missing_ok=True)
        raise


def list_pending(
    *,
    hermes_home: str | Path,
    expected_session_id: str | None = None,
    expected_project_id: str | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    directory = pending_directory(hermes_home)
    for path in sorted(directory.glob("*.json")):
        try:
            record, _ = load_pending(
                hermes_home=hermes_home,
                pending_id=path.stem,
                expected_session_id=expected_session_id,
                expected_project_id=expected_project_id,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        records.append(
            {
                "id": record.get("id"),
                "tool": record.get("tool"),
                "summary": record.get("summary"),
                "created_at": record.get("created_at"),
                "review": {
                    "tool": record.get("tool"),
                    "payload": record.get("payload"),
                    "payload_sha256": record.get("payload_sha256"),
                },
            }
        )
    return records
