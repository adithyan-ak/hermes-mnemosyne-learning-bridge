import json

from mnemosyne_learning_bridge.evidence import build_episode, contains_secret


def test_trivial_successful_inspection_does_not_create_execution_episode() -> None:
    messages = [
        {"role": "user", "content": "Where am I?"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-pwd",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": json.dumps({"command": "pwd"})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-pwd",
            "tool_name": "terminal",
            "content": json.dumps({"output": "/workspace", "exit_code": 0}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="workspace:test",
            session_id="session-pwd",
            turn_id="turn-pwd",
        )
        is None
    )


def test_structured_tool_failure_creates_verified_failure_episode() -> None:
    messages = [
        {"role": "user", "content": "Apply the change."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "patch",
                    "function": {"name": "patch", "arguments": json.dumps({"path": "src/app.py"})},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "patch",
            "tool_name": "patch",
            "content": json.dumps({"success": False, "error": "target not found"}),
        },
    ]

    episode = build_episode(
        messages, project_id="github.com/acme/app", session_id="s-1", turn_id="t-1"
    )

    assert episode is not None
    assert episode.metadata["outcome"] == "failure"
    assert episode.metadata["verified"] is True


def test_later_meaningful_failure_overrides_earlier_passing_test() -> None:
    messages = [
        {"role": "user", "content": "Verify and deploy parser."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "test",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                },
                {
                    "id": "deploy",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "deploy parser"}),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "test",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 0, "output": "1 passed"}),
        },
        {
            "role": "tool",
            "tool_call_id": "deploy",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 1, "output": "failed"}),
        },
    ]

    episode = build_episode(
        messages, project_id="github.com/acme/parser", session_id="s-1", turn_id="t-1"
    )

    assert episode is not None
    assert episode.metadata["outcome"] == "failure"


def test_failed_test_is_not_overridden_by_unrelated_successful_command() -> None:
    messages = [
        {"role": "user", "content": "Verify parser."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "test",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                },
                {
                    "id": "pwd",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": json.dumps({"command": "pwd"})},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "test",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 1, "output": "failed"}),
        },
        {
            "role": "tool",
            "tool_call_id": "pwd",
            "tool_name": "terminal",
            "content": json.dumps({"exit_code": 0, "output": "/workspace"}),
        },
    ]

    episode = build_episode(
        messages,
        project_id="workspace:test",
        session_id="session-mixed",
        turn_id="turn-mixed",
    )

    assert episode is not None
    assert episode.metadata["outcome"] == "failure"


def test_pytest_success_creates_deterministically_verified_project_episode() -> None:
    messages = [
        {"role": "user", "content": "Fix the parser and verify it."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
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
            "tool_call_id": "call-1",
            "tool_name": "terminal",
            "content": json.dumps({"output": "1 passed", "exit_code": 0}),
        },
        {
            "role": "assistant",
            "content": "The parser is fixed. This entire sentence must not be stored verbatim.",
        },
    ]

    episode = build_episode(
        messages,
        project_id="github.com/example/parser",
        session_id="session-2",
        turn_id="turn-9",
    )

    assert episode is not None
    assert episode.scope == "global"
    assert episode.veracity == "tool"
    assert episode.metadata["kind"] == "execution_episode"
    assert episode.metadata["project_id"] == "github.com/example/parser"
    assert episode.metadata["verified"] is True
    assert episode.metadata["verification_evidence"][0]["kind"] == "test_result"
    assert "This entire sentence" not in episode.content
    assert "Fix the parser and verify it." not in episode.content
    assert episode.content.startswith("[PROJECT:github.com/example/parser]")


def test_failed_command_creates_tool_verified_failure_episode() -> None:
    messages = [
        {"role": "user", "content": "Run the parser tests."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-fail",
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
            "tool_call_id": "call-fail",
            "tool_name": "terminal",
            "content": json.dumps({"output": "1 failed", "exit_code": 1}),
        },
    ]

    episode = build_episode(
        messages,
        project_id="github.com/example/parser",
        session_id="session-fail",
        turn_id="turn-fail",
    )

    assert episode is not None
    assert episode.metadata["verified"] is True
    assert episode.metadata["outcome"] == "failure"
    assert episode.metadata["source_authority"] == "tool"
    assert episode.veracity == "tool"


def test_secret_bearing_turn_is_rejected_before_storage() -> None:
    messages = [
        {
            "role": "user",
            "content": "Use api_key=sk-live-abcdefghijklmnopqrstuvwxyz to verify access.",
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-secret",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest tests/test_access.py -q"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-secret",
            "tool_name": "terminal",
            "content": json.dumps({"output": "1 passed", "exit_code": 0}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="github.com/example/access",
            session_id="session-secret",
            turn_id="turn-secret",
        )
        is None
    )


def test_secret_signature_in_tool_output_is_rejected() -> None:
    messages = [
        {"role": "user", "content": "Inspect authentication."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call-token",
                    "type": "function",
                    "function": {"name": "terminal", "arguments": json.dumps({"command": "env"})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-token",
            "tool_name": "terminal",
            "content": json.dumps({"output": ("ghp_" + "a" * 32), "exit_code": 0}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="github.com/example/access",
            session_id="session-token",
            turn_id="turn-token",
        )
        is None
    )


def test_common_unlabeled_credential_signatures_are_rejected() -> None:
    values = [
        ("AKIA" + "A" * 16),
        ("ghp_" + "a" * 32),
        ("github_pat_" + "a" * 82),
        ("Bearer " + "a" * 32),
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz123456",
        "postgresql://dbuser:verysecretpassword@example.test/database",
        ("xoxb-" + "a" * 24),
    ]

    assert all(contains_secret(value) for value in values)


def test_common_environment_credential_labels_are_rejected() -> None:
    values = [
        f"OPENAI_API_KEY={'x' * 24}",
        f"AWS_SECRET_ACCESS_KEY={'x' * 24}",
        f"ANTHROPIC_API_KEY: {'x' * 24}",
        f"PRIVATE_TOKEN={'x' * 24}",
        f"PRIVATE_KEY={'x' * 24}",
        f"AZURE_STORAGE_ACCOUNT_KEY={'x' * 24}",
        f"SESSION_COOKIE={'x' * 24}",
    ]

    assert all(contains_secret(value) for value in values)


def test_additional_common_credential_signatures_are_rejected() -> None:
    values = [
        "glpat-" + "a" * 24,
        "npm_" + "b" * 36,
        "AIza" + "C" * 35,
        "-----BEGIN DSA " + "PRIVATE KEY-----",
    ]

    assert all(contains_secret(value) for value in values)


def test_unreviewed_tool_cannot_self_assert_verified_episode() -> None:
    messages = [
        {"role": "user", "content": "Do something."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "plugin-1",
                    "type": "function",
                    "function": {"name": "unreviewed_plugin", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "plugin-1",
            "tool_name": "unreviewed_plugin",
            "content": json.dumps({"verified": True, "success": True}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="workspace:test",
            session_id="session-plugin",
            turn_id="turn-plugin",
        )
        is None
    )


def test_unreviewed_tool_cannot_forge_exit_code_evidence() -> None:
    messages = [
        {"role": "user", "content": "Run tests."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "plugin-test",
                    "type": "function",
                    "function": {
                        "name": "unreviewed_plugin",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "plugin-test",
            "tool_name": "unreviewed_plugin",
            "content": json.dumps({"exit_code": 0}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="workspace:test",
            session_id="session-plugin-exit",
            turn_id="turn-plugin-exit",
        )
        is None
    )


def test_memory_mutation_tool_cannot_recursively_create_execution_episode() -> None:
    messages = [
        {"role": "user", "content": "Remember this."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "remember-1",
                    "type": "function",
                    "function": {
                        "name": "mnemosyne_remember",
                        "arguments": json.dumps({"content": "ordinary fact"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "remember-1",
            "tool_name": "mnemosyne_remember",
            "content": json.dumps({"status": "staged", "verified": True}),
        },
    ]

    assert (
        build_episode(
            messages,
            project_id="workspace:test",
            session_id="session-memory",
            turn_id="turn-memory",
        )
        is None
    )


def test_episode_records_only_skills_actually_loaded_with_versions() -> None:
    messages = [
        {"role": "user", "content": "Run the documented workflow."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "skill-1",
                    "type": "function",
                    "function": {
                        "name": "skill_view",
                        "arguments": json.dumps({"name": "test-driven-development"}),
                    },
                },
                {
                    "id": "test-1",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest -q"}),
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "skill-1",
            "tool_name": "skill_view",
            "content": json.dumps(
                {"name": "test-driven-development", "content": "---\nversion: 1.1.0\n---\n# TDD"}
            ),
        },
        {
            "role": "tool",
            "tool_call_id": "test-1",
            "tool_name": "terminal",
            "content": json.dumps({"output": "12 passed", "exit_code": 0}),
        },
    ]

    episode = build_episode(
        messages,
        project_id="github.com/example/workflow",
        session_id="session-skill",
        turn_id="turn-skill",
    )

    assert episode is not None
    assert episode.metadata["skills_used"] == ["test-driven-development"]
    assert episode.metadata["skill_versions"] == {"test-driven-development": "1.1.0"}


def test_episode_records_only_touched_file_names_without_local_paths() -> None:
    messages = [
        {"role": "user", "content": "Update the configuration."},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "path": "/workspace/config.toml",
                                "content": "private body not retained",
                            }
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "write-1",
            "tool_name": "write_file",
            "content": json.dumps({"verified": True, "resolved_path": "/workspace/config.toml"}),
        },
    ]

    episode = build_episode(
        messages,
        project_id="workspace:project:abc123",
        session_id="session-file",
        turn_id="turn-file",
    )

    assert episode is not None
    assert episode.metadata["files"] == ["config.toml"]
    serialized_metadata = json.dumps(episode.metadata)
    assert "/workspace" not in serialized_metadata
    assert "session-file" not in serialized_metadata
    assert "turn-file" not in serialized_metadata
    assert "private body not retained" not in episode.content
    assert "private body not retained" not in serialized_metadata
