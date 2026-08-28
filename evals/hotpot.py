"""HotpotQA 评测数据：HF 加载、按题隔离摄取与清理。

每题 10 个段落（2 金标 + 8 干扰）以 owner=hotpot_<id> 隔离写入 pgvector，
防止跨题污染；跑完按 owner 清理，重复执行幂等。

国内网络默认走 hf-mirror（HF_ENDPOINT 未设置时才兜底，不覆盖已有配置）。
"""

from __future__ import annotations

import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import datasets  # noqa: E402  必须在设置 HF_ENDPOINT 之后导入
import psycopg

from config.settings import settings
from ingestion.ingest import create_splitter
from models import create_embeddings
from utils.vector_store import ChunkStore

def load_hotpot_sample(n: int = 50, split: str = "validation", seed: int = 42) -> list[dict]:
    """加载 HotpotQA(distractor) 抽样，返回 [{id, question, answer, type, paragraphs}]。"""
    ds = datasets.load_dataset("hotpotqa/hotpot_qa", "distractor", split=split, trust_remote_code=True)
    ds = ds.shuffle(seed=seed).select(range(min(n, len(ds))))
    items = []
    for row in ds:
        paragraphs = [
            {"title": title, "text": " ".join(sentences).strip()}
            for title, sentences in zip(row["context"]["title"], row["context"]["sentences"])
        ]
        items.append(
            {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "type": row["type"],
                "paragraphs": [p for p in paragraphs if p["text"]],
            }
        )
    return items

def ingest_question(item: dict, owner: str) -> tuple[int, int]:
    """该题全部段落按产品同款切块入库，并建一个专属知识库隔离本题数据。

    返回 (kb_id, 块数)。kb_id 供 agent 通过 config["configurable"]["kb_ids"] 指定
    检索范围（与产品链路一致，保证 doc_retrieval 能命中本题片段）。
    """
    store = ChunkStore()
    kb = store.create_knowledge_base(
        name=f"hotpot_{item['id']}",
        description=f"HotpotQA 评测题 {item['id']} 的专属知识库",
        owner=owner,
    )
    # 与产品摄取同款切块器：评测链路 = 产品链路，避免参数漂移污染评测结论
    splitter = create_splitter()
    embeddings = create_embeddings()
    total = 0
    for idx, para in enumerate(item["paragraphs"]):
        chunks = splitter.split_text(para["text"]) or [para["text"]]
        vectors = embeddings.embed_documents(chunks)
        total += store.insert_batch(
            document_id=f"{item['id']}_{idx:02d}",
            file_name=para["title"],
            chunks=chunks,
            embeddings=vectors,
            owner=owner,
            kb_id=kb["id"],
        )
    return kb["id"], total

def cleanup_question(owner: str, kb_id: int | None = None) -> None:
    """删除该题知识库及其全部块（幂等），保持 chunks / knowledge_bases 表干净。

    优先按 kb_id 走 delete_knowledge_base（同事务删块+删库）；
    摄取中途失败拿不到 kb_id 时兜底按 owner 清理。
    """
    if kb_id is not None:
        ChunkStore().delete_knowledge_base(kb_id, owner=owner)
        return
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute("DELETE FROM chunks WHERE owner = %s", (owner,))
        conn.execute("DELETE FROM knowledge_bases WHERE owner = %s", (owner,))
        conn.commit()
