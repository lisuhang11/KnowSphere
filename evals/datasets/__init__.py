"""数据集加载：JSON 内置集、Parquet（WeKnora 兼容）、HotpotQA。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evals.schemas import EvalDataset, Passage, QAPair

_SAMPLES_ROOT = Path(__file__).resolve().parent / "samples"
_DATASET_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


def _json_dataset_path(dataset_id: str) -> Path:
    return _SAMPLES_ROOT / f"{dataset_id}.json"


def validate_json_dataset(raw: dict[str, Any]) -> str:
    """校验 JSON 数据集结构，返回 dataset id。"""
    ds_id = str(raw.get("id") or "").strip()
    if not ds_id:
        raise ValueError("缺少 id 字段")
    if not _DATASET_ID_RE.match(ds_id):
        raise ValueError("id 须以字母开头，仅含字母数字 _ -")
    if ds_id in ("hotpot",):
        raise ValueError(f"保留 id 不可用: {ds_id}")
    passages = raw.get("passages")
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items 须为非空数组")

    intent_items = 0
    for row in items:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if row.get("intent_gt") or meta.get("intent_gt"):
            intent_items += 1
    is_intent_dataset = intent_items == len(items)

    if not isinstance(passages, list):
        raise ValueError("passages 须为数组")
    if not passages and not is_intent_dataset:
        raise ValueError("passages 须为非空数组（意图数据集除外）")
    if is_intent_dataset and intent_items < len(items):
        raise ValueError("意图数据集要求每题都有 intent_gt / meta.intent_gt")

    pids = set()
    for p in passages:
        if "pid" not in p or "text" not in p:
            raise ValueError("passage 须含 pid 与 text")
        pids.add(int(p["pid"]))
    qids: set[int] = set()
    for row in items:
        if "qid" not in row or "question" not in row:
            raise ValueError("item 须含 qid 与 question")
        qid = int(row["qid"])
        if qid in qids:
            raise ValueError(f"重复 qid: {qid}")
        qids.add(qid)
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        intent_gt = row.get("intent_gt") or meta.get("intent_gt")
        if is_intent_dataset and not intent_gt:
            raise ValueError(f"qid={qid} 缺少 intent_gt")
        for pid in row.get("pids") or []:
            if int(pid) not in pids:
                raise ValueError(f"qid={qid} 引用未知 pid={pid}")
    mode = raw.get("corpus_mode", "shared")
    if mode not in ("shared", "isolated"):
        raise ValueError("corpus_mode 须为 shared 或 isolated")
    return ds_id


def save_json_dataset(raw: dict[str, Any]) -> dict[str, str]:
    """校验并保存 JSON 数据集到 samples/{id}.json。"""
    ds_id = validate_json_dataset(raw)
    raw["id"] = ds_id
    _SAMPLES_ROOT.mkdir(parents=True, exist_ok=True)
    path = _json_dataset_path(ds_id)
    if path.exists():
        raise ValueError(f"数据集 {ds_id} 已存在，请换 id 或先删除")
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"id": ds_id, "format": "json", "path": str(path)}


def list_datasets() -> list[dict[str, str | int]]:
    out: list[dict[str, str | int]] = []
    _SAMPLES_ROOT.mkdir(parents=True, exist_ok=True)
    for path in sorted(_SAMPLES_ROOT.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            n_items = len(raw.get("items") or [])
            n_passages = len(raw.get("passages") or [])
            intent_n = sum(
                1
                for row in (raw.get("items") or [])
                if (isinstance(row.get("meta"), dict) and row["meta"].get("intent_gt"))
                or row.get("intent_gt")
            )
            kind = "intent" if intent_n == n_items and n_items > 0 else "rag"
            desc = (
                f"意图数据集（{n_items} 题）"
                if kind == "intent"
                else f"JSON 数据集（{n_items} 题 / {n_passages} 段）"
            )
        except Exception:
            n_items, n_passages, kind, desc = 0, 0, "rag", "JSON 数据集"
        out.append(
            {
                "id": path.stem,
                "format": "json",
                "kind": kind,
                "description": desc,
                "item_count": n_items,
            }
        )
    for path in sorted(_SAMPLES_ROOT.glob("*/queries.parquet")):
        ds_id = path.parent.name
        out.append({"id": ds_id, "format": "parquet", "description": f"Parquet 数据集 {ds_id}", "item_count": 0})
    out.append(
        {
            "id": "hotpot",
            "format": "hotpot",
            "description": "HotpotQA distractor（在线加载，RAGAS / rag_bench）",
            "item_count": 0,
        }
    )
    return out


def load_dataset(dataset_id: str, *, sample_limit: int | None = None, seed: int = 42) -> EvalDataset:
    if dataset_id == "hotpot":
        from evals.datasets.hotpot import load_hotpot_dataset

        return load_hotpot_dataset(sample_limit=sample_limit, seed=seed)
    json_path = _SAMPLES_ROOT / f"{dataset_id}.json"
    if json_path.exists():
        return _load_json(json_path, sample_limit=sample_limit)
    parquet_dir = _SAMPLES_ROOT / dataset_id
    if (parquet_dir / "queries.parquet").exists():
        return _load_parquet(parquet_dir, sample_limit=sample_limit)
    raise FileNotFoundError(f"未知数据集: {dataset_id}")


def _load_json(path: Path, *, sample_limit: int | None) -> EvalDataset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    passages = [
        Passage(pid=int(p["pid"]), title=str(p.get("title") or f"passage_{p['pid']}"), text=str(p["text"]))
        for p in raw.get("passages", [])
    ]
    pid_to_text = {p.pid: p.text for p in passages}
    items: list[QAPair] = []
    for row in raw.get("items", []):
        pids = [int(x) for x in row.get("pids", [])]
        meta = dict(row.get("meta") or {}) if isinstance(row.get("meta"), dict) else {}
        # 兼容顶层 intent 字段
        for key in (
            "intent_gt",
            "needs_retrieval_gt",
            "history",
            "kb_selected",
            "has_images",
            "has_attachments",
            "rewrite_gt",
        ):
            if key in row and key not in meta:
                meta[key] = row[key]
        items.append(
            QAPair(
                qid=int(row["qid"]),
                question=str(row["question"]),
                pids=pids,
                passages=[pid_to_text.get(pid, "") for pid in pids],
                answer=str(row.get("answer") or ""),
                meta=meta,
            )
        )
    if sample_limit is not None:
        items = items[:sample_limit]
    return EvalDataset(
        id=str(raw.get("id") or path.stem),
        corpus_mode=raw.get("corpus_mode", "shared"),
        passages=passages,
        items=items,
    )


def _load_parquet(dir_path: Path, *, sample_limit: int | None) -> EvalDataset:
    import pandas as pd

    queries = pd.read_parquet(dir_path / "queries.parquet")
    corpus = pd.read_parquet(dir_path / "corpus.parquet")
    answers = pd.read_parquet(dir_path / "answers.parquet")
    qrels = pd.read_parquet(dir_path / "qrels.parquet")
    qas = pd.read_parquet(dir_path / "qas.parquet")

    corpus_map = {int(r.id): str(r.text) for r in corpus.itertuples(index=False)}
    answer_map = {int(r.id): str(r.text) for r in answers.itertuples(index=False)}
    query_map = {int(r.id): str(r.text) for r in queries.itertuples(index=False)}

    qid_to_pids: dict[int, list[int]] = {}
    for r in qrels.itertuples(index=False):
        qid_to_pids.setdefault(int(r.qid), []).append(int(r.pid))

    passages = [
        Passage(pid=pid, title=f"passage_{pid}", text=text)
        for pid, text in sorted(corpus_map.items())
    ]

    items: list[QAPair] = []
    for r in qas.itertuples(index=False):
        qid, aid = int(r.qid), int(r.aid)
        pids = qid_to_pids.get(qid, [])
        items.append(
            QAPair(
                qid=qid,
                question=query_map.get(qid, ""),
                pids=pids,
                passages=[corpus_map.get(pid, "") for pid in pids],
                answer=answer_map.get(aid, ""),
            )
        )
    if sample_limit is not None:
        items = items[:sample_limit]
    return EvalDataset(id=dir_path.name, corpus_mode="shared", passages=passages, items=items)
