import json
from pathlib import Path


CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "rag_cases.jsonl"


def test_rag_eval_cases_are_well_formed():
    cases = [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["query"]
        assert case["expected_source"].endswith(".txt")
        assert case["expected_keywords"]
