"""检索层离线评测：对比纯向量和混合检索的 Hit@K、MRR 和关键词覆盖率。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 直接执行 python scripts/evaluate_rag.py 时，scripts 目录会成为 sys.path[0]，
# 项目根目录不在其中。先补上根目录，才能稳定导入 rag.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rag.vector_store import VectorStoreService


def load_cases(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def evaluate_with_retriever(retriever, cases: list[dict], top_k: int) -> dict:
    details: list[dict] = []

    for case in cases:
        documents = retriever.invoke(case["query"])
        sources = [
            Path(str(document.metadata.get("source", ""))).name
            for document in documents
        ]
        combined_text = "\n".join(document.page_content for document in documents)
        matched_keywords = [
            keyword
            for keyword in case["expected_keywords"]
            if keyword in combined_text
        ]
        keyword_coverage = len(matched_keywords) / len(case["expected_keywords"])

        hit_rank = 0
        for rank, source in enumerate(sources, start=1):
            if source == case["expected_source"]:
                hit_rank = rank
                break

        details.append(
            {
                "id": case["id"],
                "query": case["query"],
                "hit": hit_rank > 0,
                "hit_rank": hit_rank or None,
                "mrr": round(1 / hit_rank, 4) if hit_rank else 0.0,
                "sources": sorted(set(sources)),
                "keyword_coverage": round(keyword_coverage, 4),
                "matched_keywords": matched_keywords,
            }
        )

    return {
        "top_k": top_k,
        "case_count": len(details),
        "hit_rate": round(
            sum(item["hit"] for item in details) / max(len(details), 1),
            4,
        ),
        "mrr": round(
            sum(item["mrr"] for item in details) / max(len(details), 1),
            4,
        ),
        "average_keyword_coverage": round(
            sum(item["keyword_coverage"] for item in details)
            / max(len(details), 1),
            4,
        ),
        "details": details,
    }


def evaluate(cases: list[dict], top_k: int, mode: str) -> dict:
    service = VectorStoreService()
    if mode == "vector":
        retriever = service.get_vector_retriever(top_k)
    else:
        retriever = service.get_retriever(top_k)

    result = evaluate_with_retriever(retriever, cases, top_k)
    result["mode"] = mode
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 RAG 检索是否命中预期资料")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(_PROJECT_ROOT) / "eval" / "rag_cases.jsonl",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-hit-rate", type=float, default=0.7)
    parser.add_argument(
        "--mode",
        choices=("vector", "hybrid", "compare"),
        default="hybrid",
        help="vector=纯向量，hybrid=混合检索，compare=同时跑两者",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.mode == "compare":
        vector = evaluate(cases, args.top_k, "vector")
        hybrid = evaluate(cases, args.top_k, "hybrid")
        result = {
            "top_k": args.top_k,
            "case_count": len(cases),
            "vector": vector,
            "hybrid": hybrid,
            "delta_hit_rate": round(
                hybrid["hit_rate"] - vector["hit_rate"], 4
            ),
            "delta_keyword_coverage": round(
                hybrid["average_keyword_coverage"]
                - vector["average_keyword_coverage"],
                4,
            ),
        }
        gate = hybrid
    else:
        result = evaluate(cases, args.top_k, args.mode)
        gate = result

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return 0 if gate["hit_rate"] >= args.min_hit_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
