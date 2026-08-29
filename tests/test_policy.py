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


def test_durable_fact_writes_are_staged_for_foreground_approval() -> None:
    assert classify_tool("mnemosyne_remember", {}) is Decision.STAGE


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
