"""评测入口的纯函数测试，不需要 API Key。"""

from types import SimpleNamespace

from eval.run_agent_eval import (
    filter_cases,
    parse_sources,
    render_report,
    rescore_results,
)


def test_filter_cases_by_category_and_limit():
    cases = [
        {"id": "a", "category": "knowledge"},
        {"id": "b", "category": "order"},
        {"id": "c", "category": "knowledge"},
    ]

    assert [case["id"] for case in filter_cases(cases, category="knowledge")] == ["a", "c"]
    assert [case["id"] for case in filter_cases(cases, limit=2)] == ["a", "b"]


def test_parse_sources_reads_bullets_and_event_data():
    events = [
        SimpleNamespace(
            content="总结\n\n检索来源：\n- 故障排除.txt\n- 选购指南.txt",
            data={},
        ),
        SimpleNamespace(
            content="检索到 1 条资料",
            data={"sources": ["故障排除.txt", "维护保养.txt"]},
        ),
    ]

    assert parse_sources(events) == [
        "故障排除.txt",
        "选购指南.txt",
        "维护保养.txt",
    ]


def test_rescore_results_reuses_stored_answer():
    cases = [
        {
            "id": "k1",
            "query": "主刷不转",
            "category": "knowledge",
            "expected_route": "knowledge",
            "expected_capability": "rag",
            "expected_tools": ["rag_summarize"],
            "must_include": ["主刷", "异物"],
            "should_include": [],
            "must_not_include": [],
            "citation_required": True,
            "refusal_required": False,
            "manual_review": False,
            "notes": "",
        }
    ]
    stored = [
        {
            "id": "k1",
            "answer": "主刷被异物卡住。参考资料：故障排除.txt",
            "actual_tools": ["rag_summarize"],
            "sources": ["故障排除.txt"],
            "latency_ms": 10.0,
            "actual_route": "knowledge",
            "error": "",
        }
    ]

    results, missing = rescore_results(stored, cases)

    assert missing == []
    assert results[0]["must_include_pass"]
    assert results[0]["citation_pass"]
    assert results[0]["route_match"]


def test_rescore_results_reports_missing_case_ids():
    results, missing = rescore_results([{"id": "missing"}], [])

    assert results == []
    assert missing == ["missing"]


def test_render_report_contains_summary_and_cases():
    summary = {
        "case_count": 1,
        "must_include_pass_rate": 1.0,
        "should_include_recall": None,
        "capability_match_rate": 1.0,
        "tool_match_rate": 1.0,
        "safety_pass_rate": 1.0,
        "citation_coverage": 1.0,
        "refusal_pass_rate": None,
        "route_accuracy": None,
        "latency_p50_ms": 100.0,
        "latency_p95_ms": 100.0,
        "manual_review_ids": [],
        "failed_ids": {"must_include": [], "safety": []},
    }
    results = [
        {
            "id": "case-1",
            "category": "knowledge",
            "actual_tools": ["rag_summarize"],
            "capability_match": True,
            "must_include_pass": True,
            "should_include_recall": None,
            "safety_pass": True,
            "citation_pass": True,
            "latency_ms": 100.0,
            "manual_review": False,
        }
    ]

    report = render_report(summary, results, cases_path="eval/agent_cases.jsonl")

    assert "# Agent 评测报告" in report
    assert "case-1" in report
    assert "must_include 通过率" in report
