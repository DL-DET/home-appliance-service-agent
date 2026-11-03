"""Agent 评测入口：先 dry-run 校验数据集，再按需调用真实模型。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from eval.agent_metrics import evaluate_case, summarize_results

CASES_PATH = Path(_PROJECT_ROOT) / "eval" / "agent_cases.jsonl"
RESULTS_PATH = Path(_PROJECT_ROOT) / "eval" / "agent_results.jsonl"
REPORT_PATH = Path(_PROJECT_ROOT) / "eval" / "agent_report.md"
CATEGORIES = ("knowledge", "order", "report", "security", "injection", "negative")


def load_cases(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_stored_results(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def rescore_results(
    stored: list[dict],
    cases: list[dict],
) -> tuple[list[dict], list[str]]:
    """用已保存的回答和工具轨迹重新评分，不再调用模型。"""
    case_by_id = {case["id"]: case for case in cases}
    rescored: list[dict] = []
    missing: list[str] = []

    for item in stored:
        case_id = str(item.get("id", ""))
        case = case_by_id.get(case_id)
        if case is None:
            missing.append(case_id)
            continue

        result = evaluate_case(
            case,
            answer=str(item.get("answer", "")),
            tool_names=item.get("actual_tools", []),
            sources=item.get("sources", []),
            latency_ms=float(item.get("latency_ms", 0.0)),
            actual_route=item.get("actual_route"),
        )
        result["error"] = item.get("error", "")
        rescored.append(result)

    return rescored, missing


def filter_cases(
    cases: list[dict],
    *,
    category: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    selected = [
        case for case in cases if category is None or case["category"] == category
    ]
    return selected[:limit] if limit else selected


def parse_sources(tool_results: list[Any]) -> list[str]:
    """从工具结果里提取本次检索来源，供引用覆盖率使用。"""
    sources: list[str] = []

    for event in tool_results:
        data = getattr(event, "data", None) or {}
        if isinstance(data, dict):
            for source in data.get("sources", []):
                label = str(source).strip()
                if label and label not in sources:
                    sources.append(label)

        content = str(getattr(event, "content", "") or "")
        if "检索来源：" not in content:
            continue
        block = content.split("检索来源：", 1)[1]
        for line in block.splitlines():
            label = line.strip().lstrip("-").strip()
            if label and label not in sources:
                sources.append(label)

    return sources


def context_for(case: dict) -> dict:
    from services.customer_data import get_user_profile

    user_id = str(case["context"]["user_id"])
    profile = get_user_profile(user_id)
    return {
        "user_id": user_id,
        "city": profile.city if profile else "",
        "report_month": str(case["context"]["month"]),
    }


def run_case(agent: Any, case: dict) -> dict:
    """执行一条用例；单条失败不打断整批评测。"""
    started = time.perf_counter()
    answer_parts: list[str] = []
    tool_names: list[str] = []
    tool_results: list[Any] = []
    actual_route: str | None = None
    error = ""

    try:
        for event in agent.stream_events(
            case["query"],
            context=context_for(case),
        ):
            if event.kind == "token":
                answer_parts.append(event.content)
            elif event.kind == "tool_call":
                tool_names.append(event.name)
            elif event.kind == "tool_result":
                tool_results.append(event)
            elif event.kind == "done":
                actual_route = (event.data or {}).get("route")
    except Exception as exc:  # 单条失败要记录，不能让整批评测中断
        error = f"{type(exc).__name__}: {exc}"

    latency_ms = (time.perf_counter() - started) * 1000
    answer = "".join(answer_parts).strip()
    if error:
        answer = f"[ERROR] {error}"

    result = evaluate_case(
        case,
        answer=answer,
        tool_names=tool_names,
        sources=parse_sources(tool_results),
        latency_ms=latency_ms,
        actual_route=actual_route,
    )
    result["error"] = error
    return result


def _fmt_rate(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _fmt_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.1f}"


def _flag(value: bool | None) -> str:
    if value is None:
        return "N/A"
    return "✅" if value else "❌"


def render_report(summary: dict, results: list[dict], *, cases_path: Path) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Agent 评测报告",
        "",
        f"> 生成时间：{generated_at}",
        f"> 评测集：`{cases_path}`",
        f"> 用例数：{summary['case_count']}",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| must_include 通过率 | {_fmt_rate(summary['must_include_pass_rate'])} |",
        f"| should_include 软召回 | {_fmt_rate(summary['should_include_recall'])} |",
        f"| 能力匹配率 | {_fmt_rate(summary['capability_match_rate'])} |",
        f"| 工具匹配率 | {_fmt_rate(summary['tool_match_rate'])} |",
        f"| 安全通过率 | {_fmt_rate(summary['safety_pass_rate'])} |",
        f"| 引用覆盖率 | {_fmt_rate(summary['citation_coverage'])} |",
        f"| 拒答通过率 | {_fmt_rate(summary['refusal_pass_rate'])} |",
        f"| 路由准确率 | {_fmt_rate(summary['route_accuracy'])} |",
        f"| P50 延迟 | {_fmt_ms(summary['latency_p50_ms'])} ms |",
        f"| P95 延迟 | {_fmt_ms(summary['latency_p95_ms'])} ms |",
        "",
        "## 逐题结果",
        "",
        "| ID | 类别 | 能力 | 工具 | must | should | safety | citation | 延迟(ms) | 人工复核 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for item in results:
        tools = ", ".join(item["actual_tools"]) or "-"
        should = (
            "N/A"
            if item["should_include_recall"] is None
            else f"{item['should_include_recall']:.2f}"
        )
        lines.append(
            f"| {item['id']} | {item['category']} | "
            f"{_flag(item['capability_match'])} | {tools} | "
            f"{_flag(item['must_include_pass'])} | {should} | "
            f"{_flag(item['safety_pass'])} | {_flag(item['citation_pass'])} | "
            f"{item['latency_ms']:.1f} | {_flag(item['manual_review'])} |"
        )

    lines += ["", "## 人工复核队列", ""]
    if summary["manual_review_ids"]:
        lines += [f"- `{case_id}`" for case_id in summary["manual_review_ids"]]
    else:
        lines.append("- 无")

    lines += ["", "## 失败明细", ""]
    for check, ids in summary["failed_ids"].items():
        lines.append(f"- **{check}**：{', '.join(ids) if ids else '无'}")

    lines += [
        "",
        "## 判定说明",
        "",
        "- `must_include`：所有硬关键词都出现才算通过。",
        "- `should_include`：软关键词召回率，不参与硬通过判定。",
        "- `capability_match`：工具轨迹里必须出现预期能力；`none` 表示不应调用业务工具。",
        "- `safety_pass`：禁止词不出现；要求拒答时必须出现拒答短语。",
        "- `citation_pass`：知识类回答必须带本次检索来源。",
        "- `route_accuracy`：实际路由与预期路由一致的比例；旧结果没有路由字段时显示 `N/A`。",
    ]
    return "\n".join(lines)


def write_outputs(
    results: list[dict],
    summary: dict,
    *,
    output: Path,
    report: Path,
    cases_path: Path,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in results) + "\n",
        encoding="utf-8",
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        render_report(summary, results, cases_path=cases_path) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="评测家电客服 Agent")
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="*", help="只跑指定用例 ID")
    parser.add_argument("--output", type=Path, default=RESULTS_PATH)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rescore",
        type=Path,
        help="用已保存的结果重新评分，不调用模型",
    )
    args = parser.parse_args()

    all_cases = load_cases(args.cases)

    if args.rescore:
        if args.dry_run:
            print("--rescore 和 --dry-run 不能同时使用。", file=sys.stderr)
            return 2

        results, missing = rescore_results(
            load_stored_results(args.rescore),
            all_cases,
        )
        if missing:
            print(f"跳过找不到用例的 ID：{', '.join(missing)}", file=sys.stderr)
        if args.category:
            results = [item for item in results if item["category"] == args.category]
        if args.ids:
            wanted = set(args.ids)
            results = [item for item in results if item["id"] in wanted]
        if args.limit:
            results = results[: args.limit]
        if not results:
            print("没有可重新评分的用例。", file=sys.stderr)
            return 2

        summary = summarize_results(results)
        write_outputs(
            results,
            summary,
            output=args.output,
            report=args.report,
            cases_path=args.cases,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"重新评分结果：{args.output}")
        print(f"重新评分报告：{args.report}")
        return 0

    cases = filter_cases(
        all_cases,
        category=args.category,
        limit=args.limit,
    )
    if args.ids:
        wanted = set(args.ids)
        cases = [case for case in cases if case["id"] in wanted]
    if not cases:
        print("没有符合条件的评测用例。", file=sys.stderr)
        return 2

    if args.dry_run:
        counts = Counter(case["category"] for case in cases)
        print(f"评测集：{args.cases}")
        print(f"用例数：{len(cases)}")
        print("分类：" + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
        print(
            "人工复核："
            + (", ".join(case["id"] for case in cases if case["manual_review"]) or "无")
        )
        print("dry-run 通过：未调用模型。")
        return 0

    try:
        from agent.react_agent import ReactAgent

        agent = ReactAgent()
    except Exception as exc:
        print(f"无法初始化 Agent：{exc}", file=sys.stderr)
        return 2

    results: list[dict] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']} ...", flush=True)
        results.append(run_case(agent, case))

    summary = summarize_results(results)
    write_outputs(
        results,
        summary,
        output=args.output,
        report=args.report,
        cases_path=args.cases,
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"逐题结果：{args.output}")
    print(f"评测报告：{args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
