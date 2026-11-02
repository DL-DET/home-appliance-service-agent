"""生成 judge 人工校准表，并计算 judge 与人工标注的一致率。"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval.judge import JUDGE_DIMENSIONS, load_jsonl

DEFAULT_RESULTS_PATH = _PROJECT_ROOT / "eval" / "judge_results.jsonl"
DEFAULT_CSV_PATH = _PROJECT_ROOT / "eval" / "judge_calibration.csv"
HUMAN_COLUMNS = [f"human_{dimension}" for dimension in JUDGE_DIMENSIONS]


def sample_results(results: list[dict], *, n: int = 20, seed: int = 42) -> list[dict]:
    """按类别轮询抽样，避免 20 条全是知识题。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in results:
        groups[str(item.get("category", "unknown"))].append(item)

    rng = random.Random(seed)
    for items in groups.values():
        rng.shuffle(items)

    selected: list[dict] = []
    while len(selected) < n:
        added = False
        for category in sorted(groups):
            if groups[category] and len(selected) < n:
                selected.append(groups[category].pop(0))
                added = True
        if not added:
            break
    return selected


def write_calibration_csv(
    results: list[dict],
    path: Path,
    *,
    n: int = 20,
    seed: int = 42,
) -> int:
    judged = [
        item
        for item in results
        if any(item.get(f"judge_{dimension}") is not None for dimension in JUDGE_DIMENSIONS)
    ]
    if not judged:
        raise ValueError("结果里没有 judge 分数；先运行 python -m eval.judge 生成 judge_results.jsonl")

    selected = sample_results(judged, n=n, seed=seed)
    fieldnames = [
        "id",
        "category",
        "query",
        "answer",
        "evidence_preview",
        *[f"judge_{dimension}" for dimension in JUDGE_DIMENSIONS],
        "judge_reason",
        *HUMAN_COLUMNS,
    ]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in selected:
            row = {
                "id": item.get("id", ""),
                "category": item.get("category", ""),
                "query": item.get("query", ""),
                "answer": item.get("answer", ""),
                "evidence_preview": str(item.get("evidence", ""))[:300].replace("\n", " "),
                "judge_reason": item.get("judge_reason", ""),
            }
            for dimension in JUDGE_DIMENSIONS:
                row[f"judge_{dimension}"] = item.get(f"judge_{dimension}", "")
                row[f"human_{dimension}"] = ""
            writer.writerow(row)
    return len(selected)


def compute_agreement(
    path: Path,
    *,
    judge_threshold: float = 0.7,
) -> tuple[float, dict[str, float | None], int]:
    """人工列填 0/1；judge >= 阈值视为 1，计算逐维度和总体一致率。"""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    per_dimension = {
        dimension: {"matched": 0, "total": 0} for dimension in JUDGE_DIMENSIONS
    }
    for row in rows:
        for dimension in JUDGE_DIMENSIONS:
            human_raw = str(row.get(f"human_{dimension}", "") or "").strip()
            if human_raw not in {"0", "1"}:
                continue
            judge_raw = str(row.get(f"judge_{dimension}", "") or "").strip()
            if not judge_raw:
                continue
            try:
                judge_value = float(judge_raw)
            except ValueError:
                continue

            human_value = int(human_raw)
            predicted = 1 if judge_value >= judge_threshold else 0
            per_dimension[dimension]["total"] += 1
            if predicted == human_value:
                per_dimension[dimension]["matched"] += 1

    total = sum(item["total"] for item in per_dimension.values())
    if total == 0:
        raise ValueError("没有可比较的人工标注；请在 human_* 列填写 0 或 1")

    matched = sum(item["matched"] for item in per_dimension.values())
    rates = {
        dimension: (
            round(item["matched"] / item["total"], 4) if item["total"] else None
        )
        for dimension, item in per_dimension.items()
    }
    return round(matched / total, 4), rates, total


def main() -> int:
    parser = argparse.ArgumentParser(description="judge 人工校准")
    parser.add_argument("--init", action="store_true", help="生成校准表")
    parser.add_argument("--check", type=Path, help="计算已填写校准表的一致率")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--judge-threshold", type=float, default=0.7)
    parser.add_argument("--min-agreement", type=float, default=0.8)
    args = parser.parse_args()

    if args.check:
        try:
            overall, rates, total = compute_agreement(
                args.check,
                judge_threshold=args.judge_threshold,
            )
        except Exception as exc:
            print(f"一致率计算失败：{exc}", file=sys.stderr)
            return 2

        print(
            json.dumps(
                {"overall": overall, "per_dimension": rates, "labels": total},
                ensure_ascii=False,
                indent=2,
            )
        )
        if overall < args.min_agreement:
            print(
                f"一致率 {overall:.4f} 低于阈值 {args.min_agreement:.4f}，先改 rubric。",
                file=sys.stderr,
            )
            return 1
        print(f"一致率 {overall:.4f} 达标。")
        return 0

    if args.init:
        try:
            count = write_calibration_csv(
                load_jsonl(args.results),
                args.output,
                n=args.sample,
                seed=args.seed,
            )
        except Exception as exc:
            print(f"生成校准表失败：{exc}", file=sys.stderr)
            return 2
        print(f"已生成 {count} 条：{args.output}")
        print("请填写 human_faithfulness / human_answer_relevance / human_context_precision（0 或 1）。")
        print(f"填完后运行：python -m eval.calibrate_judge --check {args.output}")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
