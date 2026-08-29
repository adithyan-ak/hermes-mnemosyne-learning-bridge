from mnemosyne_learning_bridge.benchmark import run_recall_benchmark


def test_recall_benchmark_reports_hits_negatives_duplicates_and_context_size() -> None:
    responses = {
        "positive one": [{"id": "m1", "content": "alpha"}],
        "positive two": [{"id": "other", "content": "beta"}, {"id": "m2", "content": "target"}],
        "unrelated": [],
        "duplicate": [{"id": "m3", "content": "same"}, {"id": "m3", "content": "same"}],
    }
    cases = [
        {"category": "positive", "query": "positive one", "expected_id": "m1"},
        {"category": "positive", "query": "positive two", "expected_id": "m2"},
        {"category": "negative", "query": "unrelated", "expect_match": False},
        {"category": "exact", "query": "duplicate", "expected_id": "m3"},
    ]

    report = run_recall_benchmark(cases, recall=lambda query, limit: responses[query])

    assert report["case_count"] == 4
    assert report["top_1_hit_rate"] == 0.5
    assert report["top_5_hit_rate"] == 1.0
    assert report["unrelated_injection_rate"] == 0.0
    assert report["duplicate_result_count"] == 1
    assert report["average_injected_characters"] == 5.75
