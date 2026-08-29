import json

from mnemosyne_learning_bridge.filtering import (
    filter_prefetch_text,
    filter_recall_payload,
)


def test_recall_filters_execution_episodes_by_project_but_keeps_user_facts() -> None:
    payload = {
        "results": [
            {
                "id": "same",
                "content": "[PROJECT:github.com/acme/app] fixed parser",
                "metadata": {"kind": "execution_episode", "project_id": "github.com/acme/app"},
            },
            {
                "id": "other",
                "content": "[PROJECT:github.com/acme/other] fixed parser",
                "metadata": {"kind": "execution_episode", "project_id": "github.com/acme/other"},
            },
            {
                "id": "fact",
                "content": "The user prefers concise technical answers",
                "metadata": {"kind": "preference"},
            },
        ]
    }

    filtered = json.loads(filter_recall_payload(json.dumps(payload), "github.com/acme/app"))

    assert [item["id"] for item in filtered["results"]] == ["same", "fact"]


def test_recall_filter_drops_conflicting_project_id_sources() -> None:
    payload = {
        "results": [
            {
                "id": "conflict",
                "content": "[PROJECT:project-b] foreign episode",
                "metadata": {
                    "kind": "execution_episode",
                    "project_id": "project-a",
                },
            }
        ]
    }

    filtered = json.loads(filter_recall_payload(payload, "project-a"))

    assert filtered["results"] == []


def test_recall_filter_drops_multiple_conflicting_inline_markers() -> None:
    payload = {
        "results": [
            {
                "id": "conflict",
                "content": "[PROJECT:project-a] safe [PROJECT:project-b] foreign",
                "metadata": {
                    "kind": "execution_episode",
                    "project_id": "project-a",
                },
            }
        ]
    }

    filtered = json.loads(filter_recall_payload(payload, "project-a"))

    assert filtered["results"] == []


def test_recall_filter_recurses_through_unknown_result_shapes() -> None:
    nested = {
        "unexpected": {
            "items": [
                {
                    "id": "foreign",
                    "content": "[PROJECT:project-b] foreign episode",
                    "metadata": {"kind": "execution_episode", "project_id": "project-b"},
                },
                {
                    "id": "same",
                    "content": "[PROJECT:project-a] same episode",
                    "metadata": {"kind": "execution_episode", "project_id": "project-a"},
                },
            ]
        }
    }

    payload = json.loads(filter_recall_payload(nested, "project-a"))

    assert [row["id"] for row in payload["unexpected"]["items"]] == ["same"]


def test_prefetch_filter_drops_entire_multiline_foreign_episode_block() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [2026-08-27] [PROJECT:github.com/other/repo] foreign summary\n"
        "    foreign continuation must not leak\n"
        "  [2026-08-27] ordinary global preference\n"
    )

    filtered = filter_prefetch_text(text, "github.com/acme/app")

    assert "foreign summary" not in filtered
    assert "foreign continuation" not in filtered
    assert "ordinary global preference" in filtered


def test_prefetch_removes_foreign_project_episode_lines() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [2026-08-26] [PROJECT:github.com/acme/app] fixed parser\n"
        "  [2026-08-26] [PROJECT:github.com/acme/other] fixed parser\n"
        "  [2026-08-26] The user prefers concise technical answers"
    )

    filtered = filter_prefetch_text(text, "github.com/acme/app")

    assert "github.com/acme/app" in filtered
    assert "github.com/acme/other" not in filtered
    assert "The user prefers concise" in filtered


def test_prefetch_drops_line_with_multiple_conflicting_markers() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [PROJECT:project-a] safe [PROJECT:project-b] foreign\n"
        "  ordinary global preference"
    )

    filtered = filter_prefetch_text(text, "project-a")

    assert "foreign" not in filtered
    assert "ordinary global preference" in filtered


def test_prefetch_drops_complete_block_when_continuation_marker_conflicts() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [PROJECT:project-a] foreign-secret-before-conflict\n"
        "    [PROJECT:project-b] conflicting continuation\n"
        "  ordinary global preference"
    )

    filtered = filter_prefetch_text(text, "project-a")

    assert "foreign-secret-before-conflict" not in filtered
    assert "conflicting continuation" not in filtered
    assert "ordinary global preference" in filtered


def test_prefetch_indented_header_cannot_split_conflicting_entry() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [PROJECT:project-a] foreign-secret-before-conflict\n"
        "    ## Mnemosyne Context\n"
        "      [PROJECT:project-b] conflicting nested marker\n"
        "  ordinary global preference"
    )

    filtered = filter_prefetch_text(text, "project-a")

    assert "foreign-secret-before-conflict" not in filtered
    assert "conflicting nested marker" not in filtered
    assert "ordinary global preference" in filtered


def test_prefetch_mixed_space_tab_continuation_stays_in_same_block() -> None:
    text = (
        "## Mnemosyne Context\n"
        "  [PROJECT:project-a] foreign-secret-before-conflict\n"
        " \t[PROJECT:project-b] conflicting mixed-indent marker\n"
        "  ordinary global preference"
    )

    filtered = filter_prefetch_text(text, "project-a")

    assert "foreign-secret-before-conflict" not in filtered
    assert "conflicting mixed-indent marker" not in filtered
    assert "ordinary global preference" in filtered
