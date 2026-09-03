"""HotpotQA 数据集 → EvalDataset（isolated 模式，每题自带 paragraphs）。"""

from __future__ import annotations

import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import datasets  # noqa: E402

from evals.schemas import EvalDataset, Passage, QAPair


def gold_paragraph_indices(paragraphs: list[dict], supporting_facts: dict) -> list[int]:
    """从 supporting_facts 解析金标段落下标（按 title 匹配 context 段落）。"""
    title_to_idx = {p["title"]: idx for idx, p in enumerate(paragraphs)}
    gold: list[int] = []
    seen: set[int] = set()
    for sf_title in supporting_facts.get("title") or []:
        idx = title_to_idx.get(sf_title)
        if idx is not None and idx not in seen:
            seen.add(idx)
            gold.append(idx)
    return sorted(gold)


def load_hotpot_dataset(
    *,
    sample_limit: int | None = 50,
    split: str = "validation",
    seed: int = 42,
) -> EvalDataset:
    ds = datasets.load_dataset("hotpotqa/hotpot_qa", "distractor", split=split, trust_remote_code=True)
    n = sample_limit if sample_limit is not None else len(ds)
    ds = ds.shuffle(seed=seed).select(range(min(n, len(ds))))

    items: list[QAPair] = []
    all_passages: dict[int, Passage] = {}
    for row in ds:
        paragraphs: list[dict] = []
        for title, sentences in zip(row["context"]["title"], row["context"]["sentences"]):
            text = " ".join(sentences).strip()
            if text:
                paragraphs.append({"title": title, "text": text})

        qid = len(items)
        gold_idx = gold_paragraph_indices(paragraphs, row["supporting_facts"])
        if not gold_idx:
            # 兜底：至少保留首个段落，避免空 gold
            gold_idx = [0] if paragraphs else []

        gold_pids = [qid * 100 + idx for idx in gold_idx]
        gold_texts = [paragraphs[i]["text"] for i in gold_idx]

        for idx, para in enumerate(paragraphs):
            pid = qid * 100 + idx
            all_passages[pid] = Passage(pid=pid, title=para["title"], text=para["text"])

        items.append(
            QAPair(
                qid=qid,
                question=row["question"],
                pids=gold_pids,
                passages=gold_texts,
                answer=row["answer"],
                meta={
                    "type": row["type"],
                    "hotpot_id": row["id"],
                    "paragraphs": paragraphs,
                },
            )
        )
    return EvalDataset(
        id="hotpot",
        passages=list(all_passages.values()),
        items=items,
    )
