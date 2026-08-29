from __future__ import annotations

import json
import logging
import queue
import re
import sqlite3
import subprocess
import threading
from pathlib import Path
from typing import Any, ClassVar

import yaml
from mnemosyne_hermes import MnemosyneMemoryProvider

from . import __version__
from .evidence import Episode, build_episode, latest_completed_turn
from .filtering import filter_prefetch_text, filter_recall_payload
from .pending import claim_pending, list_pending, stage_mutation
from .policy import Decision, classify_tool
from .project import normalize_project_id, normalize_project_reference

logger = logging.getLogger(__name__)


class ProjectAwareMnemosyneProvider(MnemosyneMemoryProvider):
    """Thin Mnemosyne wrapper enforcing project isolation for execution episodes."""

    bridge_version = __version__
    _EPISODE_STOP: ClassVar[object] = object()

    _BRIDGE_TOOL_SCHEMAS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "mnemosyne_bridge_pending_list",
            "description": (
                "List staged Mnemosyne mutations and each exact review payload without applying them."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "mnemosyne_bridge_apply_pending",
            "description": (
                "Apply one staged mutation only after the user explicitly says "
                "APPLY <pending_id>. First inspect the review payload from the stage result or "
                "pending list. The confirmation field must preserve that exact text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pending_id": {"type": "string"},
                    "confirmation": {"type": "string"},
                },
                "required": ["pending_id", "confirmation"],
            },
        },
        {
            "name": "mnemosyne_bridge_reject_pending",
            "description": "Reject one staged mutation after exact REJECT <pending_id> confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pending_id": {"type": "string"},
                    "confirmation": {"type": "string"},
                },
                "required": ["pending_id", "confirmation"],
            },
        },
    ]

    @property
    def name(self) -> str:
        return "mnemosyne-learning-bridge"

    def __init__(self) -> None:
        super().__init__()
        self._project_id = ""
        self._foreground_confirmation: tuple[str, str] | None = None
        self._confirmation_lock = threading.Lock()
        self._episode_queue: queue.Queue[tuple[Episode, str] | object] = queue.Queue(maxsize=32)
        self._episode_worker_lock = threading.Lock()
        self._episode_thread: threading.Thread | None = None

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        """Capture an exact host-delivered user confirmation for this turn only."""
        match = re.fullmatch(r"(APPLY|REJECT) ([A-Za-z0-9]{1,64})", str(message or ""))
        with self._confirmation_lock:
            self._foreground_confirmation = (
                (match.group(1), match.group(2)) if match is not None else None
            )
        super().on_turn_start(turn_number, message, **kwargs)

    def _consume_foreground_confirmation(self, action: str, pending_id: str) -> bool:
        with self._confirmation_lock:
            expected = (action, pending_id)
            if self._foreground_confirmation != expected:
                return False
            self._foreground_confirmation = None
            return True

    def _remove_orphan_gists(self, memory_id: str) -> int:
        """Remove only gists whose exact memory no longer exists."""
        with self._ensure_beam_access_lock():
            connection = self._beam.conn
            with connection:
                connection.execute(
                    "DELETE FROM gists WHERE memory_id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM working_memory WHERE id = ?) "
                    "AND NOT EXISTS (SELECT 1 FROM episodic_memory WHERE id = ?)",
                    (memory_id, memory_id, memory_id),
                )
            row = connection.execute(
                "SELECT COUNT(*) FROM gists WHERE memory_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM working_memory WHERE id = ?) "
                "AND NOT EXISTS (SELECT 1 FROM episodic_memory WHERE id = ?)",
                (memory_id, memory_id, memory_id),
            ).fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _mutation_acknowledged(
        tool: str,
        payload: dict[str, Any],
        mutation: dict[str, Any],
    ) -> bool:
        status = mutation.get("status")
        if tool == "mnemosyne_remember":
            return status in {"stored", "deduplicated"} and bool(mutation.get("memory_id"))
        if tool == "mnemosyne_remember_canonical":
            return status in {"stored", "unchanged", "superseded"}
        if tool == "mnemosyne_forget_canonical":
            return mutation.get("retired") is True
        if tool == "mnemosyne_update":
            return status == "updated" and mutation.get("memory_id") == payload.get("memory_id")
        if tool == "mnemosyne_forget":
            return status == "deleted" and mutation.get("memory_id") == payload.get("memory_id")
        if tool == "mnemosyne_invalidate":
            return status == "invalidated" and mutation.get("memory_id") == payload.get("memory_id")
        if tool == "mnemosyne_triple_add":
            return status == "stored" and bool(mutation.get("triple_id"))
        if tool == "mnemosyne_triple_end":
            return status == "ended" and int(mutation.get("count") or 0) > 0
        if tool == "mnemosyne_shared_remember":
            return status == "stored_shared" and bool(mutation.get("memory_id"))
        if tool == "mnemosyne_shared_forget":
            return status in {"deleted", "deleted_shared"} and mutation.get(
                "memory_id"
            ) == payload.get("memory_id")
        if tool == "mnemosyne_validate":
            return status == f"validation_{payload.get('action')}" and mutation.get(
                "memory_id"
            ) == payload.get("memory_id")
        return False

    @staticmethod
    def _git_remote(workdir: Path) -> str:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workdir),
                    "config",
                    "--null",
                    "--get",
                    "remote.origin.url",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0 or not result.stdout.endswith("\x00"):
                return ""
            return result.stdout[:-1]
        except (OSError, subprocess.SubprocessError):
            return ""

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        hermes_home = (
            Path(kwargs.get("hermes_home") or Path.home() / ".hermes").expanduser().resolve()
        )
        config_path = hermes_home / "config.yaml"
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            memory_config = config.get("memory") or {}
            provider_config = memory_config.get("mnemosyne") or {}
        except Exception as exc:
            raise RuntimeError(
                "cannot verify Mnemosyne sole durable authority configuration"
            ) from exc
        if (
            memory_config.get("memory_enabled") is not False
            or memory_config.get("user_profile_enabled") is not False
            or provider_config.get("sync_roles") != []
        ):
            raise RuntimeError(
                "Mnemosyne bridge requires sole durable authority and sync_roles: []"
            )
        explicit_raw = str(kwargs.pop("project_id", "") or "").strip()
        has_explicit_workdir = bool(kwargs.get("workdir") or kwargs.get("cwd"))
        workdir = Path(kwargs.get("workdir") or kwargs.get("cwd") or Path.cwd())
        remote = self._git_remote(workdir)
        derived = (
            normalize_project_id(workdir=workdir, git_remote=remote)
            if remote or has_explicit_workdir
            else ""
        )
        explicit = normalize_project_reference(explicit_raw) if explicit_raw else ""
        if explicit_raw and (not explicit or not has_explicit_workdir or explicit != derived):
            raise RuntimeError("explicit project_id must match the explicitly supplied workdir")
        self._project_id = explicit or derived
        list_pending(hermes_home=hermes_home)
        super().initialize(session_id, **kwargs)
        self._auto_sleep_enabled = False
        if set(getattr(self, "_sync_roles", set()) or set()):
            self.shutdown()
            raise RuntimeError("Mnemosyne bridge refuses non-empty raw autosave roles")

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for schema in super().get_tool_schemas():
            name = str(schema.get("name") or "")
            if classify_tool(name, {}) not in {Decision.BLOCK, Decision.UNKNOWN}:
                schemas.append(schema)
        return schemas + list(self._BRIDGE_TOOL_SCHEMAS)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return filter_prefetch_text(
            super().prefetch(query, session_id=session_id),
            self._project_id,
        )

    def handle_tool_call(self, tool_name: str, args: dict[str, Any]) -> str:
        current_session = str(getattr(self, "_session_id", "") or "")
        if tool_name == "mnemosyne_bridge_pending_list":
            hermes_home = self._hermes_home or str(Path.home() / ".hermes")
            records = list_pending(
                hermes_home=hermes_home,
                expected_session_id=current_session,
                expected_project_id=self._project_id,
            )
            return json.dumps({"count": len(records), "pending": records})
        if tool_name == "mnemosyne_bridge_apply_pending":
            try:
                return self._apply_bridge_pending(args)
            except Exception as exc:
                logger.exception("Mnemosyne bridge pending application failed")
                return json.dumps(
                    {
                        "status": "error",
                        "error": f"pending_application_failed:{type(exc).__name__}",
                    }
                )
        if tool_name == "mnemosyne_bridge_reject_pending":
            pending_id = str(args.get("pending_id") or "").strip()
            if args.get("confirmation") != f"REJECT {pending_id}":
                return json.dumps({"status": "blocked", "error": "exact confirmation is required"})
            if not self._consume_foreground_confirmation("REJECT", pending_id):
                return json.dumps(
                    {
                        "status": "blocked",
                        "error": "foreground_user_confirmation_required",
                    }
                )
            hermes_home = self._hermes_home or str(Path.home() / ".hermes")
            try:
                _, path = claim_pending(
                    hermes_home=hermes_home,
                    pending_id=pending_id,
                    expected_session_id=current_session,
                    expected_project_id=self._project_id,
                )
                path.unlink()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return json.dumps({"status": "error", "error": str(exc)})
            return json.dumps({"status": "rejected", "pending_id": pending_id})
        decision = classify_tool(tool_name, args)
        if (
            tool_name == "mnemosyne_remember"
            and decision is Decision.STAGE
            and (
                args.get("scope") not in {"session", "global"}
                or args.get("source") != "user"
                or args.get("veracity") != "stated"
                or bool(args.get("extract"))
                or bool(args.get("extract_entities"))
            )
        ):
            return json.dumps(
                {
                    "status": "blocked",
                    "error": "ordinary_remember_requires_explicit_scope_source_and_stated_veracity",
                }
            )
        if decision is Decision.STAGE:
            hermes_home = self._hermes_home or str(Path.home() / ".hermes")
            return json.dumps(
                stage_mutation(
                    hermes_home=hermes_home,
                    tool=tool_name,
                    payload=args,
                    session_id=current_session,
                    project_id=self._project_id,
                )
            )
        if decision is Decision.BLOCK:
            return json.dumps(
                {
                    "status": "blocked",
                    "error": f"{tool_name} is blocked; use the bridge approval tools",
                }
            )
        if decision is Decision.UNKNOWN:
            return json.dumps(
                {
                    "status": "blocked",
                    "error": f"{tool_name} has no reviewed mutation policy",
                }
            )
        result = super().handle_tool_call(tool_name, args)
        if decision is not Decision.READ:
            return result
        try:
            filtered = filter_recall_payload(result, self._project_id)
            if tool_name == "mnemosyne_get":
                parsed = json.loads(filtered)
                if isinstance(parsed, dict) and parsed.get("memory") is None:
                    parsed["status"] = "not_found"
                    return json.dumps(parsed, ensure_ascii=False)
            return filtered
        except (TypeError, ValueError, json.JSONDecodeError):
            logger.error("Mnemosyne bridge project filter failed; suppressing read results")
            return json.dumps(
                {
                    "status": "error",
                    "error": "project_filter_failed",
                    "results": [],
                }
            )

    def _apply_bridge_pending(self, args: dict[str, Any]) -> str:
        pending_id = str(args.get("pending_id") or "").strip()
        if args.get("confirmation") != f"APPLY {pending_id}":
            return json.dumps({"status": "blocked", "error": "exact confirmation is required"})
        if not self._consume_foreground_confirmation("APPLY", pending_id):
            return json.dumps(
                {
                    "status": "blocked",
                    "error": "foreground_user_confirmation_required",
                }
            )
        hermes_home = self._hermes_home or str(Path.home() / ".hermes")
        current_session = str(getattr(self, "_session_id", "") or "")
        try:
            record, path = claim_pending(
                hermes_home=hermes_home,
                pending_id=pending_id,
                expected_session_id=current_session,
                expected_project_id=self._project_id,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        tool = str(record["tool"])
        payload = dict(record["payload"])
        if tool not in {
            "mnemosyne_remember",
            "mnemosyne_update",
            "mnemosyne_forget",
        }:
            return json.dumps(
                {
                    "status": "blocked",
                    "error": f"no verified read-back adapter for {tool}",
                    "pending_id": pending_id,
                }
            )
        mutation_raw = super().handle_tool_call(tool, payload)
        try:
            mutation = json.loads(mutation_raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return json.dumps({"status": "error", "error": "mutation returned invalid JSON"})
        if mutation.get("error") or mutation.get("status") in {
            "error",
            "blocked",
            "memory_unavailable",
        }:
            return json.dumps({"status": "error", "mutation": mutation})
        if not self._mutation_acknowledged(tool, payload, mutation):
            return json.dumps(
                {
                    "status": "verification_failed",
                    "pending_id": pending_id,
                    "error": "mutation_not_acknowledged",
                    "mutation": mutation,
                }
            )
        verified = False
        if tool == "mnemosyne_remember":
            memory_id = str(mutation.get("memory_id") or "")
            readback = (
                json.loads(super().handle_tool_call("mnemosyne_get", {"memory_id": memory_id}))
                if memory_id
                else {"status": "not_found"}
            )
            memory_value = readback.get("memory")
            memory = memory_value if isinstance(memory_value, dict) else {}
            verified = bool(
                readback.get("status") == "ok"
                and memory.get("id") == memory_id
                and memory.get("content") == payload.get("content")
                and memory.get("scope") == payload.get("scope")
                and memory.get("source") == payload.get("source")
                and memory.get("veracity") == payload.get("veracity")
            )
            if verified and payload.get("importance") is not None:
                verified = memory.get("importance") == payload.get("importance")
            if verified and payload.get("valid_until") is not None:
                verified = memory.get("valid_until") == payload.get("valid_until")
            if verified and payload.get("metadata") is not None:
                memory_metadata = memory.get("metadata")
                if isinstance(memory_metadata, str):
                    try:
                        memory_metadata = json.loads(memory_metadata)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        memory_metadata = None
                verified = memory_metadata == payload.get("metadata")
        elif tool in {"mnemosyne_remember_canonical", "mnemosyne_forget_canonical"}:
            readback_args = {"category": payload.get("category"), "name": payload.get("name")}
            readback = json.loads(
                super().handle_tool_call("mnemosyne_recall_canonical", readback_args)
            )
            expected_found = tool == "mnemosyne_remember_canonical"
            verified = (
                readback.get("found") is True if expected_found else readback.get("found") is False
            )
            if verified and expected_found:
                verified = (readback.get("result") or {}).get("body") == payload.get("body")
        elif tool in {"mnemosyne_triple_add", "mnemosyne_triple_end"}:
            query = {key: payload.get(key) for key in ("subject", "predicate", "object")}
            readback = json.loads(super().handle_tool_call("mnemosyne_triple_query", query))
            rows = readback.get("results")
            matches = (
                [
                    item
                    for item in rows
                    if isinstance(item, dict)
                    and all(item.get(key) == payload.get(key) for key in query)
                ]
                if isinstance(rows, list)
                else []
            )
            if tool == "mnemosyne_triple_add":
                verified = (
                    isinstance(rows, list) and bool(matches) and readback.get("count") == len(rows)
                )
            else:
                verified = isinstance(rows, list) and rows == [] and readback.get("count") == 0
        elif tool in {"mnemosyne_shared_remember", "mnemosyne_shared_forget"}:
            memory_id = str(mutation.get("memory_id") or payload.get("memory_id") or "")
            surface = getattr(self, "_surface_beam", None)
            with self._ensure_beam_access_lock():
                readback = surface.get(memory_id) if surface is not None and memory_id else None
            if tool == "mnemosyne_shared_forget":
                verified = readback is None
            else:
                verified = bool(
                    readback
                    and readback.get("id") == memory_id
                    and str(payload.get("content") or "") in str(readback.get("content") or "")
                )
        elif tool == "mnemosyne_invalidate":
            with self._ensure_beam_access_lock():
                row = self._beam.conn.execute(
                    "SELECT valid_until FROM working_memory WHERE id = ?",
                    (payload.get("memory_id"),),
                ).fetchone()
            readback = {
                "memory_id": payload.get("memory_id"),
                "valid_until": row[0] if row else None,
            }
            verified = bool(row and row[0])
        elif tool == "mnemosyne_validate":
            action = str(payload.get("action") or "")
            bank = str(payload.get("bank") or "private")
            target = getattr(self, "_surface_beam", None) if bank == "surface" else self._beam
            if action in {"invalidate", "attest"}:
                if target is None:
                    row = None
                else:
                    with self._ensure_beam_access_lock():
                        row = target.conn.execute(
                            "SELECT valid_until, validator, validated_at, validation_count, content "
                            "FROM working_memory WHERE id = ?",
                            (payload.get("memory_id"),),
                        ).fetchone()
                readback = {
                    "memory_id": payload.get("memory_id"),
                    "valid_until": row[0] if row else None,
                    "validator": row[1] if row else None,
                    "validated_at": row[2] if row else None,
                    "validation_count": row[3] if row else None,
                    "content": row[4] if row else None,
                }
                if action == "invalidate":
                    verified = bool(row and row[0])
                else:
                    verified = bool(row and row[2] and int(row[3] or 0) > 0)
            else:
                if bank == "surface":
                    with self._ensure_beam_access_lock():
                        surface_memory = (
                            target.get(payload.get("memory_id")) if target is not None else None
                        )
                    readback = {
                        "status": "ok" if surface_memory else "not_found",
                        "memory": surface_memory,
                    }
                else:
                    readback = json.loads(
                        super().handle_tool_call(
                            "mnemosyne_get", {"memory_id": payload.get("memory_id")}
                        )
                    )
                if action == "update":
                    readback_memory = readback.get("memory")
                    readback_memory = readback_memory if isinstance(readback_memory, dict) else {}
                    verified = readback.get("status") == "ok" and readback_memory.get(
                        "content"
                    ) == payload.get("new_content")
                elif action == "delete":
                    verified = readback.get("status") == "not_found"
                else:
                    verified = False
        else:
            readback = json.loads(
                super().handle_tool_call("mnemosyne_get", {"memory_id": payload.get("memory_id")})
            )
            memory_value = readback.get("memory")
            memory = memory_value if isinstance(memory_value, dict) else {}
            verified = (
                (
                    readback.get("status") == "not_found"
                    and (readback.get("memory") is None or readback.get("memory") == "")
                    and readback.get("memory_id", payload.get("memory_id"))
                    == payload.get("memory_id")
                )
                if tool == "mnemosyne_forget"
                else (
                    readback.get("status") == "ok" and memory.get("id") == payload.get("memory_id")
                )
            )
            if verified and tool == "mnemosyne_forget":
                memory_id = str(payload.get("memory_id") or "")
                orphan_gists = self._remove_orphan_gists(memory_id)
                readback["orphan_gists"] = orphan_gists
                verified = orphan_gists == 0
            if verified and payload.get("content") is not None:
                verified = memory.get("content") == payload.get("content")
            if verified and payload.get("importance") is not None:
                readback_importance = memory.get("importance")
                verified = bool(
                    readback_importance is not None
                    and float(readback_importance) == float(payload["importance"])
                )
        if not verified:
            return json.dumps(
                {
                    "status": "verification_failed",
                    "pending_id": pending_id,
                    "mutation": mutation,
                    "readback": readback,
                }
            )
        path.unlink()
        return json.dumps(
            {
                "status": "applied",
                "pending_id": pending_id,
                "tool": tool,
                "mutation": mutation,
                "readback": readback,
            }
        )

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Store at most one compact project episode without upstream autosave."""
        if not messages:
            return
        latest = latest_completed_turn(messages)
        if not self._project_id:
            return
        episode = build_episode(
            latest,
            project_id=self._project_id,
            session_id=session_id,
            turn_id=str(latest[-1].get("id") or f"{session_id}:{len(latest)}"),
        )
        if episode is None:
            return
        self._enqueue_episode(episode, session_id)

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """Disable the upstream automatic consolidation hook."""

    def on_memory_write(self, action: str, target: str, content: str) -> None:
        """Disable upstream mirroring of built-in Hermes memory writes."""

    def _enqueue_episode(self, episode: Episode, session_id: str) -> None:
        with self._episode_worker_lock:
            if self._episode_thread is None or not self._episode_thread.is_alive():
                self._episode_thread = threading.Thread(
                    target=self._episode_worker,
                    name="mnemosyne-learning-bridge-writer",
                    daemon=True,
                )
                self._episode_thread.start()
        try:
            self._episode_queue.put_nowait((episode, session_id))
        except queue.Full:
            logger.warning("Mnemosyne bridge episode queue is full; dropping %s", episode.memory_id)

    def _episode_worker(self) -> None:
        while True:
            item = self._episode_queue.get()
            try:
                if item is self._EPISODE_STOP:
                    return
                if not isinstance(item, tuple):
                    logger.error("Mnemosyne bridge received an invalid queue item")
                    continue
                episode, session_id = item
                self._persist_episode(episode, session_id)
            finally:
                self._episode_queue.task_done()

    def _persist_episode(self, episode: Episode, session_id: str) -> None:
        try:
            with self._beam_session_scope(session_id) as beam:
                if beam is None:
                    return
                beam.remember(
                    content=episode.content,
                    source=episode.source,
                    importance=episode.importance,
                    metadata=episode.metadata,
                    scope=episode.scope,
                    memory_id=episode.memory_id,
                    extract_entities=False,
                    extract=False,
                    veracity=episode.veracity,
                )
        except sqlite3.IntegrityError:
            logger.debug("Mnemosyne bridge skipped duplicate episode %s", episode.memory_id)
        except Exception as exc:
            logger.warning("Mnemosyne bridge episode write failed: %s", exc)

    def shutdown(self) -> None:
        thread = self._episode_thread
        if thread is not None and thread.is_alive():
            try:
                self._episode_queue.put(self._EPISODE_STOP, timeout=1)
                thread.join(timeout=5)
                if thread.is_alive():
                    logger.warning(
                        "Mnemosyne bridge writer did not stop; upstream state remains open"
                    )
                    return
            except queue.Full:
                logger.warning("Mnemosyne bridge writer did not drain during shutdown")
                return
        super().shutdown()


def register_memory_provider(ctx: Any) -> None:
    ctx.register_memory_provider(ProjectAwareMnemosyneProvider())


def register(ctx: Any) -> None:
    register_memory_provider(ctx)
