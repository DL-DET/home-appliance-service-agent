"""来源文件名提取：Windows 路径在 Linux/Docker 下也要只显示文件名。"""

from langchain_core.documents import Document

from rag.citation import ensure_real_sources, extract_citations, normalize_source
from rag.hybrid_retriever import document_key
from rag.rag_service import get_source_label


WINDOWS_SOURCE = r"D:\python\agent智能体\data\故障排除.txt"


def test_get_source_label_strips_windows_directory():
    document = Document(
        page_content="主刷被异物卡住",
        metadata={"source": WINDOWS_SOURCE},
    )

    assert get_source_label(document) == "故障排除.txt"


def test_get_source_label_keeps_page_suffix():
    document = Document(
        page_content="内容",
        metadata={"source": WINDOWS_SOURCE, "page": 1},
    )

    assert get_source_label(document) == "故障排除.txt（第2页）"


def test_normalize_source_handles_windows_path_and_page():
    assert normalize_source(WINDOWS_SOURCE) == "故障排除.txt"
    assert normalize_source(WINDOWS_SOURCE + "（第2页）") == "故障排除.txt"


def test_extract_citations_strips_windows_path():
    answer = rf"参考资料：{WINDOWS_SOURCE}"

    assert extract_citations(answer) == ["故障排除.txt"]


def test_document_key_uses_filename_not_full_path():
    document = Document(
        page_content="主刷被异物卡住",
        metadata={"source": WINDOWS_SOURCE},
    )

    assert document_key(document)[0] == "故障排除.txt"


def test_ensure_real_sources_strips_directory_from_sources():
    cleaned = ensure_real_sources("主刷需要清理。", [WINDOWS_SOURCE])

    assert "参考资料" in cleaned
    assert "故障排除.txt" in cleaned
    assert r"D:\python" not in cleaned
