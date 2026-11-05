"""混合检索：向量召回 + BM25 召回，用 RRF 融合排名。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import jieba
from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

from utils.path_tool import get_basename

jieba.setLogLevel(logging.WARNING)

STOPWORDS = {
    "的",
    "了",
    "是",
    "在",
    "和",
    "与",
    "及",
    "或",
    "有",
    "没有",
    "怎么",
    "如何",
    "什么",
    "哪里",
    "哪些",
    "可以",
    "能否",
    "需要",
    "应该",
    "一下",
    "这个",
    "那个",
    "吗",
    "呢",
    "啊",
    "吧",
    "我",
    "你",
    "它",
    "他",
    "她",
    "们",
}

VectorSearch = Callable[[str, int], Sequence[tuple[Document, float]]]


def tokenize(text: str) -> list[str]:
    """中文分词，去掉空白、停用词和单字。"""
    tokens: list[str] = []
    for token in jieba.lcut(text or ""):
        token = token.strip().lower()
        if len(token) >= 2 and token not in STOPWORDS:
            tokens.append(token)
    return tokens


def document_key(document: Document) -> tuple[str, str]:
    """用来源文件名 + 内容做去重 key，避免同一 chunk 被融合两次。"""
    source = get_basename(str(document.metadata.get("source", "")))
    return source, document.page_content


class HybridRetriever:
    """向量召回和 BM25 召回各自排序，再用 RRF 融合。"""

    def __init__(
        self,
        *,
        vector_search: VectorSearch,
        documents: Sequence[Document],
        k: int = 3,
        candidate_k: int = 10,
        bm25_candidate_k: int | None = None,
        rrf_k: int = 60,
        vector_weight: float = 1.0,
        bm25_weight: float = 1.0,
        max_per_source: int = 1,
        min_vector_score: float = 0.35,
    ) -> None:
        self.vector_search = vector_search
        self.documents = list(documents)
        self.k = k
        self.candidate_k = max(candidate_k, k)
        self.bm25_candidate_k = bm25_candidate_k or self.candidate_k
        self.rrf_k = rrf_k
        self.vector_weight = vector_weight
        self.bm25_weight = bm25_weight
        self.max_per_source = max_per_source
        self.min_vector_score = min_vector_score
        self._tokenized = [tokenize(document.page_content) for document in self.documents]
        self._bm25 = BM25Okapi(self._tokenized) if self._tokenized else None

    def invoke(self, query: str) -> list[Document]:
        vector_results = [
            (document, float(score))
            for document, score in self.vector_search(query, self.candidate_k)
        ]
        bm25_results = self._bm25_search(query)

        max_vector = max((score for _, score in vector_results), default=0.0)
        # 两个检索器都没有给出任何相关信号时，直接返回空，交给上层拒答。
        if max_vector < self.min_vector_score and not bm25_results:
            return []

        merged = self._rrf(
            (vector_results, self.vector_weight),
            (bm25_results, self.bm25_weight),
        )
        selected = self._select_diverse(merged, self.k)
        return [document for document, _score in selected]

    def _bm25_search(self, query: str) -> list[tuple[Document, float]]:
        if self._bm25 is None:
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked: list[tuple[Document, float]] = []
        for document, doc_tokens, score in zip(
            self.documents, self._tokenized, scores
        ):
            # BM25 的 IDF 在某些语料下可能为负；只要词面命中就保留，
            # 排名交给 BM25 分数，融合交给 RRF 的排名。
            if any(token in doc_tokens for token in tokens):
                ranked.append((document, float(score)))

        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[: self.bm25_candidate_k]

    def _rrf(
        self,
        *weighted_lists: tuple[Sequence[tuple[Document, float]], float],
    ) -> list[tuple[Document, float]]:
        scores: dict[tuple[str, str], float] = {}
        documents: dict[tuple[str, str], Document] = {}

        for ranked, weight in weighted_lists:
            for rank, (document, _score) in enumerate(ranked, start=1):
                key = document_key(document)
                documents[key] = document
                scores[key] = (
                    scores.get(key, 0.0)
                    + weight / (self.rrf_k + rank)
                )

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [(documents[key], score) for key, score in ordered]

    def _select_diverse(
        self,
        merged: Sequence[tuple[Document, float]],
        limit: int,
    ) -> list[tuple[Document, float]]:
        """同一来源最多取 max_per_source 个 chunk，避免一个文件占满上下文。"""
        selected: list[tuple[Document, float]] = []
        selected_keys: set[tuple[str, str]] = set()
        source_counts: dict[str, int] = {}

        for document, score in merged:
            key = document_key(document)
            source = key[0]
            if source_counts.get(source, 0) >= self.max_per_source:
                continue
            selected.append((document, score))
            selected_keys.add(key)
            source_counts[source] = source_counts.get(source, 0) + 1
            if len(selected) >= limit:
                return selected

        # 来源不够时，用剩余结果补齐，保证召回数量。
        for document, score in merged:
            key = document_key(document)
            if key in selected_keys:
                continue
            selected.append((document, score))
            if len(selected) >= limit:
                break
        return selected
