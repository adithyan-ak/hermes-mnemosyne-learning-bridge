from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

RecallFunction = Callable[[str, int], list[dict[str, Any]]]


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def run_recall_benchmark(
    cases: Iterable[dict[str, Any]],
    *,
    recall: RecallFunction,
    limit: int = 5,
) -> dict[str, Any]:
    case_list = list(cases)
    details: list[dict[str, Any]] = []
    positive_count = top_1_hits = top_5_hits = 0
    expected_count = expected_hits = 0
    negative_count = negative_injections = 0
    duplicate_result_count = stale_result_count = cross_scope_leakage = 0
    injected_characters = 0
    categories: Counter[str] = Counter()

    for case in case_list:
        category = str(case.get("category") or "uncategorized")
        categories[category] += 1
        rows = list(recall(str(case.get("query") or ""), limit))[:limit]
        ids = [str(row.get("id") or "") for row in rows]
        injected_characters += sum(len(str(row.get("content") or "")) for row in rows)
        duplicate_result_count += len(ids) - len(set(ids))
        stale_result_count += sum(1 for row in rows if row.get("valid_until"))

        expected_id = str(case.get("expected_id") or "")
        if expected_id:
            expected_count += 1
            expected_hits += int(expected_id in ids)
        if category == "positive":
            positive_count += 1
            top_1_hits += int(bool(ids) and ids[0] == expected_id)
            top_5_hits += int(expected_id in ids[:5])
        if category == "negative" or case.get("expect_match") is False:
            negative_count += 1
            negative_injections += int(bool(rows))

        expected_project = str(case.get("project_id") or "")
        if expected_project:
            marker = f"[PROJECT:{expected_project}]"
            cross_scope_leakage += sum(
                1
                for row in rows
                if "[PROJECT:" in str(row.get("content") or "")
                and marker not in str(row.get("content") or "")
            )
        details.append(
            {
                "category": category,
                "query": case.get("query"),
                "expected_id": expected_id or None,
                "result_ids": ids,
            }
        )

    return {
        "case_count": len(case_list),
        "category_counts": dict(sorted(categories.items())),
        "top_1_hit_rate": _rate(top_1_hits, positive_count),
        "top_5_hit_rate": _rate(top_5_hits, positive_count),
        "expected_memory_hit_rate": _rate(expected_hits, expected_count),
        "unrelated_injection_rate": _rate(negative_injections, negative_count),
        "cross_scope_leakage_count": cross_scope_leakage,
        "duplicate_result_count": duplicate_result_count,
        "stale_memory_result_count": stale_result_count,
        "average_injected_characters": round(injected_characters / len(case_list), 2)
        if case_list
        else 0.0,
        "cases": details,
    }
