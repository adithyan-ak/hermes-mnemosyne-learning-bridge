from __future__ import annotations

from enum import StrEnum
from typing import Any


class Decision(StrEnum):
    READ = "read"
    DIRECT = "direct"
    STAGE = "stage"
    BLOCK = "block"
    UNKNOWN = "unknown"


_READ_TOOLS = {
    "mnemosyne_recall",
    "mnemosyne_shared_recall",
    "mnemosyne_shared_stats",
    "mnemosyne_stats",
    "mnemosyne_get",
    "mnemosyne_triple_query",
    "mnemosyne_recall_canonical",
    "mnemosyne_model_card",
    "mnemosyne_model_refresh",
    "mnemosyne_scratchpad_read",
    "mnemosyne_graph_query",
    "mnemosyne_sync_status",
    "mnemosyne_persona_list",
}

_STAGE_TOOLS = {
    "mnemosyne_update",
    "mnemosyne_forget",
}

_BLOCK_TOOLS = {
    # No reviewed read-back adapter exists for these mutation families.
    "mnemosyne_scratchpad_write",
    "mnemosyne_scratchpad_clear",
    "mnemosyne_export",
    "mnemosyne_import",
    "mnemosyne_task_progress",
    "mnemosyne_graph_link",
    "mnemosyne_sync_push",
    "mnemosyne_sync_pull",
    "mnemosyne_persona_promote",
    "mnemosyne_persona_demote",
    "mnemosyne_persona_reinforce",
    # These mutation families remain hidden until every writable field and
    # state transition has an exact deterministic read-back adapter.
    "mnemosyne_shared_remember",
    "mnemosyne_shared_forget",
    "mnemosyne_invalidate",
    "mnemosyne_validate",
    "mnemosyne_triple_add",
    "mnemosyne_triple_end",
    "mnemosyne_remember_canonical",
    "mnemosyne_forget_canonical",
    # The upstream apply path only understands remember payloads and therefore
    # cannot safely approve the complete mutation matrix.
    "mnemosyne_apply_pending",
}

_VISIBLE_TOOLS = (
    _READ_TOOLS
    | _STAGE_TOOLS
    | {
        "mnemosyne_remember",
        "mnemosyne_batch",
        "mnemosyne_sleep",
        "mnemosyne_diagnose",
        "mnemosyne_recall_diagnostics",
    }
)


def is_tool_visible(tool_name: str) -> bool:
    """Return whether a reviewed tool should be advertised to the model."""
    return tool_name in _VISIBLE_TOOLS


def classify_tool(tool_name: str, args: dict[str, Any]) -> Decision:
    if tool_name in _READ_TOOLS:
        return Decision.READ
    if tool_name == "mnemosyne_remember":
        is_direct_user_statement = (
            args.get("scope") in {"session", "global"}
            and args.get("source") == "user"
            and args.get("veracity") == "stated"
            and not bool(args.get("extract"))
            and not bool(args.get("extract_entities"))
        )
        return Decision.DIRECT if is_direct_user_statement else Decision.BLOCK
    if tool_name in _STAGE_TOOLS:
        return Decision.STAGE
    if tool_name == "mnemosyne_batch":
        return Decision.READ if bool(args.get("dry_run")) else Decision.BLOCK
    if tool_name == "mnemosyne_sleep":
        return Decision.READ if bool(args.get("dry_run")) else Decision.BLOCK
    if tool_name == "mnemosyne_diagnose":
        repairs = bool(args.get("repair_vec_working"))
        return Decision.BLOCK if repairs and not bool(args.get("dry_run")) else Decision.READ
    if tool_name == "mnemosyne_recall_diagnostics":
        return Decision.BLOCK if bool(args.get("reset")) else Decision.READ
    if tool_name in _BLOCK_TOOLS:
        return Decision.BLOCK
    return Decision.UNKNOWN
