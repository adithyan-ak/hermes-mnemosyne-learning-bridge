from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _load_sqlite_extensions(connection: sqlite3.Connection) -> None:
    try:
        import sqlite_vec

        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
    except (ImportError, sqlite3.Error):
        return


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _online_backup(source_path: Path, destination_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(destination_path)
    try:
        _load_sqlite_extensions(source)
        _load_sqlite_extensions(destination)
        source.backup(destination)
        destination.commit()
    finally:
        destination.close()
        source.close()


def _verify_binary_backup(backup_path: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="mnemosyne-restore-test-") as directory:
        restored = Path(directory) / "restored.db"
        with gzip.open(backup_path, "rb") as source, restored.open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
        health = database_health(restored)
        return health["quick_check"] == "ok" and health["integrity_check"] == "ok"


def database_health(db_path: str | Path) -> dict[str, Any]:
    path = Path(db_path).expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "mode": oct(path.stat().st_mode & 0o777),
        "quick_check": quick,
        "integrity_check": integrity,
        "table_count": tables,
    }


def run_maintenance(
    *,
    db_path: str | Path,
    backup_dir: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    health = database_health(db_path)
    backup_root = Path(backup_dir).expanduser().resolve()
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "database": health,
        "backup": {
            "would_create": True,
            "directory": str(backup_root),
        },
    }
    if not apply:
        return report
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(backup_root, 0o700)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    source_path = Path(db_path).expanduser().resolve(strict=True)
    backup_id = f"{timestamp}-{uuid.uuid4().hex[:8]}"
    raw_path = backup_root / f"mnemosyne-{backup_id}.db.tmp"
    gzip_path = backup_root / f"mnemosyne-{backup_id}.db.gz"
    _online_backup(source_path, raw_path)
    raw_sha256 = _sha256(raw_path)
    temporary_gzip = backup_root / f".{gzip_path.name}.tmp"
    try:
        with (
            raw_path.open("rb") as source,
            gzip.open(temporary_gzip, "wb", compresslevel=9) as destination,
        ):
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
        os.replace(temporary_gzip, gzip_path)
        os.chmod(gzip_path, 0o600)
    finally:
        raw_path.unlink(missing_ok=True)
        temporary_gzip.unlink(missing_ok=True)
    verified = _verify_binary_backup(gzip_path)
    report["backup"] = {
        "would_create": True,
        "path": str(gzip_path),
        "sha256": _sha256(gzip_path),
        "database_sha256": raw_sha256,
        "verified": verified,
        "restore_test": "temporary_restore_deleted_after_verification",
    }
    return report
