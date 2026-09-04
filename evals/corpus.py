"""评测语料灌库：passage → chunks，document_id=eval_passage_{pid}。"""

from __future__ import annotations

from collections.abc import Callable

from evals.schemas import Passage, QAPair
from ingestion.ingest import create_splitter
from models import create_embeddings
from utils.vector_store import ChunkStore

def eval_passage_document_id(pid: int) -> str:
    return f"eval_passage_{pid}"

def parse_passage_id(document_id: str) -> int | None:
    prefix = "eval_passage_"
    if document_id.startswith(prefix):
        try:
            return int(document_id[len(prefix) :])
        except ValueError:
            return None
    return None

def ingest_passages(
    passages: list[Passage],
    *,
    kb_id: int,
    owner: str,
    kb_row: dict | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """将 passages 切块写入 eval KB，返回总 chunk 数。"""
    store = ChunkStore()
    kb = kb_row or store.get_knowledge_base(kb_id, owner=owner)
    splitter = create_splitter(
        chunk_size=kb["chunk_size"] if kb else None,
        chunk_overlap=kb["chunk_overlap"] if kb else None,
    )
    embeddings = create_embeddings()
    total = 0
    n_passages = len(passages)
    if on_progress:
        on_progress(0, n_passages)
    for idx, passage in enumerate(passages):
        if not passage.text.strip():
            if on_progress:
                on_progress(idx + 1, n_passages)
            continue
        chunks = splitter.split_text(passage.text) or [passage.text]
        vectors = embeddings.embed_documents(chunks)
        total += store.insert_batch(
            document_id=eval_passage_document_id(passage.pid),
            file_name=passage.title,
            chunks=chunks,
            embeddings=vectors,
            owner=owner,
            kb_id=kb_id,
            base_metadata={"eval_passage_id": passage.pid},
        )
        if on_progress:
            on_progress(idx + 1, n_passages)
    return total

def ingest_isolated_item(item: QAPair, *, kb_id: int, owner: str, kb_row: dict | None = None) -> int:
    passages = [
        Passage(pid=pid, title=f"passage_{pid}", text=text)
        for pid, text in zip(item.pids, item.passages)
        if text
    ]
    extra = []
    for idx, para in enumerate(item.meta.get("paragraphs") or []):
        if isinstance(para, dict):
            pid = item.qid * 100 + idx
            extra.append(Passage(pid=pid, title=para.get("title", ""), text=para.get("text", "")))
    if extra:
        passages = extra
    return ingest_passages(passages, kb_id=kb_id, owner=owner, kb_row=kb_row)

def map_retrieval_ids(sources: list[dict], qa_pair: QAPair) -> list[int]:
    """从 doc_retrieval 结果反查 passage id；优先 document_id，回退正文匹配。"""
    out: list[int] = []
    seen: set[int] = set()
    for src in sources:
        pid = parse_passage_id(str(src.get("document_id") or ""))
        if pid is None:
            snippet = str(src.get("snippet") or "")
            for i, passage in enumerate(qa_pair.passages):
                if not passage:
                    continue
                if passage in snippet or snippet in passage:
                    pid = qa_pair.pids[i] if i < len(qa_pair.pids) else None
                    break
        if pid is not None and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out
