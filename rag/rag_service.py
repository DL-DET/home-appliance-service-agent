"""RAG 检索总结服务。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from model.factory import get_chat_model
from rag.citation import ensure_real_sources
from rag.vector_store import VectorStoreService
from utils.prompt_loader import load_rag_prompts


def get_source_label(document: Document) -> str:
    """从加载器元数据中提取适合展示给用户的来源名称。"""
    source = str(document.metadata.get("source", "")).strip()
    label = Path(source).name if source else "未知来源"
    page = document.metadata.get("page")
    if page is not None:
        return f"{label}（第{int(page) + 1}页）"
    return label


def format_sources(documents: list[Document]) -> str:
    """去重后生成稳定的来源列表，方便最终回答引用和人工核查。"""
    if not documents:
        return "未检索到可用来源。"

    labels: list[str] = []
    for document in documents:
        label = get_source_label(document)
        if label not in labels:
            labels.append(label)
    return "\n".join(f"- {label}" for label in labels)


class RagSummarizeService:
    """先检索证据，再让模型只基于证据总结，并附上来源。"""

    def __init__(self) -> None:
        self.vector_store = VectorStoreService()
        self.retriever = self.vector_store.get_retriever()
        self.prompt_template = PromptTemplate.from_template(load_rag_prompts())
        self.model = get_chat_model()
        self.chain = self._init_chain()

    def _init_chain(self):
        return self.prompt_template | self.model | StrOutputParser()

    def retrieve_documents(self, query: str) -> list[Document]:
        return self.retriever.invoke(query)

    def rag_summarize(self, query: str) -> str:
        documents = self.retrieve_documents(query)
        if not documents:
            return "知识库暂未检索到与该问题直接相关的内容。"

        context = "\n".join(
            f"[资料 {index}] 来源：{get_source_label(document)}\n"
            f"{document.page_content}"
            for index, document in enumerate(documents, start=1)
        )
        summary = self.chain.invoke({"input": query, "context": context})
        labels = [get_source_label(document) for document in documents]
        return ensure_real_sources(summary.strip(), labels)


if __name__ == "__main__":
    service = RagSummarizeService()
    print(service.rag_summarize("小户型适合什么清洁电器"))
