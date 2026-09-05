"""文档摄取编排：解析 → 切块 → 向量化 → 写入 pgvector。"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from chunkers import embedding_content, split_parent_child_with_diagnostics, split_with_diagnostics
from chunkers.legacy_splitter import CHINESE_SEPARATORS
from config.settings import settings
from ingestion.embed_batch import embed_documents_batched
from models import create_embeddings
from services.errors import BadRequestError, NotFoundError
from stores.facade import ChunkStore
from utils.observability import observe

def resolve_chunk_config(kb: dict, process_config: dict | None) -> dict[str, str | int | bool]:
    """合并知识库默认与文档级覆盖（文档优先）。"""
    chunking = (process_config or {}).get("chunking_config") or {}
    strategy = chunking.get("strategy") or kb.get("chunk_strategy", "auto")
    chunk_size = chunking.get("chunk_size") or kb["chunk_size"]
    chunk_overlap = chunking.get("chunk_overlap")
    if chunk_overlap is None:
        chunk_overlap = kb["chunk_overlap"]
    enable_parent_child = chunking.get("enable_parent_child")
    if enable_parent_child is None:
        enable_parent_child = kb.get("enable_parent_child", settings.enable_parent_child)
    parent_chunk_size = chunking.get("parent_chunk_size") or kb.get(
        "parent_chunk_size", settings.parent_chunk_size
    )
    child_chunk_size = chunking.get("child_chunk_size") or kb.get(
        "child_chunk_size", settings.child_chunk_size
    )
    return {
        "strategy": strategy,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "enable_parent_child": bool(enable_parent_child),
        "parent_chunk_size": int(parent_chunk_size),
        "child_chunk_size": int(child_chunk_size),
    }

def create_splitter(
    chunk_size: int | None = None, chunk_overlap: int | None = None
) -> RecursiveCharacterTextSplitter:
    """legacy 切块器（部分脚本/测试仍使用）。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size or settings.chunk_size,
        chunk_overlap=chunk_overlap if chunk_overlap is not None else settings.chunk_overlap,
        separators=CHINESE_SEPARATORS,
        length_function=len,
    )

class IngestionService:
    def __init__(self, store: ChunkStore | None = None) -> None:
        self.store = store or ChunkStore()

    def _parse_document(self, path: str) -> tuple[str, list]:
        from ingestion.parser import MARKDOWN_IMAGE_LINE_RE, parse_document

        parse_options = {"file_name": Path(path).name, "ocr_enabled": settings.ocr_enabled}
        result = parse_document(path, parse_options=parse_options)
        lines = [
            ln for ln in result.markdown.splitlines()
            if not re.match(MARKDOWN_IMAGE_LINE_RE, ln.strip())
        ]
        return "\n".join(lines), list(result.image_refs)

    def _upload_parse_images(self, image_refs: list, kb_id: int, document_id: str) -> list[dict]:
        from ingestion.parser.image_store import upload_parse_images

        return upload_parse_images(image_refs, kb_id=str(kb_id), document_id=document_id)

    def _chunk_embed_and_store(
        self,
        *,
        text: str,
        cfg: dict[str, str | int | bool],
        kb: dict,
        document_id: str,
        file_name: str,
        owner: str | None,
        kb_id: int,
        replace: bool = False,
    ) -> tuple[int, str, dict]:
        self.store.update_stage(document_id, "chunking", owner)
        base_meta = {"source": file_name}

        if cfg["enable_parent_child"]:
            pc = split_parent_child_with_diagnostics(
                text,
                strategy=str(cfg["strategy"]),
                parent_size=int(cfg["parent_chunk_size"]),
                child_size=int(cfg["child_chunk_size"]),
                chunk_overlap=int(cfg["chunk_overlap"]),
            )
            if not pc.children:
                raise ValueError("文档切块后为空，请检查文件内容")

            embed_texts = [embedding_content(c.content, c.context_header) for c in pc.children]
            self.store.update_stage(document_id, "embedding", owner)
            emb = create_embeddings(model=kb["embedding_model_id"])

            def _embed_progress(_done: int, _total: int) -> None:
                self.store.update_stage(document_id, "embedding", owner)

            vectors = embed_documents_batched(emb, embed_texts, on_progress=_embed_progress)
            self.store.update_stage(document_id, "indexing", owner)
            insert_fn = (
                self.store.replace_document_parent_child_batch
                if replace
                else self.store.insert_parent_child_batch
            )
            count = insert_fn(
                document_id=document_id,
                file_name=file_name,
                parent_contents=[p.content for p in pc.parents],
                parent_metadata=[{"context_header": p.context_header} for p in pc.parents],
                child_contents=[c.content for c in pc.children],
                child_embeddings=vectors,
                child_metadata=[{"context_header": c.context_header} for c in pc.children],
                child_parent_indices=[c.parent_index for c in pc.children],
                owner=owner,
                base_metadata=base_meta,
                kb_id=kb_id,
                embedding_dim=kb["embedding_dim"],
            )
            return count, pc.selected_tier, {
                "parent_count": len(pc.parents),
                "enable_parent_child": True,
            }

        result = split_with_diagnostics(
            text,
            strategy=str(cfg["strategy"]),
            chunk_size=int(cfg["chunk_size"]),
            chunk_overlap=int(cfg["chunk_overlap"]),
        )
        chunks = [c.content for c in result.chunks]
        if not chunks:
            raise ValueError("文档切块后为空，请检查文件内容")

        self.store.update_stage(document_id, "embedding", owner)
        emb = create_embeddings(model=kb["embedding_model_id"])

        def _embed_progress(_done: int, _total: int) -> None:
            self.store.update_stage(document_id, "embedding", owner)

        vectors = embed_documents_batched(emb, chunks, on_progress=_embed_progress)
        self.store.update_stage(document_id, "indexing", owner)
        insert_fn = self.store.replace_document_chunks if replace else self.store.insert_batch
        count = insert_fn(
            document_id=document_id,
            file_name=file_name,
            chunks=chunks,
            embeddings=vectors,
            owner=owner,
            base_metadata=base_meta,
            kb_id=kb_id,
            embedding_dim=kb["embedding_dim"],
            metadata_list=[{"context_header": c.context_header} for c in result.chunks],
        )
        return count, result.selected_tier, {"enable_parent_child": False}

    def _get_kb(self, kb_id: int) -> dict:
        if kb_id is None:
            raise BadRequestError("kb_id 不能为空")
        kb = self.store.get_knowledge_base(kb_id)
        if kb is None:
            raise NotFoundError(f"知识库不存在: {kb_id}")
        return kb

    @observe(name="ingest_file")
    def ingest_file(
        self,
        path: str,
        owner: str | None = None,
        kb_id: int | None = None,
        process_config: dict | None = None,
        document_id: str | None = None,
        file_name: str | None = None,
    ) -> dict:
        self.store.init_schema()
        kb = self._get_kb(kb_id)
        cfg = resolve_chunk_config(kb, process_config)
        file_name = file_name or Path(path).name
        document_id = document_id or uuid.uuid4().hex[:12]

        self.store.update_stage(document_id, "parsing", owner)
        text, image_refs = self._parse_document(path)
        image_refs = self._upload_parse_images(image_refs, kb_id, document_id)

        count, applied_tier, extra = self._chunk_embed_and_store(
            text=text,
            cfg=cfg,
            kb=kb,
            document_id=document_id,
            file_name=file_name,
            owner=owner,
            kb_id=kb_id,
            replace=False,
        )
        self.store.upsert_document(
            document_id=document_id,
            file_name=file_name,
            kb_id=kb_id,
            owner=owner,
            process_config=process_config,
            applied_strategy=applied_tier,
            image_refs=image_refs,
        )
        return {
            "document_id": document_id,
            "file_name": file_name,
            "chunk_count": count,
            "kb_id": kb_id,
            "chunk_strategy": applied_tier,
            "applied_strategy": applied_tier,
            **extra,
        }

    @observe(name="reparse_document")
    def reparse_document(
        self,
        path: str,
        document_id: str,
        owner: str | None = None,
        kb_id: int | None = None,
        process_config: dict | None = None,
        file_name: str | None = None,
    ) -> dict:
        self.store.init_schema()
        kb = self._get_kb(kb_id)
        if process_config is None:
            existing = self.store.get_document_config(document_id, owner=owner)
            process_config = (existing or {}).get("process_config") or None

        cfg = resolve_chunk_config(kb, process_config)
        file_name = file_name or Path(path).name

        self.store.update_stage(document_id, "parsing", owner)
        text, image_refs = self._parse_document(path)
        image_refs = self._upload_parse_images(image_refs, kb_id, document_id)
        count, applied_tier, extra = self._chunk_embed_and_store(
            text=text,
            cfg=cfg,
            kb=kb,
            document_id=document_id,
            file_name=file_name,
            owner=owner,
            kb_id=kb_id,
            replace=True,
        )
        self.store.upsert_document(
            document_id=document_id,
            file_name=file_name,
            kb_id=kb_id,
            owner=owner,
            process_config=process_config,
            applied_strategy=applied_tier,
            image_refs=image_refs,
        )
        return {
            "document_id": document_id,
            "file_name": file_name,
            "chunk_count": count,
            "kb_id": kb_id,
            "chunk_strategy": applied_tier,
            "applied_strategy": applied_tier,
            **extra,
        }
