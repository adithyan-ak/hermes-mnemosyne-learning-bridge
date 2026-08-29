import json
from pathlib import Path

from mnemosyne.core.beam import BeamMemory

from mnemosyne_learning_bridge.provider import ProjectAwareMnemosyneProvider


def _provider(db_path: Path, project_id: str, session_id: str) -> ProjectAwareMnemosyneProvider:
    provider = ProjectAwareMnemosyneProvider()
    provider._project_id = project_id
    provider._session_id = session_id
    provider._gateway_session_key = ""
    provider._channel_id_explicit = False
    provider._beam = BeamMemory(session_id=session_id, db_path=db_path)
    provider._sync_roles = set()
    provider._auto_sleep = False
    return provider


def _verified_messages() -> list[dict]:
    return [
        {"role": "user", "content": "Verify the isolated parser workflow."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "test-call",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest tests/test_parser.py -q"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "test-call",
            "tool_name": "terminal",
            "content": json.dumps({"output": "1 passed", "exit_code": 0}),
        },
    ]


def test_cross_session_episode_recall_is_project_isolated_and_deduplicated(tmp_path: Path) -> None:
    db_path = tmp_path / "mnemosyne.db"
    first = _provider(db_path, "github.com/acme/parser", "session-one")
    messages = _verified_messages()

    first.sync_turn("Verify parser", "Verified", session_id="session-one", messages=messages)
    first.sync_turn("Verify parser", "Verified", session_id="session-one", messages=messages)
    first._episode_queue.join()

    count = first._beam.conn.execute(
        "SELECT COUNT(*) FROM working_memory WHERE source = 'execution_bridge'"
    ).fetchone()[0]
    assert count == 1

    same_project = _provider(db_path, "github.com/acme/parser", "session-two")
    same_payload = json.loads(
        same_project.handle_tool_call(
            "mnemosyne_recall",
            {"query": "isolated parser workflow", "limit": 10},
        )
    )
    assert any(
        "[PROJECT:github.com/acme/parser]" in row["content"] for row in same_payload["results"]
    )

    other_project = _provider(db_path, "github.com/acme/other", "session-three")
    other_payload = json.loads(
        other_project.handle_tool_call(
            "mnemosyne_recall",
            {"query": "isolated parser workflow", "limit": 10},
        )
    )
    assert all("[PROJECT:" not in str(row.get("content") or "") for row in other_payload["results"])

    first._beam.conn.close()
    same_project._beam.conn.close()
    other_project._beam.conn.close()
