"""混合检索的 RRF 融合与阈值测试，不需要 API Key。"""

from langchain_core.documents import Document

from rag.hybrid_retriever import HybridRetriever


def make_doc(source: str, content: str) -> Document:
    return Document(page_content=content, metadata={"source": source})


def test_rrf_prefers_document_hit_by_both_retrievers():
    brush = make_doc("a.txt", "主刷 轮组 噪音 毛发")
    battery = make_doc("b.txt", "电池 鼓包 断电 售后")

    def vector_search(_query: str, _k: int):
        return [(brush, 0.8), (battery, 0.5)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=[brush, battery],
        k=2,
        candidate_k=2,
        min_vector_score=0.0,
    )

    result = retriever.invoke("主刷 轮组")

    assert result[0].metadata["source"] == "a.txt"
    assert len(result) == 2


def test_rrf_deduplicates_same_chunk():
    brush = make_doc("a.txt", "主刷 轮组 噪音")
    battery = make_doc("b.txt", "电池 鼓包 断电")

    def vector_search(_query: str, _k: int):
        return [(brush, 0.8), (battery, 0.5)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=[brush, battery],
        k=2,
        candidate_k=2,
        min_vector_score=0.0,
    )

    result = retriever.invoke("主刷")

    keys = [(item.metadata["source"], item.page_content) for item in result]
    assert len(keys) == len(set(keys))


def test_low_vector_and_zero_bm25_returns_empty():
    unrelated = make_doc("pdf.txt", "工业设备维护手册")

    def vector_search(_query: str, _k: int):
        return [(unrelated, 0.1)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=[unrelated],
        k=1,
        candidate_k=1,
        min_vector_score=0.35,
    )

    assert retriever.invoke("以旧换新") == []


def test_bm25_can_keep_exact_match_with_low_vector_score():
    brush = make_doc("a.txt", "主刷 轮组 噪音")

    def vector_search(_query: str, _k: int):
        return [(brush, 0.1)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=[brush],
        k=1,
        candidate_k=1,
        min_vector_score=0.35,
    )

    result = retriever.invoke("主刷")
    assert [item.metadata["source"] for item in result] == ["a.txt"]


def test_source_diversity_prefers_different_files():
    docs = [
        make_doc("a.txt", "主刷 轮组 噪音 一"),
        make_doc("a.txt", "主刷 轮组 噪音 二"),
        make_doc("b.txt", "主刷 边刷 电机 一"),
        make_doc("b.txt", "主刷 边刷 电机 二"),
    ]

    def vector_search(_query: str, _k: int):
        return [(doc, 0.8 - index * 0.05) for index, doc in enumerate(docs)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=docs,
        k=2,
        candidate_k=4,
        min_vector_score=0.0,
        max_per_source=1,
    )

    result = retriever.invoke("主刷")
    assert len(result) == 2
    assert {item.metadata["source"] for item in result} == {"a.txt", "b.txt"}


def test_top_k_limits_results():
    docs = [
        make_doc("a.txt", "主刷 轮组 噪音"),
        make_doc("b.txt", "主刷 边刷 电机"),
        make_doc("c.txt", "电池 鼓包 断电"),
    ]

    def vector_search(_query: str, _k: int):
        return [(doc, 0.8 - index * 0.1) for index, doc in enumerate(docs)]

    retriever = HybridRetriever(
        vector_search=vector_search,
        documents=docs,
        k=2,
        candidate_k=3,
        min_vector_score=0.0,
    )

    assert len(retriever.invoke("主刷")) == 2
