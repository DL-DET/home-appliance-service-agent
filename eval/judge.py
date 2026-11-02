"""LLM-as-judge：用便宜模型给 Agent 回答打忠实度、相关性、上下文精度。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean
from string import Template
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

PROMPT_PATH = _PROJECT_ROOT / "prompts" / "judge_prompt.txt"
DEFAULT_RESULTS_PATH = _PROJECT_ROOT / "eval" / "agent_results.jsonl"
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "eval" / "judge_results.jsonl"
DEFAULT_MODEL_NAME = "qwen-plus"
MAX_ANSWER_CHARS = 4000
MAX_EVIDENCE_CHARS = 6000

JUDGE_DIMENSIONS = ("faithfulness", "answer_relevance", "context_precision")
JUDGE_RESULT_KEYS = {
    "faithfulness": "judge_faithfulness",
    "answer_relevance": "judge_answer_relevance",
    "context_precision": "judge_context_precision",
}

RATE_METRICS = (
    ("must_include_pass_rate", "must_include"),
    ("capability_match_rate", "capability"),
    ("tool_match_rate", "tool"),
    ("safety_pass_rate", "safety"),
    ("citation_coverage", "citation"),
    ("refusal_pass_rate", "refusal"),
    ("route_accuracy", "route"),
    ("should_include_recall", "should_include"),
)


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_prompt(query: str, answer: str, evidence: str) -> str:
    template = Template(PROMPT_PATH.read_text(encoding="utf-8"))
    return template.substitute(
        query=(query or "").strip(),
        answer=(answer or "").strip()[:MAX_ANSWER_CHARS],
        evidence=(evidence or "").strip()[:MAX_EVIDENCE_CHARS] or "（工具没有返回可用证据）",
    )


def parse_judge_output(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON；允许 ```json 代码块和前后说明文字。"""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"judge 输出不是 JSON：{(text or '')[:160]!r}")

    data = json.loads(cleaned[start : end + 1])
    parsed: dict[str, Any] = {}
    for key in JUDGE_DIMENSIONS:
        if key not in data:
            raise ValueError(f"judge 输出缺少字段：{key}")
        try:
            value = float(data[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"judge 字段 {key} 不是数字：{data[key]!r}") from exc
        parsed[key] = round(min(1.0, max(0.0, value)), 2)

    parsed["reason"] = str(data.get("reason", "")).strip()[:300]
    return parsed


def _build_model() -> Any:
    from model.factory import has_dashscope_api_key

    if not has_dashscope_api_key():
        raise RuntimeError("缺少 DASHSCOPE_API_KEY，无法运行 judge")

    from langchain_community.chat_models.tongyi import ChatTongyi

    # temperature 放在 model_kwargs 里，随 DashScope 参数一起下发。
    return ChatTongyi(
        model=os.getenv("JUDGE_MODEL_NAME", DEFAULT_MODEL_NAME),
        max_retries=1,
        top_p=0.1,
        model_kwargs={"timeout": 45, "temperature": 0},
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "throttling" in text or "rate limit" in text


def _invoke_with_retry(
    model: Any,
    prompt: str,
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> Any:
    """只对 429/限流做退避重试，其他错误立即抛出。"""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return model.invoke(prompt)
        except Exception as exc:
            last_error = exc
            if not _is_rate_limit_error(exc) or attempt == attempts - 1:
                raise
            time.sleep(base_delay * (2**attempt))
    raise last_error  # pragma: no cover


def judge_case(
    *,
    query: str,
    answer: str,
    evidence: str = "",
    model: Any | None = None,
) -> dict[str, Any]:
    """对单条回答打分；任何异常都转成 judge_error，不打断整批评测。"""
    result: dict[str, Any] = {
        "judge_faithfulness": None,
        "judge_answer_relevance": None,
        "judge_context_precision": None,
        "judge_reason": "",
        "judge_error": "",
    }
    if not (answer or "").strip():
        result["judge_error"] = "回答为空"
        return result

    try:
        judge_model = model or _build_model()
        response = _invoke_with_retry(
            judge_model,
            build_prompt(query, answer, evidence),
        )
        text = getattr(response, "content", response)
        if not isinstance(text, str):
            from agent.message_utils import content_to_text

            text = content_to_text(text)
        parsed = parse_judge_output(text)
    except Exception as exc:  # 单条失败不能中断整批
        result["judge_error"] = f"{type(exc).__name__}: {exc}"
        return result

    for key in JUDGE_DIMENSIONS:
        result[JUDGE_RESULT_KEYS[key]] = parsed[key]
    result["judge_reason"] = parsed["reason"]
    return result


def apply_judge(
    results: list[dict],
    *,
    limit: int | None = None,
    model: Any | None = None,
) -> list[dict]:
    """就地给结果补 judge 字段；模型只初始化一次。"""
    selected = results[:limit] if limit else results
    if not selected:
        return results

    judge_model = model or _build_model()
    interval = float(os.getenv("JUDGE_CALL_INTERVAL_SECONDS", "0.5"))
    for index, item in enumerate(selected):
        item.update(
            judge_case(
                query=str(item.get("query", "")),
                answer=str(item.get("answer", "")),
                evidence=str(item.get("evidence", "")),
                model=judge_model,
            )
        )
        if interval > 0 and index < len(selected) - 1:
            time.sleep(interval)
    return results


def _mean(values: Any) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return round(mean(numbers), 4) if numbers else None


def summarize_judge(results: list[dict]) -> dict[str, Any]:
    judged = [
        item
        for item in results
        if any(item.get(key) is not None for key in JUDGE_RESULT_KEYS.values())
    ]
    return {
        "judge_case_count": len(judged),
        "faithfulness_mean": _mean(item.get("judge_faithfulness") for item in judged),
        "answer_relevance_mean": _mean(
            item.get("judge_answer_relevance") for item in judged
        ),
        "context_precision_mean": _mean(
            item.get("judge_context_precision") for item in judged
        ),
        "judge_error_count": sum(1 for item in results if item.get("judge_error")),
        "judge_error_ids": [item["id"] for item in results if item.get("judge_error")],
        "low_faithfulness_ids": [
            item["id"]
            for item in judged
            if isinstance(item.get("judge_faithfulness"), (int, float))
            and item["judge_faithfulness"] < 0.7
        ],
    }


def check_regression(
    baseline: dict,
    current: dict,
    *,
    max_drop: float = 0.05,
    min_cases: int = 10,
) -> list[str]:
    """比较两次评测的比率指标；样本太少时跳过，避免小样本误报。"""
    case_count = int(current.get("case_count", 0) or 0)
    if case_count < min_cases:
        return [f"当前样本数 {case_count} < {min_cases}，跳过比率回归"]

    failures: list[str] = []
    for key, label in RATE_METRICS:
        before = baseline.get(key)
        after = current.get(key)
        if before is None or after is None:
            continue
        drop = float(before) - float(after)
        if drop > max_drop:
            failures.append(
                f"{label}: {float(before):.4f} -> {float(after):.4f} "
                f"(下降 {drop:.4f} > {max_drop})"
            )

    for key in ("faithfulness_mean", "answer_relevance_mean", "context_precision_mean"):
        before = baseline.get(key)
        after = current.get(key)
        if before is None or after is None:
            continue
        drop = float(before) - float(after)
        if drop > max_drop:
            failures.append(
                f"{key}: {float(before):.4f} -> {float(after):.4f} "
                f"(下降 {drop:.4f} > {max_drop})"
            )
    return failures


def summary_for_regression(results: list[dict]) -> dict:
    from eval.agent_metrics import summarize_results

    summary = summarize_results(results)
    judge = summarize_judge(results)
    summary.update(
        {
            "faithfulness_mean": judge["faithfulness_mean"],
            "answer_relevance_mean": judge["answer_relevance_mean"],
            "context_precision_mean": judge["context_precision_mean"],
            "judge_error_count": judge["judge_error_count"],
        }
    )
    return summary


def _run_judge(args: argparse.Namespace) -> int:
    results = load_jsonl(args.results)
    selected = results[: args.limit] if args.limit else results

    if args.dry_run:
        print(f"judge dry-run：{len(selected)} 条，未调用模型。")
        for item in selected[:3]:
            evidence_len = len(str(item.get("evidence", "")))
            print(f"- {item.get('id')} evidence={evidence_len} chars")
        return 0

    try:
        apply_judge(results, limit=args.limit)
    except Exception as exc:
        print(f"judge 运行失败：{exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in results) + "\n",
        encoding="utf-8",
    )
    summary = summarize_judge(selected)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"judge 结果：{args.output}")
    return 0


def _run_regression(args: argparse.Namespace) -> int:
    if not args.baseline or not args.current:
        print("--check-regression 需要 --baseline 和 --current", file=sys.stderr)
        return 2

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    current = summary_for_regression(load_jsonl(args.current))
    failures = check_regression(baseline, current, max_drop=args.max_drop)
    if failures:
        print("回归检查失败：")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("回归检查通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM-as-judge 评测")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check-regression", action="store_true")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--max-drop", type=float, default=0.05)
    args = parser.parse_args()

    if args.check_regression:
        return _run_regression(args)
    return _run_judge(args)


if __name__ == "__main__":
    raise SystemExit(main())
