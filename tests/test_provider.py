import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import mnemosyne_hermes
import pytest

from mnemosyne_learning_bridge import pending
from mnemosyne_learning_bridge.provider import ProjectAwareMnemosyneProvider


def _authorize(provider: ProjectAwareMnemosyneProvider, action: str, pending_id: str) -> None:
    provider.on_turn_start(1, f"{action} {pending_id}")


def test_provider_reports_distinct_public_plugin_name() -> None:
    provider = ProjectAwareMnemosyneProvider()

    assert provider.name == "mnemosyne-learning-bridge"


def test_provider_does_not_treat_generic_startup_cwd_as_project(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ProjectAwareMnemosyneProvider,
        "_git_remote",
        staticmethod(lambda workdir: ""),
    )

    provider = ProjectAwareMnemosyneProvider()

    assert provider._project_id == ""


def test_provider_constructor_never_binds_project_from_git_cwd(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        ProjectAwareMnemosyneProvider,
        "_git_remote",
        staticmethod(lambda workdir: "git@github.com:victim/foreign.git"),
    )

    provider = ProjectAwareMnemosyneProvider()

    assert provider._project_id == ""


def test_git_remote_lookup_uses_nul_framing_and_preserves_boundary_spaces(
    tmp_path, monkeypatch
) -> None:
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return SimpleNamespace(returncode=0, stdout=" remote \x00")

    monkeypatch.setattr("mnemosyne_learning_bridge.provider.subprocess.run", fake_run)

    remote = ProjectAwareMnemosyneProvider._git_remote(tmp_path)

    assert "--null" in observed["command"]
    assert remote == " remote "


def test_provider_prefetch_removes_foreign_project_episode(monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "prefetch",
        lambda self, query, session_id="": (
            "## Mnemosyne Context\n"
            "  [PROJECT:github.com/acme/app] relevant\n"
            "  [PROJECT:github.com/acme/other] foreign\n"
            "  durable user fact"
        ),
    )

    result = provider.prefetch("parser", session_id="s2")

    assert "relevant" in result
    assert "foreign" not in result
    assert "durable user fact" in result


def test_provider_filters_explicit_recall_results(monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    payload = {
        "results": [
            {
                "id": "same",
                "content": "same",
                "metadata": {"kind": "execution_episode", "project_id": "github.com/acme/app"},
            },
            {
                "id": "other",
                "content": "other",
                "metadata": {"kind": "execution_episode", "project_id": "github.com/acme/other"},
            },
        ]
    }
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "handle_tool_call",
        lambda self, name, args: json.dumps(payload),
    )

    result = json.loads(provider.handle_tool_call("mnemosyne_recall", {"query": "parser"}))

    assert [item["id"] for item in result["results"]] == ["same"]


def test_provider_filters_foreign_project_episode_from_get(monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    payload = {
        "status": "ok",
        "memory": {
            "id": "foreign",
            "content": "[PROJECT:github.com/acme/other] secret episode",
            "metadata": {"kind": "execution_episode", "project_id": "github.com/acme/other"},
        },
    }
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "handle_tool_call",
        lambda self, name, args: json.dumps(payload),
    )

    result = json.loads(provider.handle_tool_call("mnemosyne_get", {"memory_id": "foreign"}))

    assert result["status"] == "not_found"
    assert result["memory"] is None
    assert "secret episode" not in json.dumps(result)


def test_provider_fails_closed_when_recall_filter_cannot_parse_payload(monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "handle_tool_call",
        lambda self, name, args: "not-json",
    )

    result = json.loads(provider.handle_tool_call("mnemosyne_recall", {"query": "parser"}))

    assert result == {
        "status": "error",
        "error": "project_filter_failed",
        "results": [],
    }


def test_sync_turn_writes_one_global_project_episode_with_stable_id(monkeypatch) -> None:
    class FakeBeam:
        def __init__(self) -> None:
            self.writes = []

        def remember(self, **kwargs):
            self.writes.append(kwargs)
            return kwargs["memory_id"]

    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    provider._beam = FakeBeam()
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        lambda self, user_content, assistant_content, session_id="": None,
    )
    messages = [
        {"role": "user", "content": "Verify the app."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-test",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-test",
            "tool_name": "terminal",
            "content": json.dumps({"output": "8 passed", "exit_code": 0}),
        },
    ]

    provider.sync_turn(
        "Verify the app.",
        "Verified.",
        session_id="session-9",
        messages=messages,
    )
    provider._episode_queue.join()

    assert len(provider._beam.writes) == 1
    write = provider._beam.writes[0]
    assert write["scope"] == "global"
    assert write["metadata"]["project_id"] == "github.com/acme/app"
    assert write["metadata"]["verified"] is True
    assert write["memory_id"].startswith("exec_")


def test_sync_turn_returns_before_episode_storage_finishes(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    class SlowBeam:
        def remember(self, **kwargs):
            started.set()
            release.wait(timeout=2)
            return kwargs["memory_id"]

    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    provider._beam = SlowBeam()
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        lambda self, user_content, assistant_content, session_id="": None,
    )
    messages = [
        {"role": "user", "content": "Verify the app."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-test",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-test",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 0}),
        },
    ]

    before = time.monotonic()
    provider.sync_turn("Verify", "Done", session_id="s", messages=messages)
    elapsed = time.monotonic() - before

    assert elapsed < 0.2
    assert started.wait(timeout=1)
    release.set()


def test_sync_turn_never_calls_upstream_autosave_or_consolidation(monkeypatch) -> None:
    upstream_called = threading.Event()

    def slow_upstream(self, user_content, assistant_content, session_id=""):
        upstream_called.set()
        time.sleep(0.4)

    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        slow_upstream,
    )
    provider = ProjectAwareMnemosyneProvider()

    before = time.monotonic()
    provider.sync_turn("Do not save this", "Done", session_id="session-policy")
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert upstream_called.is_set() is False


def test_shutdown_does_not_close_upstream_while_writer_is_still_running(monkeypatch) -> None:
    upstream_shutdown = threading.Event()

    class StuckThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "shutdown",
        lambda self: upstream_shutdown.set(),
    )
    provider = ProjectAwareMnemosyneProvider()
    monkeypatch.setattr(provider, "_episode_thread", StuckThread())

    provider.shutdown()

    assert upstream_shutdown.is_set() is False


def test_sync_turn_does_not_bind_project_from_tool_workdir(monkeypatch, tmp_path) -> None:
    class FakeBeam:
        def __init__(self) -> None:
            self.writes = []

        def remember(self, **kwargs):
            self.writes.append(kwargs)
            return kwargs["memory_id"]

    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = ""
    provider._beam = FakeBeam()
    monkeypatch.setattr(
        provider,
        "_git_remote",
        lambda workdir: "git@github.com:acme/parser.git" if workdir == tmp_path else "",
    )
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        lambda self, user_content, assistant_content, session_id="": None,
    )
    messages = [
        {"role": "user", "content": "Verify parser"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-test",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps(
                            {
                                "command": "pytest -q",
                                "workdir": str(tmp_path),
                            }
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-test",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 0, "output": "1 passed"}),
        },
    ]

    provider.sync_turn("Verify parser", "Verified", session_id="session-project", messages=messages)

    assert provider._project_id == ""
    assert provider._beam.writes == []


def test_ordinary_remember_requires_explicit_stated_provenance_and_scope(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_remember",
            {"content": "The user prefers concise answers"},
        )
    )

    assert result == {
        "status": "clarification_required",
        "error": "ordinary_remember_requires_explicit_scope_source_and_stated_veracity",
    }
    inferred = json.loads(
        provider.handle_tool_call(
            "mnemosyne_remember",
            {
                "content": "The user prefers concise answers",
                "scope": "global",
                "source": "assistant",
                "veracity": "stated",
            },
        )
    )
    assert inferred["status"] == "clarification_required"
    pending_dir = tmp_path / "pending" / "memory"
    assert not pending_dir.exists() or not list(pending_dir.glob("*.json"))


def test_ordinary_stated_user_memory_writes_directly_with_exact_readback(
    tmp_path, monkeypatch
) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE working_memory ("
        "id TEXT PRIMARY KEY, content TEXT, source TEXT, importance REAL, "
        "metadata_json TEXT, veracity TEXT, valid_until TEXT, scope TEXT)"
    )
    payload = {
        "content": "The user prefers concise answers",
        "scope": "global",
        "source": "user",
        "veracity": "stated",
        "metadata": {"channel": "telegram"},
        "valid_until": "2026-09-02",
    }
    connection.execute(
        "INSERT INTO working_memory VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "mem-stated",
            payload["content"],
            "user",
            0.5,
            json.dumps(payload["metadata"]),
            "stated",
            payload["valid_until"],
            "global",
        ),
    )
    provider._beam = SimpleNamespace(conn=connection)

    def base_call(self, name, args):
        if name == "mnemosyne_remember":
            return json.dumps({"status": "stored", "memory_id": "mem-stated"})
        if name == "mnemosyne_get":
            return json.dumps(
                {
                    "status": "ok",
                    "memory": {
                        "id": "mem-stated",
                        "content": payload["content"],
                        "source": "user",
                        "metadata": json.dumps(payload["metadata"]),
                        "veracity": "stated",
                    },
                }
            )
        raise AssertionError(name)

    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "handle_tool_call",
        base_call,
    )
    result = json.loads(provider.handle_tool_call("mnemosyne_remember", payload))

    assert result["status"] == "stored"
    assert result["verified"] is True
    assert result["readback"]["memory"]["metadata"] == payload["metadata"]
    assert result["readback"]["memory"]["valid_until"] == payload["valid_until"]
    assert result["readback"]["memory"]["veracity"] == "stated"
    pending_dir = tmp_path / "pending" / "memory"
    assert not pending_dir.exists() or not list(pending_dir.glob("*.json"))


def test_apply_pending_update_reads_back_exact_memory(tmp_path, monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)

    def base_call(self, name, args):
        if name == "mnemosyne_update":
            return json.dumps({"status": "updated", "memory_id": args["memory_id"]})
        if name == "mnemosyne_get":
            return json.dumps(
                {"status": "ok", "memory": {"id": args["memory_id"], "content": "new value"}}
            )
        raise AssertionError(name)

    monkeypatch.setattr(mnemosyne_hermes.MnemosyneMemoryProvider, "handle_tool_call", base_call)
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update", {"memory_id": "mem-1", "content": "new value"}
        )
    )
    pending_id = staged["pending_id"]
    _authorize(provider, "APPLY", pending_id)

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": pending_id, "confirmation": f"APPLY {pending_id}"},
        )
    )

    assert result["status"] == "applied"
    assert result["readback"]["memory"]["content"] == "new value"


def test_apply_pending_forget_reads_back_absence(tmp_path, monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE working_memory (id TEXT PRIMARY KEY);"
        "CREATE TABLE episodic_memory (id TEXT PRIMARY KEY);"
        "CREATE TABLE gists (memory_id TEXT);"
        "INSERT INTO gists(memory_id) VALUES ('mem-delete');"
    )
    provider._beam = SimpleNamespace(conn=connection)

    def base_call(self, name, args):
        if name == "mnemosyne_forget":
            return json.dumps({"status": "deleted", "memory_id": args["memory_id"]})
        if name == "mnemosyne_get":
            return json.dumps({"status": "not_found", "memory_id": args["memory_id"]})
        raise AssertionError(name)

    monkeypatch.setattr(mnemosyne_hermes.MnemosyneMemoryProvider, "handle_tool_call", base_call)
    staged = json.loads(provider.handle_tool_call("mnemosyne_forget", {"memory_id": "mem-delete"}))
    pending_id = staged["pending_id"]
    _authorize(provider, "APPLY", pending_id)

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": pending_id, "confirmation": f"APPLY {pending_id}"},
        )
    )

    assert result["status"] == "applied"
    assert result["readback"]["status"] == "not_found"
    assert result["readback"]["orphan_gists"] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM gists WHERE memory_id = 'mem-delete'").fetchone()[
            0
        ]
        == 0
    )


def test_initialize_refuses_duplicate_authority_or_raw_autosave(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(
        "memory:\n"
        "  memory_enabled: true\n"
        "  user_profile_enabled: false\n"
        "  mnemosyne:\n"
        "    sync_roles: []\n"
    )
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "initialize",
        lambda self, session_id, **kwargs: setattr(self, "_sync_roles", set()),
    )
    provider = ProjectAwareMnemosyneProvider()

    with pytest.raises(RuntimeError, match="sole durable authority"):
        provider.initialize("session-1", hermes_home=str(tmp_path))


def test_initialize_disables_upstream_automatic_consolidation(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(
        "memory:\n"
        "  memory_enabled: false\n"
        "  user_profile_enabled: false\n"
        "  mnemosyne:\n"
        "    sync_roles: []\n"
    )

    def base_initialize(self, session_id, **kwargs):
        self._sync_roles = set()
        self._auto_sleep_enabled = True

    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "initialize",
        base_initialize,
    )
    provider = ProjectAwareMnemosyneProvider()

    provider.initialize("session-1", hermes_home=str(tmp_path))

    assert provider._auto_sleep_enabled is False


def test_session_end_never_calls_upstream_consolidation(monkeypatch) -> None:
    upstream_called = threading.Event()
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "on_session_end",
        lambda self, messages: upstream_called.set(),
    )
    provider = ProjectAwareMnemosyneProvider()

    provider.on_session_end([])

    assert upstream_called.is_set() is False


def test_builtin_memory_write_never_calls_upstream_mirror(monkeypatch) -> None:
    upstream_called = threading.Event()
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "on_memory_write",
        lambda self, action, target, content: upstream_called.set(),
    )
    provider = ProjectAwareMnemosyneProvider()

    provider.on_memory_write("add", "user", "do not mirror")

    assert upstream_called.is_set() is False


def test_provider_exposes_conditionally_allowed_upstream_tools() -> None:
    provider = ProjectAwareMnemosyneProvider()
    names = {schema["name"] for schema in provider.get_tool_schemas()}

    assert {"mnemosyne_remember", "mnemosyne_batch", "mnemosyne_sleep"} <= names


def test_provider_exposes_bridge_approval_tools_and_hides_unsafe_upstream_apply() -> None:
    provider = ProjectAwareMnemosyneProvider()
    schemas = {schema["name"]: schema for schema in provider.get_tool_schemas()}
    names = set(schemas)

    assert "mnemosyne_bridge_pending_list" in names
    assert "mnemosyne_bridge_apply_pending" in names
    assert "mnemosyne_bridge_reject_pending" in names
    assert "mnemosyne_apply_pending" not in names
    assert "review payload" in schemas["mnemosyne_bridge_pending_list"]["description"]
    assert "inspect" in schemas["mnemosyne_bridge_apply_pending"]["description"]


def test_staged_mutation_and_pending_list_expose_exact_review_payload(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update",
            {
                "memory_id": "mem-pending-review",
                "content": "durable preference",
            },
        )
    )

    result = json.loads(provider.handle_tool_call("mnemosyne_bridge_pending_list", {}))

    expected_payload = {
        "memory_id": "mem-pending-review",
        "content": "durable preference",
    }
    assert staged["review"] == {
        "tool": "mnemosyne_update",
        "payload": expected_payload,
        "payload_sha256": staged["review"]["payload_sha256"],
    }
    assert result["count"] == 1
    assert result["pending"][0]["id"] == staged["pending_id"]
    assert result["pending"][0]["tool"] == "mnemosyne_update"
    assert result["pending"][0]["review"] == staged["review"]
    assert result["pending"][0]["review"]["payload"] == expected_payload


def test_unclaimed_apply_attempt_cannot_delete_another_call_claim(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update",
            {
                "memory_id": "mem-pending-review",
                "content": "durable preference",
            },
        )
    )
    _, claimed_path = pending.claim_pending(
        hermes_home=tmp_path,
        pending_id=staged["pending_id"],
    )

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": staged["pending_id"], "confirmation": "wrong"},
        )
    )

    assert result["status"] == "blocked"
    assert claimed_path.exists()


def test_reject_pending_requires_exact_confirmation_and_deletes_record(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update",
            {
                "memory_id": "mem-pending-review",
                "content": "durable preference",
            },
        )
    )
    pending_id = staged["pending_id"]

    blocked = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_reject_pending",
            {"pending_id": pending_id, "confirmation": "REJECT wrong"},
        )
    )
    assert blocked["status"] == "blocked"

    _authorize(provider, "REJECT", pending_id)
    rejected = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_reject_pending",
            {"pending_id": pending_id, "confirmation": f"REJECT {pending_id}"},
        )
    )
    assert rejected["status"] == "rejected"
    assert json.loads(provider.handle_tool_call("mnemosyne_bridge_pending_list", {}))["count"] == 0


def test_incomplete_mutation_adapters_are_blocked_and_hidden() -> None:
    provider = ProjectAwareMnemosyneProvider()
    blocked = {
        "mnemosyne_shared_remember",
        "mnemosyne_shared_forget",
        "mnemosyne_invalidate",
        "mnemosyne_validate",
        "mnemosyne_triple_add",
        "mnemosyne_triple_end",
        "mnemosyne_remember_canonical",
        "mnemosyne_forget_canonical",
    }
    exposed = {schema["name"] for schema in provider.get_tool_schemas()}

    assert blocked.isdisjoint(exposed)
    for name in blocked:
        result = json.loads(provider.handle_tool_call(name, {}))
        assert result["status"] == "blocked"


def test_model_supplied_confirmation_without_foreground_turn_is_blocked(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update",
            {
                "memory_id": "mem-pending-review",
                "content": "durable preference",
            },
        )
    )
    pending_id = staged["pending_id"]

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": pending_id, "confirmation": f"APPLY {pending_id}"},
        )
    )

    assert result["status"] == "blocked"
    assert result["error"] == "foreground_user_confirmation_required"


def test_pending_approval_is_bound_to_staging_session(tmp_path) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)
    provider._session_id = "session-a"
    staged = json.loads(
        provider.handle_tool_call(
            "mnemosyne_update",
            {
                "memory_id": "mem-pending-review",
                "content": "durable preference",
            },
        )
    )
    pending_id = staged["pending_id"]
    provider._session_id = "session-b"
    _authorize(provider, "APPLY", pending_id)

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": pending_id, "confirmation": f"APPLY {pending_id}"},
        )
    )

    assert result["status"] == "error"
    assert "different session" in result["error"]


def test_sync_turn_refuses_project_namespace_switch(monkeypatch, tmp_path) -> None:
    class FakeBeam:
        def __init__(self) -> None:
            self.writes = []

        def remember(self, **kwargs):
            self.writes.append(kwargs)

    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = "github.com/acme/app"
    provider._beam = FakeBeam()
    monkeypatch.setattr(
        provider,
        "_git_remote",
        lambda workdir: "git@github.com:acme/other.git",
    )
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        lambda self, user_content, assistant_content, session_id="": None,
    )
    messages = [
        {"role": "user", "content": "Run tests"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "test",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q", "workdir": str(tmp_path)}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "test",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 0}),
        },
    ]

    provider.sync_turn("Run tests", "Done", session_id="s", messages=messages)
    provider._episode_queue.join()

    assert provider._project_id == "github.com/acme/app"
    assert provider._beam.writes[0]["metadata"]["project_id"] == "github.com/acme/app"


def test_unbound_provider_does_not_bind_from_foreign_absolute_tool_path(
    monkeypatch, tmp_path
) -> None:
    class FakeBeam:
        def __init__(self) -> None:
            self.writes = []

        def remember(self, **kwargs):
            self.writes.append(kwargs)

    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = ""
    provider._beam = FakeBeam()
    monkeypatch.setattr(
        provider,
        "_git_remote",
        lambda workdir: "git@github.com:victim/foreign.git",
    )
    monkeypatch.setattr(
        mnemosyne_hermes.MnemosyneMemoryProvider,
        "sync_turn",
        lambda self, user_content, assistant_content, session_id="": None,
    )
    messages = [
        {"role": "user", "content": "Read a foreign file"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "read",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": str(tmp_path / "secret.txt")}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read",
            "tool_name": "read_file",
            "content": json.dumps({"content": "not relevant"}),
        },
    ]

    provider.sync_turn("Read a foreign file", "Done", session_id="s", messages=messages)

    assert provider._project_id == ""
    assert provider._beam.writes == []


def test_update_readback_requires_requested_memory_id(tmp_path, monkeypatch) -> None:
    provider = ProjectAwareMnemosyneProvider()
    provider._hermes_home = str(tmp_path)

    def base_call(self, name, args):
        if name == "mnemosyne_update":
            return json.dumps({"status": "updated", "memory_id": "mem-1"})
        if name == "mnemosyne_get":
            return json.dumps({"status": "ok", "memory": {"id": "different", "content": "new"}})
        raise AssertionError(name)

    monkeypatch.setattr(mnemosyne_hermes.MnemosyneMemoryProvider, "handle_tool_call", base_call)
    staged = json.loads(
        provider.handle_tool_call("mnemosyne_update", {"memory_id": "mem-1", "content": "new"})
    )
    pending_id = staged["pending_id"]
    _authorize(provider, "APPLY", pending_id)

    result = json.loads(
        provider.handle_tool_call(
            "mnemosyne_bridge_apply_pending",
            {"pending_id": pending_id, "confirmation": f"APPLY {pending_id}"},
        )
    )

    assert result["status"] == "verification_failed"
