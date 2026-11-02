"""judge 纯函数测试：不调用真实模型，不需要 API Key。"""

from __future__ import annotations

import csv

import pytest
from langchain_core.messages import AIMessage

from eval.calibrate_judge import compute_agreement, write_calibration_csv
from eval.judge import (
    _invoke_with_retry,
    build_prompt,
    check_regression,
    judge_case,
    parse_judge_output,
    summarize_judge,
)

VALID_JSON = (
    '{"faithfulness": 0.9, "answer_relevance": 0.8, '
    '"context_precision": 0.7, "reason": "ok"}'
)


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    def invoke(self, prompt: str) -> AIMessage:
        self.prompts.append(prompt)
        return AIMessage(content=self.content)


def test_parse_judge_output_valid() -> None:
    parsed = parse_judge_output(VALID_JSON)
    assert parsed == {
        "faithfulness": 0.9,
        "answer_relevance": 0.8,
        "context_precision": 0.7,
        "reason": "ok",
    }


def test_parse_judge_output_code_fence() -> None:
    parsed = parse_judge_output(f"```json\n{VALID_JSON}\n```")
    assert parsed["faithfulness"] == 0.9


def test_parse_judge_output_clamps_range() -> None:
    parsed = parse_judge_output(
        '{"faithfulness": 1.5, "answer_relevance": -0.2, '
        '"context_precision": 0.5, "reason": "x"}'
    )
    assert parsed["faithfulness"] == 1.0
    assert parsed["answer_relevance"] == 0.0


def test_parse_judge_output_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        parse_judge_output("这不是 JSON")


def test_parse_judge_output_rejects_missing_field() -> None:
    with pytest.raises(ValueError):
        parse_judge_output('{"faithfulness": 0.9}')


def test_build_prompt_replaces_placeholders() -> None:
    prompt = build_prompt("问题", "回答", "证据")
    assert "问题" in prompt
    assert "回答" in prompt
    assert "证据" in prompt
    assert "$query" not in prompt


def test_judge_case_with_fake_model() -> None:
    model = FakeModel(VALID_JSON)
    result = judge_case(query="q", answer="a", evidence="e", model=model)
    assert result["judge_faithfulness"] == 0.9
    assert result["judge_answer_relevance"] == 0.8
    assert result["judge_context_precision"] == 0.7
    assert result["judge_reason"] == "ok"
    assert result["judge_error"] == ""
    assert len(model.prompts) == 1


def test_judge_case_empty_answer_skips_model() -> None:
    model = FakeModel(VALID_JSON)
    result = judge_case(query="q", answer="", evidence="e", model=model)
    assert result["judge_error"] == "回答为空"
    assert model.prompts == []


def test_invoke_retries_rate_limit(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("eval.judge.time.sleep", lambda seconds: sleeps.append(seconds))

    class FlakyModel:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, prompt: str) -> AIMessage:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("429 Throttling.RateQuota")
            return AIMessage(content=VALID_JSON)

    model = FlakyModel()
    response = _invoke_with_retry(model, "prompt", attempts=2, base_delay=1.0)
    assert response.content == VALID_JSON
    assert model.calls == 2
    assert sleeps == [1.0]


def test_judge_case_model_error_is_recorded() -> None:
    class BrokenModel:
        def invoke(self, prompt: str) -> AIMessage:
            raise RuntimeError("boom")

    result = judge_case(query="q", answer="a", evidence="e", model=BrokenModel())
    assert "RuntimeError" in result["judge_error"]
    assert result["judge_faithfulness"] is None


def test_summarize_judge() -> None:
    results = [
        {
            "id": "a",
            "judge_faithfulness": 0.9,
            "judge_answer_relevance": 0.8,
            "judge_context_precision": 0.7,
            "judge_error": "",
        },
        {
            "id": "b",
            "judge_faithfulness": 0.5,
            "judge_answer_relevance": 0.6,
            "judge_context_precision": 0.5,
            "judge_error": "",
        },
        {"id": "c", "judge_error": "bad output"},
    ]
    summary = summarize_judge(results)
    assert summary["judge_case_count"] == 2
    assert summary["faithfulness_mean"] == 0.7
    assert summary["low_faithfulness_ids"] == ["b"]
    assert summary["judge_error_ids"] == ["c"]


def test_check_regression_detects_metric_drop() -> None:
    failures = check_regression(
        {"case_count": 30, "citation_coverage": 0.8},
        {"case_count": 30, "citation_coverage": 0.5},
        max_drop=0.05,
    )
    assert any("citation" in item for item in failures)


def test_check_regression_skips_small_sample() -> None:
    failures = check_regression(
        {"citation_coverage": 0.8},
        {"case_count": 5, "citation_coverage": 0.0},
    )
    assert failures == ["当前样本数 5 < 10，跳过比率回归"]


def test_write_calibration_csv(tmp_path) -> None:
    results = [
        {
            "id": "a",
            "category": "knowledge",
            "query": "q",
            "answer": "a",
            "evidence": "e",
            "judge_faithfulness": 0.9,
            "judge_answer_relevance": 0.8,
            "judge_context_precision": 0.7,
            "judge_reason": "ok",
        },
        {
            "id": "b",
            "category": "order",
            "query": "q2",
            "answer": "a2",
            "evidence": "",
            "judge_faithfulness": 0.6,
            "judge_answer_relevance": 0.7,
            "judge_context_precision": 0.5,
            "judge_reason": "x",
        },
    ]
    path = tmp_path / "calibration.csv"
    count = write_calibration_csv(results, path, n=2, seed=1)
    assert count == 2
    text = path.read_text(encoding="utf-8-sig")
    assert "human_faithfulness" in text
    assert "judge_answer_relevance" in text


def test_compute_agreement(tmp_path) -> None:
    path = tmp_path / "calibration.csv"
    fieldnames = [
        "id",
        "judge_faithfulness",
        "human_faithfulness",
        "judge_answer_relevance",
        "human_answer_relevance",
        "judge_context_precision",
        "human_context_precision",
    ]
    rows = [
        {
            "id": "a",
            "judge_faithfulness": "0.9",
            "human_faithfulness": "1",
            "judge_answer_relevance": "0.8",
            "human_answer_relevance": "1",
            "judge_context_precision": "0.2",
            "human_context_precision": "0",
        },
        {
            "id": "b",
            "judge_faithfulness": "0.4",
            "human_faithfulness": "1",
            "judge_answer_relevance": "0.9",
            "human_answer_relevance": "1",
            "judge_context_precision": "0.8",
            "human_context_precision": "1",
        },
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    overall, rates, total = compute_agreement(path)
    assert total == 6
    assert rates["faithfulness"] == 0.5
    assert rates["answer_relevance"] == 1.0
    assert rates["context_precision"] == 1.0
    assert overall == pytest.approx(5 / 6, rel=1e-4)
