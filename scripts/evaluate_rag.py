"""检索层离线评测：统计 Hit@K 和关键词覆盖率。"""

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


def evaluate(cases: list[dict], top_k: int) -> dict:
    service = VectorStoreService()
    retriever = service.vector_store.as_retriever(search_kwargs={"k": top_k})
    details: list[dict] = []

    for case in cases:
        documents = retriever.invoke(case["query"])
        sources = {
            Path(str(document.metadata.get("source", ""))).name
            for document in documents
        }
        combined_text = "\n".join(document.page_content for document in documents)
        matched_keywords = [
            keyword
            for keyword in case["expected_keywords"]
            if keyword in combined_text
        ]
        keyword_coverage = len(matched_keywords) / len(case["expected_keywords"])

        details.append(
            {
                "id": case["id"],
                "query": case["query"],
                "hit": case["expected_source"] in sources,
                "sources": sorted(sources),
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
        "average_keyword_coverage": round(
            sum(item["keyword_coverage"] for item in details) / max(len(details), 1),
            4,
        ),
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 RAG 检索是否命中预期资料")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(_PROJECT_ROOT) / "eval" / "rag_cases.jsonl",
    )
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-hit-rate", type=float, default=0.7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = evaluate(load_cases(args.cases), top_k=args.top_k)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return 0 if result["hit_rate"] >= args.min_hit_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
