import sqlite3
from pathlib import Path

from mnemosyne_learning_bridge.maintenance import run_maintenance


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE canary (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO canary(value) VALUES ('ok')")
    connection.commit()
    connection.close()


def test_maintenance_dry_run_checks_database_without_creating_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    _database(db_path)

    report = run_maintenance(db_path=db_path, backup_dir=backup_dir, apply=False)

    assert report["mode"] == "dry_run"
    assert report["database"]["quick_check"] == "ok"
    assert report["database"]["integrity_check"] == "ok"
    assert report["backup"]["would_create"] is True
    assert not backup_dir.exists()


def test_maintenance_apply_creates_restorable_binary_sqlite_backup(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    _database(db_path)

    report = run_maintenance(db_path=db_path, backup_dir=backup_dir, apply=True)

    backup_path = Path(report["backup"]["path"])
    assert backup_path.is_file()
    assert report["backup"]["verified"] is True
    assert report["backup"]["restore_test"] == "temporary_restore_deleted_after_verification"
    assert "restore_test_path" not in report["backup"]


def test_repeated_maintenance_backups_never_overwrite_same_second(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "memory.db"
    backup_dir = tmp_path / "backups"
    _database(db_path)
    monkeypatch.setattr(
        "mnemosyne_learning_bridge.maintenance.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(
                    lambda timezone: __import__("datetime").datetime(2026, 8, 27, tzinfo=timezone)
                )
            },
        ),
    )

    first = run_maintenance(db_path=db_path, backup_dir=backup_dir, apply=True)
    second = run_maintenance(db_path=db_path, backup_dir=backup_dir, apply=True)

    assert first["backup"]["path"] != second["backup"]["path"]
    assert Path(first["backup"]["path"]).is_file()
    assert Path(second["backup"]["path"]).is_file()
