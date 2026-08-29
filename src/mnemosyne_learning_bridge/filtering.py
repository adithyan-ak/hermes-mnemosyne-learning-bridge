from __future__ import annotations

import json
import re
from typing import Any

_PROJECT_MARKER = re.compile(r"\[PROJECT:([^\]]+)\]")


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("metadata") or {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _allowed(item: dict[str, Any], project_id: str) -> bool:
    meta = _metadata(item)
    content = str(item.get("content") or "")
    marker_projects = _PROJECT_MARKER.findall(content)
    is_episode = meta.get("kind") == "execution_episode" or bool(marker_projects)
    if not is_episode:
        return True
    metadata_project = str(meta.get("project_id") or "")
    identity_sources = {value for value in [metadata_project, *marker_projects] if value}
    if len(identity_sources) != 1:
        return False
    stored_project = next(iter(identity_sources))
    return bool(stored_project) and stored_project == project_id


def filter_recall_payload(payload: str | dict[str, Any] | list[Any], project_id: str) -> str:
    """Remove execution episodes outside the active project namespace at any depth."""
    parsed = json.loads(payload) if isinstance(payload, str) else payload
    drop = object()

    def filter_value(value: Any) -> Any:
        if isinstance(value, list):
            kept: list[Any] = []
            for item in value:
                filtered_item = filter_value(item)
                if filtered_item is not drop:
                    kept.append(filtered_item)
            return kept
        if isinstance(value, dict):
            if not _allowed(value, project_id):
                return drop
            filtered: dict[str, Any] = {}
            for key, item in value.items():
                filtered_item = filter_value(item)
                if filtered_item is drop:
                    if key == "memory":
                        filtered[key] = None
                    continue
                filtered[key] = filtered_item
            return filtered
        return value

    filtered = filter_value(parsed)
    return json.dumps(None if filtered is drop else filtered, ensure_ascii=False)


def filter_prefetch_text(text: str, project_id: str) -> str:
    """Drop complete formatted prefetch blocks with non-unanimous project identity."""
    blocks: list[list[str]] = []
    current: list[str] = []
    base_indent = 0

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    def indentation_columns(line: str) -> int:
        stripped = line.lstrip()
        prefix = line[: len(line) - len(stripped)]
        return len(prefix.expandtabs(8))

    for line in (text or "").splitlines():
        if line == "## Mnemosyne Context":
            flush()
            blocks.append([line])
            continue
        indent = indentation_columns(line)
        if not current:
            current = [line]
            base_indent = indent
        elif line.strip() and indent <= base_indent:
            flush()
            current = [line]
            base_indent = indent
        else:
            current.append(line)
    flush()

    kept: list[str] = []
    for block in blocks:
        marker_projects = set(_PROJECT_MARKER.findall("\n".join(block)))
        if marker_projects and marker_projects != {project_id}:
            continue
        kept.extend(block)
    if len(kept) == 1 and kept[0].strip() == "## Mnemosyne Context":
        return ""
    return "\n".join(kept)
