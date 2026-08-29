from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HIGH_RISK_SKILLS = {
    "openhue",
    "xurl",
    "airtable",
    "github-pr-workflow",
    "github-repo-management",
}


def load_verified_episode_records(
    db_path: str | Path,
    *,
    project_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read bounded verified execution episodes through a query-only connection."""
    if not str(project_id or "").strip():
        raise ValueError("project_id is required for execution-episode reports")
    path = Path(db_path).expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        available = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        records: list[dict[str, Any]] = []
        bounded_limit = max(1, min(int(limit), 1000))
        for table in ("working_memory", "episodic_memory"):
            if table not in available:
                continue
            rows = connection.execute(
                f"SELECT id, timestamp, metadata_json FROM {table} "
                "WHERE source = ? ORDER BY timestamp DESC LIMIT ?",
                ("execution_bridge", bounded_limit),
            ).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    metadata.get("kind") != "execution_episode"
                    or metadata.get("verified") is not True
                ):
                    continue
                if metadata.get("project_id") != project_id:
                    continue
                records.append(
                    {
                        "memory_id": row["id"],
                        "timestamp": row["timestamp"],
                        "store": table,
                        "metadata": metadata,
                    }
                )
        records.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)
        return records[:bounded_limit]
    finally:
        connection.close()


def build_skill_report(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        metadata = record.get("metadata") or {}
        if metadata.get("kind") != "execution_episode" or metadata.get("verified") is not True:
            continue
        for skill in metadata.get("skills_used") or []:
            grouped[str(skill)].append(record)

    skills: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    for skill in sorted(grouped):
        evidence = grouped[skill]
        independent = {
            (
                str((row.get("metadata") or {}).get("session_id") or ""),
                str((row.get("metadata") or {}).get("fingerprint") or row.get("memory_id") or ""),
            )
            for row in evidence
        }
        required = 3 if skill in HIGH_RISK_SKILLS else 2
        evidence_ids = sorted(str(row.get("memory_id") or "") for row in evidence)
        versions = sorted(
            {
                str(
                    ((row.get("metadata") or {}).get("skill_versions") or {}).get(skill)
                    or "unknown"
                )
                for row in evidence
            }
        )
        skills.append(
            {
                "skill": skill,
                "evidence_count": len(independent),
                "required_evidence": required,
                "evidence_ids": evidence_ids,
                "skill_versions": versions,
            }
        )
        if len(independent) >= required:
            proposals.append(
                {
                    "skill": skill,
                    "status": "eligible_for_staged_diff",
                    "evidence_ids": evidence_ids,
                    "skill_versions": versions,
                    "required_evidence": required,
                }
            )
    return {"skills": skills, "proposals": proposals}


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Mnemosyne Skill Evidence Report",
        "",
        "> Report-only evidence. Human approval required before any skill change.",
        "",
    ]
    skills = report.get("skills") or []
    if not skills:
        lines.append("No verified skill evidence was found.")
        return "\n".join(lines) + "\n"
    for item in skills:
        lines.extend(
            [
                f"## {item['skill']}",
                f"- Independent verified episodes: {item['evidence_count']} / {item['required_evidence']}",
                f"- Skill versions: {', '.join(item['skill_versions'])}",
                f"- Evidence IDs: {', '.join(item['evidence_ids'])}",
                "- Status: "
                + (
                    "eligible for a separately reviewed staged diff"
                    if item["evidence_count"] >= item["required_evidence"]
                    else "insufficient evidence; no patch"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def write_skill_report(
    records: Iterable[dict[str, Any]],
    *,
    output_dir: str | Path,
    report_name: str,
) -> dict[str, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", report_name):
        raise ValueError("report_name must be a simple filename stem")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = build_skill_report(records)
    json_path = directory / f"{report_name}.json"
    markdown_path = directory / f"{report_name}.md"
    if json_path.parent != directory or markdown_path.parent != directory:
        raise ValueError("report_name escapes output directory")

    def atomic_write(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=directory
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    atomic_write(json_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    atomic_write(markdown_path, render_markdown_report(report))
    return {"json": json_path, "markdown": markdown_path}
