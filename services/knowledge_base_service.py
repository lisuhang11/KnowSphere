"""知识库业务编排：CRUD 与跨模型文档移动。"""

from __future__ import annotations

from typing import Any

from models import create_embeddings
from services.errors import NotFoundError
from stores.facade import ChunkStore

class KnowledgeBaseService:
    def __init__(self, store: ChunkStore() | None = None) -> None:
        self.store = store or ChunkStore()

    def create(self, **kwargs: Any) -> dict[str, Any]:
        return self.store.create_knowledge_base(**kwargs)

    def list_all(self, owner: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_knowledge_bases(owner=owner)

    def get(self, kb_id: int, owner: str | None = None) -> dict[str, Any]:
        kb = self.store.get_knowledge_base(kb_id, owner=owner)
        if kb is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        return kb

    def update(self, kb_id: int, **kwargs: Any) -> dict[str, Any]:
        if self.store.get_knowledge_base(kb_id) is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        kb = self.store.update_knowledge_base(kb_id, **kwargs)
        if kb is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        return kb

    def delete(self, kb_id: int, owner: str | None = None) -> dict[str, Any]:
        result = self.store.delete_knowledge_base(kb_id, owner=owner)
        if result is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        try:
            from services.graph_extract_service import GraphExtractService

            GraphExtractService(self.store).delete_kb_graph(kb_id)
        except Exception:  # noqa: BLE001
            pass
        return {
            "id": result["id"],
            "name": result["name"],
            "deleted_documents": result["deleted_documents"],
            "deleted_chunks": result["deleted_chunks"],
        }

    def move_document(self, document_id: str, target_kb_id: int) -> dict[str, Any]:
        """移动文档到目标库；embedding 模型不同时重新嵌入。"""
        target = self.store.get_knowledge_base(target_kb_id)
        if target is None:
            raise NotFoundError(f"目标知识库不存在: {target_kb_id}")

        source_kb_id = self.store.get_document_kb_id(document_id)
        if source_kb_id is None:
            raise NotFoundError(f"文档不存在: {document_id}")

        if source_kb_id != target_kb_id:
            source = self.store.get_knowledge_base(source_kb_id)
            if source is not None and source["embedding_model_id"] != target["embedding_model_id"]:
                return self._move_with_reembed(document_id, source, target)

        moved = self.store.move_document(document_id, target_kb_id)
        return {"document_id": document_id, "kb_id": target_kb_id, "moved_chunks": moved}

    def _move_with_reembed(
        self,
        document_id: str,
        source: dict[str, Any],
        target: dict[str, Any],
    ) -> dict[str, Any]:
        contents = self.store.get_document_chunks_content(document_id)
        if not contents:
            raise NotFoundError(f"文档不存在: {document_id}")

        embeddings = create_embeddings(model=target["embedding_model_id"])
        vectors = embeddings.embed_documents(contents)
        meta = self.store.get_document_meta(document_id)
        doc_cfg = self.store.get_document_config(document_id)

        self.store.delete_document(document_id)
        count = self.store.insert_batch(
            document_id=document_id,
            file_name=meta["file_name"] if meta else document_id,
            chunks=contents,
            embeddings=vectors,
            base_metadata={"source": meta["file_name"] if meta else document_id},
            kb_id=target["id"],
            embedding_dim=target["embedding_dim"],
        )
        self.store.upsert_document(
            document_id=document_id,
            file_name=meta["file_name"] if meta else document_id,
            kb_id=target["id"],
            process_config=(doc_cfg or {}).get("process_config"),
            applied_strategy=(doc_cfg or {}).get("applied_strategy"),
        )
        return {
            "document_id": document_id,
            "kb_id": target["id"],
            "moved_chunks": count,
            "reembedded": True,
        }
