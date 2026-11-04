"""索引重建逻辑测试：不触碰真实 Chroma。"""

from rag.vector_store import VectorStoreService
import rag.vector_store as vector_store_module


class FakeVectorStore:
    def __init__(self) -> None:
        self.reset_called = False

    def reset_collection(self) -> None:
        self.reset_called = True


def test_rebuild_resets_collection_and_md5(tmp_path, monkeypatch):
    md5_path = tmp_path / "md5.text"
    md5_path.write_text("old-md5\n", encoding="utf-8")
    monkeypatch.setattr(
        vector_store_module,
        "get_abs_path",
        lambda _path: str(md5_path),
    )

    service = object.__new__(VectorStoreService)
    service.vector_store = FakeVectorStore()
    calls: list[str] = []
    service.load_documents = lambda: calls.append("load")

    service.rebuild()

    assert service.vector_store.reset_called is True
    assert md5_path.exists() is False
    assert calls == ["load"]
