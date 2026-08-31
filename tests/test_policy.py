import mnemosyne_hermes

from mnemosyne_learning_bridge.policy import Decision, classify_tool


def _base_tool_names() -> set[str]:
    return {
        schema["name"] for schema in mnemosyne_hermes.MnemosyneMemoryProvider().get_tool_schemas()
    }


def test_every_installed_mnemosyne_tool_has_an_explicit_policy() -> None:
    decisions = {name: classify_tool(name, {}) for name in _base_tool_names()}

    assert decisions
    assert Decision.UNKNOWN not in decisions.values()


def test_direct_stated_user_memory_is_written_without_second_approval() -> None:
    assert (
        classify_tool(
            "mnemosyne_remember",
            {
                "content": "Telegram is the user's primary Hermes channel",
                "scope": "global",
                "source": "user",
                "veracity": "stated",
                "extract": False,
                "extract_entities": False,
            },
        )
        is Decision.DIRECT
    )


def test_ambiguous_or_inferred_memory_is_not_treated_as_direct() -> None:
    assert classify_tool("mnemosyne_remember", {}) is Decision.BLOCK
    assert (
        classify_tool(
            "mnemosyne_remember",
            {
                "content": "The user may prefer Telegram",
                "scope": "global",
                "source": "assistant",
                "veracity": "inferred",
            },
        )
        is Decision.BLOCK
    )


def test_dry_run_mutations_are_read_only_but_unsupported_live_mutations_block() -> None:
    assert classify_tool("mnemosyne_batch", {"dry_run": True}) is Decision.READ
    assert (
        classify_tool("mnemosyne_batch", {"operations": [{"action": "remember", "content": "x"}]})
        is Decision.BLOCK
    )
    assert classify_tool("mnemosyne_sleep", {"dry_run": True}) is Decision.READ
    assert classify_tool("mnemosyne_sleep", {"force": True}) is Decision.BLOCK
    assert (
        classify_tool("mnemosyne_diagnose", {"repair_vec_working": True, "dry_run": True})
        is Decision.READ
    )
    assert classify_tool("mnemosyne_diagnose", {"repair_vec_working": True}) is Decision.BLOCK
