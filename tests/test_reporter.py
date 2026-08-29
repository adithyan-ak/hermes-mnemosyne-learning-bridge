import json
import sqlite3
from pathlib import Path

from mnemosyne_learning_bridge.reporter import (
    build_skill_report,
    load_verified_episode_records,
    render_markdown_report,
    write_skill_report,
)


def _episode(memory_id: str, session_id: str) -> dict:
    return {
        "memory_id": memory_id,
        "metadata": {
            "kind": "execution_episode",
            "verified": True,
            "outcome": "success",
            "session_id": session_id,
            "fingerprint": f"fingerprint-{memory_id}",
            "skills_used": ["test-driven-development"],
            "skill_versions": {"test-driven-development": "1.1.0"},
        },
    }


def test_normal_skill_requires_two_independent_verified_episodes() -> None:
    one = build_skill_report([_episode("mem-1", "session-1")])
    two = build_skill_report([_episode("mem-1", "session-1"), _episode("mem-2", "session-2")])

    assert one["proposals"] == []
    assert one["skills"][0]["evidence_count"] == 1
    assert two["proposals"] == [
        {
            "skill": "test-driven-development",
            "status": "eligible_for_staged_diff",
            "evidence_ids": ["mem-1", "mem-2"],
            "skill_versions": ["1.1.0"],
            "required_evidence": 2,
        }
    ]


def test_loader_reads_only_verified_project_episodes_from_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE working_memory (id TEXT, source TEXT, timestamp TEXT, metadata_json TEXT)"
    )
    rows = [
        (
            "keep",
            "execution_bridge",
            "2026-08-26",
            json.dumps(
                {
                    "kind": "execution_episode",
                    "verified": True,
                    "project_id": "project-a",
                    "skills_used": ["test-driven-development"],
                }
            ),
        ),
        (
            "wrong-project",
            "execution_bridge",
            "2026-08-26",
            json.dumps(
                {
                    "kind": "execution_episode",
                    "verified": True,
                    "project_id": "project-b",
                    "skills_used": ["test-driven-development"],
                }
            ),
        ),
        (
            "unverified",
            "execution_bridge",
            "2026-08-26",
            json.dumps(
                {
                    "kind": "execution_episode",
                    "verified": False,
                    "project_id": "project-a",
                    "skills_used": ["test-driven-development"],
                }
            ),
        ),
    ]
    connection.executemany("INSERT INTO working_memory VALUES (?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()

    records = load_verified_episode_records(db_path, project_id="project-a", limit=10)

    assert [record["memory_id"] for record in records] == ["keep"]


def test_markdown_report_is_evidence_only_and_requires_human_review() -> None:
    report = build_skill_report([_episode("mem-1", "session-1"), _episode("mem-2", "session-2")])

    markdown = render_markdown_report(report)

    assert "test-driven-development" in markdown
    assert "mem-1" in markdown and "mem-2" in markdown
    assert "Human approval required" in markdown
    assert "skill_manage" not in markdown


def test_write_skill_report_creates_only_manifest_and_markdown(tmp_path: Path) -> None:
    paths = write_skill_report(
        [_episode("mem-1", "session-1")],
        output_dir=tmp_path,
        report_name="canary",
    )

    assert sorted(path.name for path in tmp_path.iterdir()) == ["canary.json", "canary.md"]
    assert json.loads(paths["json"].read_text())["proposals"] == []
    assert "insufficient evidence" in paths["markdown"].read_text()


def test_write_skill_report_replaces_symlink_without_following_it(tmp_path: Path) -> None:
    target = tmp_path / "protected.txt"
    target.write_text("do not overwrite")
    output = tmp_path / "reports"
    output.mkdir()
    (output / "canary.json").symlink_to(target)

    paths = write_skill_report([], output_dir=output, report_name="canary")

    assert target.read_text() == "do not overwrite"
    assert paths["json"].is_file()
    assert not paths["json"].is_symlink()


def test_loader_requires_project_id(tmp_path: Path) -> None:
    import pytest

    db_path = tmp_path / "memory.db"
    db_path.touch()
    with pytest.raises(ValueError, match="project_id"):
        load_verified_episode_records(db_path, project_id="")


def test_write_skill_report_rejects_path_traversal(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="report_name"):
        write_skill_report([], output_dir=tmp_path / "reports", report_name="../escape")

    assert not (tmp_path / "escape.json").exists()
