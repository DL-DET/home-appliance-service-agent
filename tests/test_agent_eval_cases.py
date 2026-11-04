"""校验 Agent 评测集的结构，防止 schema 漂移。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from services.customer_data import get_user_profile

CASES_PATH = Path(__file__).resolve().parents[1] / "eval" / "agent_cases.jsonl"

ROUTES = {"knowledge", "order", "report", "general", "security", "policy"}
CAPABILITIES = {"rag", "order", "report", "none"}
CATEGORY_COUNTS = {
    "knowledge": 12,
    "order": 5,
    "report": 4,
    "security": 4,
    "injection": 3,
    "negative": 2,
}
REQUIRED_FIELDS = {
    "id",
    "query",
    "category",
    "expected_route",
    "expected_capability",
    "expected_tools",
    "must_include",
    "should_include",
    "must_not_include",
    "answerable",
    "refusal_required",
    "citation_required",
    "manual_review",
    "context",
    "notes",
}


def load_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_agent_eval_cases_are_well_formed():
    cases = load_cases()

    assert len(cases) >= 30
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["query"] for case in cases}) == len(cases)
    assert Counter(case["category"] for case in cases) == Counter(CATEGORY_COUNTS)

    for case in cases:
        assert REQUIRED_FIELDS <= case.keys(), case["id"]
        assert case["expected_route"] in ROUTES, case["id"]
        assert case["expected_capability"] in CAPABILITIES, case["id"]
        assert isinstance(case["expected_tools"], list), case["id"]
        assert isinstance(case["must_include"], list), case["id"]
        assert isinstance(case["should_include"], list), case["id"]
        assert isinstance(case["must_not_include"], list), case["id"]
        assert not set(case["must_include"]) & set(case["must_not_include"]), case["id"]
        assert not set(case["should_include"]) & set(case["must_not_include"]), case["id"]
        assert isinstance(case["answerable"], bool), case["id"]
        assert isinstance(case["refusal_required"], bool), case["id"]
        assert isinstance(case["citation_required"], bool), case["id"]
        assert isinstance(case["manual_review"], bool), case["id"]
        assert case["context"]["user_id"], case["id"]
        assert re.fullmatch(r"\d{4}-\d{2}", case["context"]["month"]), case["id"]
        assert case["notes"], case["id"]

        if case["expected_capability"] == "none":
            assert case["expected_tools"] == [], case["id"]
        if case["citation_required"]:
            assert case["category"] == "knowledge", case["id"]


def test_agent_eval_context_users_exist():
    for case in load_cases():
        assert get_user_profile(case["context"]["user_id"]) is not None, case["id"]
