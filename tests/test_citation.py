"""引用白名单与真实来源补全测试。"""

from rag.citation import (
    citation_integrity,
    ensure_real_sources,
    extract_citations,
)


def test_extract_citations_finds_filenames():
    answer = "结论如下。\n\n参考资料：故障排除.txt、维护保养.txt"
    assert extract_citations(answer) == ["故障排除.txt", "维护保养.txt"]


def test_citation_integrity_rejects_fake_source():
    valid, fake = citation_integrity(
        "参考资料：故障排除.txt、选购指南.txt",
        ["故障排除.txt（第2页）"],
    )

    assert valid is False
    assert fake == ["选购指南.txt"]


def test_citation_integrity_accepts_real_source_with_page_suffix():
    valid, fake = citation_integrity(
        "参考资料：故障排除.txt",
        ["故障排除.txt（第2页）"],
    )

    assert valid is True
    assert fake == []


def test_ensure_real_sources_replaces_fake_citation():
    cleaned = ensure_real_sources(
        "主刷需要清理。\n\n参考资料：不存在的.txt",
        ["故障排除.txt"],
    )

    assert "不存在的.txt" not in cleaned
    assert "故障排除.txt" in cleaned
    assert "参考资料" in cleaned


def test_ensure_real_sources_appends_when_missing():
    cleaned = ensure_real_sources("主刷需要清理。", ["故障排除.txt"])

    assert cleaned.startswith("主刷需要清理。")
    assert "参考资料" in cleaned
    assert "故障排除.txt" in cleaned


def test_ensure_real_sources_keeps_valid_citation():
    answer = "主刷需要清理。\n\n参考资料：故障排除.txt"
    assert ensure_real_sources(answer, ["故障排除.txt"]) == answer
