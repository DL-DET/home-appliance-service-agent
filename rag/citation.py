"""引用提取、白名单校验和真实来源补全。"""

from __future__ import annotations

import re
from collections.abc import Iterable

from utils.path_tool import get_basename

CITATION_PATTERN = re.compile(
    r"([^\n，。；、：:（）()\[\]{}<>\"'|]+\.(?:txt|pdf|docx))",
    re.IGNORECASE,
)


def normalize_source(label: str) -> str:
    """只保留文件名，去掉页码和目录。"""
    text = str(label or "").strip()
    text = re.split(r"[（(]第", text, maxsplit=1)[0]
    return get_basename(text).strip().casefold()


def extract_citations(answer: str) -> list[str]:
    """提取回答里出现的所有引用文件名。"""
    citations: list[str] = []
    for match in CITATION_PATTERN.finditer(answer or ""):
        # 允许文件名里有空格，同时去掉列表符号和多余空白。
        name = match.group(1).strip().lstrip("-*• ").strip()
        name = get_basename(name).strip()
        if name and name not in citations:
            citations.append(name)
    return citations


def citation_integrity(answer: str, sources: Iterable[str]) -> tuple[bool, list[str]]:
    """所有引用是否都在本次检索来源白名单里。"""
    allowed = {normalize_source(source) for source in sources if str(source).strip()}
    fake = [
        citation
        for citation in extract_citations(answer)
        if normalize_source(citation) not in allowed
    ]
    return (not fake, fake)


def ensure_real_sources(answer: str, sources: Iterable[str]) -> str:
    """没有引用就补真实来源；有假引用就删掉并替换成真实来源。"""
    source_labels = [
        get_basename(str(source)) for source in sources if str(source).strip()
    ]
    if not source_labels:
        return answer

    is_valid, fake = citation_integrity(answer, source_labels)
    if is_valid and extract_citations(answer):
        return answer

    lines = answer.splitlines()
    if fake:
        lines = [
            line
            for line in lines
            if not any(fake_name in line for fake_name in fake)
        ]
    lines = [line for line in lines if line.strip() not in {"参考资料：", "参考资料:"}]
    cleaned = "\n".join(lines).rstrip()

    source_block = "\n".join(f"- {label}" for label in source_labels)
    if cleaned:
        return f"{cleaned}\n\n参考资料：\n{source_block}"
    return f"参考资料：\n{source_block}"
