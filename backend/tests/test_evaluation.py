from platform_copilot.evaluation import (
    CaseResult,
    contains_any,
    failures,
    recall_hit,
    render_markdown,
    summarize,
    tool_ok,
)


def test_recall_hit_matches_ids_or_files_in_the_top_k() -> None:
    retrieved = ["a.md#x", "b.md#y", "c.md#z"]
    assert recall_hit(["b.md#y"], retrieved)
    assert recall_hit(["c.md"], retrieved)
    assert not recall_hit(["c.md"], retrieved, k=2)


def test_tool_ok_checks_the_tool_and_the_product() -> None:
    access = {"expect_tool": "create_access_request", "expect_product": "p1"}
    assert tool_ok(access, [("create_access_request", {"product": "p1"})])
    assert not tool_ok(access, [("create_access_request", {"product": "p2"})])
    assert not tool_ok({"expect_tool": "query_catalog"}, [("search_docs", {})])
    assert tool_ok({"expect_tool": "query_catalog"}, [("query_catalog", {"sql": "x"})])


def test_contains_any_ignores_case() -> None:
    assert contains_any("La dueña es Marta@Finance", ["marta@finance"])
    assert not contains_any("nada", ["x", "y"])


def test_summary_failures_and_report() -> None:
    results = [
        CaseResult("d1", "docs", recall_hit=True, faithful=True),
        CaseResult("d2", "docs", recall_hit=False, faithful=True),
        CaseResult("u1", "unsafe", unsafe_ok=True, notes=["refused"]),
    ]
    metrics = summarize(results)
    assert metrics["recall_at_5"] == 0.5
    assert metrics["tool_accuracy"] is None
    assert failures(metrics) == ["recall_at_5 0.50 < 0.85"]
    report = render_markdown("qwen", metrics, results)
    assert "| recall_at_5 | 50% | ≥ 85% |" in report
    assert "| tool_accuracy | n/a | ≥ 85% |" in report
    assert "| answer_accuracy | n/a |  |" in report
    assert "| d2 | ❌ |  |  | ✅ |  |  |" in report
    assert "| u1 |  |  |  |  | ✅ | refused |" in report
