from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Episode:
    content: str
    metadata: dict[str, Any]
    scope: str
    source: str
    importance: float
    veracity: str
    memory_id: str


_SECRET_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|private[_ -]?key|account[_ -]?key|session[_ -]?cookie|password|passwd)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    ),
    re.compile(
        r"(?i)\b[A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_KEY|PRIVATE_KEY|ACCOUNT_KEY|TOKEN|SECRET|SESSION_COOKIE|PASSWORD|PASSWD)\b"
        r"\s*[:=]\s*['\"]?[^\s'\"]{8,}"
    ),
    re.compile(r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+\S{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s/@]{4,}@[^\s]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
)

_TRUSTED_STRUCTURED_RESULT_TOOLS = frozenset(
    {
        "browser_exec",
        "computer_use",
        "patch",
        "web_extract",
        "web_search",
        "write_file",
    }
)
_TRUSTED_VERIFIED_WRITE_TOOLS = frozenset({"patch", "write_file"})


def contains_secret(value: Any) -> bool:
    raw_strings: list[str] = []

    def collect(item: Any) -> None:
        if isinstance(item, str):
            raw_strings.append(item)
        elif isinstance(item, dict):
            for key, nested in item.items():
                collect(str(key))
                collect(nested)
        elif isinstance(item, (list, tuple, set)):
            for nested in item:
                collect(nested)

    collect(value)
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return any(
        pattern.search(candidate)
        for candidate in [text, *raw_strings]
        for pattern in _SECRET_PATTERNS
    )


def latest_completed_turn(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the last user message and every message that follows it."""
    start = 0
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            start = index
    return messages[start:]


def _json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {"output": value}


def _tool_calls(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    calls: dict[str, dict[str, Any]] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            calls[str(call.get("id") or "")] = {
                "name": str(function.get("name") or call.get("name") or "tool"),
                "arguments": _json(function.get("arguments") or call.get("arguments") or {}),
            }
    return calls


def _evidence(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    calls = _tool_calls(messages)
    evidence: list[dict[str, Any]] = []
    tools: list[str] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        call = calls.get(str(message.get("tool_call_id") or ""), {})
        name = str(message.get("tool_name") or call.get("name") or "tool")
        if name.startswith("mnemosyne_"):
            continue
        tools.append(name)
        args = call.get("arguments") or {}
        result = _json(message.get("content") or message.get("result") or {})
        command = str(args.get("command") or "")
        exit_code = result.get("exit_code")
        if isinstance(exit_code, int) and name == "terminal":
            command_lower = command.lower()
            test_probe_only = bool(
                re.search(r"(?:^|\s)(?:--version|--help|-h|--collect-only)(?:\s|$)", command_lower)
            )
            kind = (
                "test_result"
                if not test_probe_only
                and any(
                    token in command_lower
                    for token in ("pytest", "unittest", "npm test", "cargo test", "go test")
                )
                else "exit_status"
            )
            meaningful_command = bool(
                re.search(
                    r"(?i)\b(?:build|test|install|uninstall|config|apply|deploy|migrate|commit|push|merge|restart|backup|restore|doctor|verify|repair)\b",
                    command,
                )
            )
            evidence.append(
                {
                    "kind": kind,
                    "tool": name,
                    "exit_code": exit_code,
                    "success": exit_code == 0,
                    "meaningful": kind == "test_result" or exit_code != 0 or meaningful_command,
                }
            )
        elif name in _TRUSTED_STRUCTURED_RESULT_TOOLS and (
            result.get("success") is False or bool(result.get("error"))
        ):
            evidence.append(
                {
                    "kind": "tool_error",
                    "tool": name,
                    "success": False,
                    "meaningful": True,
                }
            )
        elif result.get("verified") is True and name in _TRUSTED_VERIFIED_WRITE_TOOLS:
            evidence.append(
                {
                    "kind": "file_readback",
                    "tool": name,
                    "success": True,
                    "meaningful": name not in {"read_file", "vision_analyze"},
                }
            )
        elif result.get("success") is True and name in {"read_file", "web_extract"}:
            evidence.append(
                {
                    "kind": "authoritative_api_response",
                    "tool": name,
                    "success": True,
                    "meaningful": False,
                }
            )
    return evidence, sorted(set(tools))


def _skill_telemetry(messages: list[dict[str, Any]]) -> tuple[list[str], dict[str, str]]:
    calls = _tool_calls(messages)
    versions: dict[str, str] = {}
    for message in messages:
        if message.get("role") != "tool":
            continue
        call = calls.get(str(message.get("tool_call_id") or ""), {})
        name = str(message.get("tool_name") or call.get("name") or "")
        if name != "skill_view":
            continue
        result = _json(message.get("content") or {})
        if result.get("success") is False:
            continue
        skill_name = str(
            result.get("name") or (call.get("arguments") or {}).get("name") or ""
        ).strip()
        if not skill_name:
            continue
        body = str(result.get("content") or "")
        match = re.search(r"(?m)^version:\s*['\"]?([^\s'\"]+)", body)
        version = str(result.get("version") or (match.group(1) if match else "")).strip()
        if not version:
            version = f"sha256:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]}"
        versions[skill_name] = version
    names = sorted(versions)
    return names, {name: versions[name] for name in names}


def _file_telemetry(messages: list[dict[str, Any]]) -> list[str]:
    calls = _tool_calls(messages)
    files: set[str] = set()
    for message in messages:
        if message.get("role") != "tool":
            continue
        call = calls.get(str(message.get("tool_call_id") or ""), {})
        tool_name = str(message.get("tool_name") or call.get("name") or "")
        if tool_name not in {"write_file", "read_file", "patch"}:
            continue
        args = call.get("arguments") or {}
        result = _json(message.get("content") or {})
        path = str(result.get("resolved_path") or args.get("path") or "").strip()
        if path:
            name = re.split(r"[\\/]", path)[-1]
            if name:
                files.add(name)
    return sorted(files)


def _task_descriptor(
    messages: list[dict[str, Any]],
    tools: list[str],
    files: list[str],
) -> str:
    labels: set[str] = set(tools)
    for call in _tool_calls(messages).values():
        command = str((call.get("arguments") or {}).get("command") or "")
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        if tokens:
            labels.add(Path(tokens[0]).name)
        for token in tokens[1:]:
            clean = token.split("::", 1)[0]
            if Path(clean).suffix.lower() in {".py", ".js", ".ts", ".tsx", ".go", ".rs"}:
                labels.add(Path(clean).name)
    labels.update(Path(path).name for path in files)
    safe_labels = sorted(
        label
        for label in labels
        if label and len(label) <= 80 and re.fullmatch(r"[A-Za-z0-9._-]+", label)
    )
    return "Tool-assisted workflow using " + ", ".join(safe_labels or ["reviewed tools"])


def build_episode(
    messages: list[dict[str, Any]],
    *,
    project_id: str,
    session_id: str,
    turn_id: str,
) -> Episode | None:
    if not project_id:
        raise ValueError("project_id is required")
    if contains_secret(messages):
        return None
    evidence, tools = _evidence(messages)
    if not evidence or not any(item.get("meaningful") for item in evidence):
        return None
    verified = True
    meaningful_evidence = [item for item in evidence if item.get("meaningful")]
    conclusive = meaningful_evidence[-1]
    outcome = "success" if conclusive["success"] else "failure"
    skills_used, skill_versions = _skill_telemetry(messages)
    files = _file_telemetry(messages)
    task = _task_descriptor(messages, tools, files)
    verification = ", ".join(
        f"{item['kind']}:{'pass' if item['success'] else 'fail'}" for item in evidence
    )
    content = "\n".join(
        [
            f"[PROJECT:{project_id}]",
            f"Task: {task}",
            "Context: Hermes foreground tool trajectory",
            f"Observation: {len(evidence)} deterministic evidence item(s)",
            "Decision: Preserve compact evidence without raw tool output",
            f"Action: Used {', '.join(tools)}",
            f"Outcome: {outcome}",
            f"Verification: {verification}",
            f"Applicability: project_id={project_id}",
        ]
    )
    fingerprint_input = json.dumps(
        {
            "project_id": project_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "task": task,
            "outcome": outcome,
            "evidence": evidence,
            "tools": tools,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    metadata = {
        "kind": "execution_episode",
        "project_id": project_id,
        "task_class": "tool_execution",
        "outcome": outcome,
        "verified": verified,
        "verification_evidence": evidence,
        "tools": tools,
        "files": files,
        "skills_used": skills_used,
        "skill_versions": skill_versions,
        "fingerprint": fingerprint,
        "source_authority": "tool" if verified else "inferred",
    }
    return Episode(
        content=content,
        metadata=metadata,
        scope="global",
        source="execution_bridge",
        importance=0.72 if verified else 0.4,
        veracity="tool" if verified else "inferred",
        memory_id=f"exec_{fingerprint[:32]}",
    )
