"""数据集加载：JSON 内置集、Parquet（WeKnora 兼容）、HotpotQA。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.datasets.squad import (
    generate_dataset_id,
    is_squad_article,
    squad_article_counts,
    squad_article_to_eval_dataset,
    validate_squad_article,
)
from evals.schemas import EvalDataset, Passage, QAPair

_SAMPLES_ROOT = Path(__file__).resolve().parent / "samples"
_REMOVED_NAME = ".removed.json"
_DATASET_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
BUILTIN_JSON_IDS = frozenset({"campus_demo", "intent_demo", "knowsphere_eval_20", "squad_normans"})
VIRTUAL_DATASET_IDS = frozenset({"hotpot", "squad_v2"})
BUILTIN_DATASET_IDS = BUILTIN_JSON_IDS | VIRTUAL_DATASET_IDS
_PREVIEW_TEXT = 240
_PREVIEW_ITEMS = 20
_PREVIEW_PASSAGES = 30


def _removed_path() -> Path:
    return _SAMPLES_ROOT / _REMOVED_NAME


def _load_removed() -> set[str]:
    path = _removed_path()
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    ids = data.get("ids") if isinstance(data, dict) else data
    if not isinstance(ids, list):
        return set()
    return {str(x) for x in ids}


def _save_removed(ids: set[str]) -> None:
    _SAMPLES_ROOT.mkdir(parents=True, exist_ok=True)
    path = _removed_path()
    if not ids:
        if path.exists():
            path.unlink()
        return
    path.write_text(json.dumps({"ids": sorted(ids)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_dataset_available(dataset_id: str) -> None:
    if dataset_id in VIRTUAL_DATASET_IDS:
        if dataset_id in _load_removed():
            raise FileNotFoundError(f"未知数据集: {dataset_id}")
        return
    if _json_dataset_path(dataset_id).exists():
        return
    if (_SAMPLES_ROOT / dataset_id / "queries.parquet").exists():
        return
    raise FileNotFoundError(f"未知数据集: {dataset_id}")


def _json_dataset_path(dataset_id: str) -> Path:
    return _SAMPLES_ROOT / f"{dataset_id}.json"


def _validate_dataset_id(ds_id: str) -> None:
    if not ds_id:
        raise ValueError("缺少 id 字段")
    if not _DATASET_ID_RE.match(ds_id):
        raise ValueError("id 须以字母开头，仅含字母数字 _ -")
    if ds_id in ("hotpot", "squad_v2"):
        raise ValueError(f"保留 id 不可用: {ds_id}")


def validate_knowsphere_dataset(raw: dict[str, Any]) -> str:
    """校验旧版 KnowSphere JSON（passages + items），返回 dataset id。"""
    ds_id = str(raw.get("id") or "").strip()
    if not ds_id:
        raise ValueError("缺少 id 字段")
    _validate_dataset_id(ds_id)
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
    return ds_id


def validate_json_dataset(raw: dict[str, Any]) -> str:
    """校验上传数据集，返回 id。支持 SQuAD article 与旧 KnowSphere 格式。"""
    if is_squad_article(raw):
        validate_squad_article(raw)
        ds_id = str(raw.get("id") or "").strip() or generate_dataset_id()
        _validate_dataset_id(ds_id)
        return ds_id
    return validate_knowsphere_dataset(raw)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_intent_item(row: dict[str, Any]) -> bool:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return bool(row.get("intent_gt") or meta.get("intent_gt"))


def _is_impossible_item(row: dict[str, Any]) -> bool:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    if meta.get("is_impossible") or row.get("is_impossible"):
        return True
    return not str(row.get("answer") or "").strip() and not _is_intent_item(row)


def _fallback_description(
    ds_id: str,
    *,
    kind: str,
    n_items: int,
    n_passages: int,
    n_noans: int = 0,
    title: str | None = None,
    items: list[dict[str, Any]] | None = None,
) -> str:
    if kind == "intent":
        return f"意图数据集（{n_items} 题）"
    if title or ds_id.startswith("squad") or ds_id.startswith("ds_"):
        label = title or ds_id
        if n_noans:
            return f"SQuAD 2.0 · {label}（{n_items} 题 / {n_passages} 段，NoAns={n_noans}）"
        return f"SQuAD 2.0 · {label}（{n_items} 题 / {n_passages} 段）"
    if items is not None:
        noans = sum(1 for row in items if _is_impossible_item(row))
        if ds_id.startswith("squad"):
            return f"SQuAD 2.0（{n_items} 题 / {n_passages} 段，NoAns={noans}）"
    return f"JSON 数据集（{n_items} 题 / {n_passages} 段）"


def _dataset_counts(raw: dict[str, Any]) -> tuple[int, int, str, str]:
    """返回 (item_count, passage_count, kind, format)。"""
    if is_squad_article(raw):
        n_items, n_passages, _, n_noans = squad_article_counts(raw)
        return n_items, n_passages, "rag", "squad_article"
    items = raw.get("items") or []
    passages = raw.get("passages") or []
    intent_n = sum(1 for row in items if _is_intent_item(row))
    kind = "intent" if intent_n == len(items) and len(items) > 0 else "rag"
    return len(items), len(passages), kind, "json"


def _json_listing(path: Path, raw: dict[str, Any] | None = None) -> dict[str, Any]:
    data = raw if raw is not None else json.loads(path.read_text(encoding="utf-8"))
    n_items, n_passages, kind, fmt = _dataset_counts(data)
    stored = str(data.get("description") or "").strip()
    title = str(data.get("title") or "").strip() or None
    if is_squad_article(data):
        _, _, _, n_noans = squad_article_counts(data)
    else:
        n_noans = sum(1 for row in (data.get("items") or []) if _is_impossible_item(row))
    return {
        "id": str(data.get("id") or path.stem),
        "format": fmt,
        "kind": kind,
        "description": stored
        or _fallback_description(
            path.stem,
            kind=kind,
            n_items=n_items,
            n_passages=n_passages,
            n_noans=n_noans,
            title=title,
            items=data.get("items") if not is_squad_article(data) else None,
        ),
        "source": str(data.get("source") or "").strip() or None,
        "created_at": data.get("created_at") or None,
        "item_count": n_items,
        "passage_count": n_passages,
        "builtin": path.stem in BUILTIN_JSON_IDS,
        "online": False,
    }


def save_json_dataset(raw: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
    """校验并保存 JSON 数据集到 samples/{id}.json。"""
    payload = dict(raw)
    ds_id = validate_json_dataset(payload)
    payload["id"] = ds_id
    if is_squad_article(payload):
        payload.setdefault("source", f"squad_v2:{payload.get('title')}")
        for key in ("passages", "items", "corpus_mode", "overwrite"):
            payload.pop(key, None)
    _SAMPLES_ROOT.mkdir(parents=True, exist_ok=True)
    path = _json_dataset_path(ds_id)
    if path.exists() and not overwrite:
        raise ValueError(f"数据集 {ds_id} 已存在，请换 id 或先删除")
    if path.exists() and ds_id in BUILTIN_JSON_IDS:
        raise ValueError(f"内置数据集 {ds_id} 不可覆盖")
    if path.exists() and overwrite:
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = {}
        if old.get("created_at") and not payload.get("created_at"):
            payload["created_at"] = old["created_at"]
    payload.setdefault("created_at", _utc_now())
    if payload.get("description") is not None:
        payload["description"] = str(payload.get("description") or "").strip()
    if payload.get("source") is not None:
        payload["source"] = str(payload.get("source") or "").strip()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    listing = _json_listing(path, payload)
    listing["path"] = str(path)
    return listing


def patch_json_dataset(dataset_id: str, *, description: str | None = None, source: str | None = None) -> dict[str, Any]:
    if dataset_id in VIRTUAL_DATASET_IDS:
        raise ValueError("在线数据集不可编辑")
    path = _json_dataset_path(dataset_id)
    if not path.exists():
        raise FileNotFoundError(f"未知数据集: {dataset_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if description is not None:
        raw["description"] = description.strip()
    if source is not None:
        raw["source"] = source.strip()
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return _json_listing(path, raw)


def delete_json_dataset(dataset_id: str) -> None:
    if dataset_id in VIRTUAL_DATASET_IDS:
        removed = _load_removed()
        if dataset_id in removed:
            raise FileNotFoundError(f"未知数据集: {dataset_id}")
        removed.add(dataset_id)
        _save_removed(removed)
        return
    path = _json_dataset_path(dataset_id)
    if path.exists():
        path.unlink()
        return
    parquet_dir = _SAMPLES_ROOT / dataset_id
    if (parquet_dir / "queries.parquet").exists():
        import shutil

        shutil.rmtree(parquet_dir)
        return
    raise FileNotFoundError(f"未知数据集: {dataset_id}")


def get_dataset_preview(dataset_id: str, *, item_limit: int = _PREVIEW_ITEMS) -> dict[str, Any]:
    if dataset_id in VIRTUAL_DATASET_IDS:
        ensure_dataset_available(dataset_id)
    if dataset_id == "hotpot":
        return {
            "id": "hotpot",
            "format": "hotpot",
            "kind": "rag",
            "description": "HotpotQA distractor（在线加载，RAGAS / rag_bench）",
            "source": "hotpotqa/hotpot_qa",
            "created_at": None,
            "item_count": 0,
            "passage_count": 0,
            "builtin": True,
            "online": True,
            "stats": {},
            "items": [],
            "passages": [],
        }
    if dataset_id == "squad_v2":
        from evals.datasets.squad_dev import is_squad_dev_cached, squad_dev_stats

        stats: dict[str, Any] = {}
        item_count = 0
        passage_count = 0
        description = "SQuAD 2.0 validation（dev-v2.0，按 context 分页浏览）"
        if is_squad_dev_cached():
            try:
                stats = squad_dev_stats()
                item_count = stats.get("item_count", 0)
                passage_count = stats.get("passage_count", 0)
                description = (
                    f"SQuAD 2.0 validation。{item_count} 题 / {passage_count} 段 context"
                    f"（HasAns={stats.get('hasans_count', 0)}, NoAns={stats.get('noans_count', 0)}）"
                )
            except Exception:  # noqa: BLE001
                pass
        return {
            "id": "squad_v2",
            "format": "squad",
            "kind": "rag",
            "description": description,
            "source": "squad_v2",
            "created_at": None,
            "item_count": item_count,
            "passage_count": passage_count,
            "builtin": True,
            "online": True,
            "stats": stats,
            "items": [],
            "passages": [],
            "view": "contexts",
        }
    path = _json_dataset_path(dataset_id)
    if not path.exists():
        raise FileNotFoundError(f"未知数据集: {dataset_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    listing = _json_listing(path, raw)
    if is_squad_article(raw):
        ds = squad_article_to_eval_dataset(raw, dataset_id=str(raw.get("id") or dataset_id))
        items = [
            {
                "qid": item.qid,
                "question": item.question,
                "answer": item.answer,
                "is_impossible": bool(item.meta.get("is_impossible")),
                "intent_gt": item.meta.get("intent_gt"),
            }
            for item in ds.items[:item_limit]
        ]
        passages = [
            {"pid": p.pid, "title": p.title, "text": p.text[:_PREVIEW_TEXT]}
            for p in ds.passages[:_PREVIEW_PASSAGES]
        ]
        noans = sum(1 for item in ds.items if item.meta.get("is_impossible"))
        intent_n = sum(1 for item in ds.items if item.meta.get("intent_gt"))
        listing["stats"] = {
            "item_count": len(ds.items),
            "passage_count": len(ds.passages),
            "noans_count": noans,
            "hasans_count": max(len(ds.items) - noans, 0),
            "intent_count": intent_n,
        }
        listing["items"] = items
        listing["passages"] = passages
        return listing
    items = raw.get("items") or []
    passages = raw.get("passages") or []
    noans = sum(1 for row in items if _is_impossible_item(row))
    intent_n = sum(1 for row in items if _is_intent_item(row))
    listing["stats"] = {
        "item_count": len(items),
        "passage_count": len(passages),
        "noans_count": noans,
        "hasans_count": max(len(items) - noans, 0) if listing["kind"] != "intent" else 0,
        "intent_count": intent_n,
    }
    listing["items"] = [
        {
            "qid": row.get("qid"),
            "question": row.get("question"),
            "answer": row.get("answer") or "",
            "is_impossible": _is_impossible_item(row),
            "intent_gt": (row.get("meta") or {}).get("intent_gt") or row.get("intent_gt"),
        }
        for row in items[:item_limit]
    ]
    listing["passages"] = [
        {
            "pid": p.get("pid"),
            "title": p.get("title") or f"passage_{p.get('pid')}",
            "text": str(p.get("text") or "")[:_PREVIEW_TEXT],
        }
        for p in passages[:_PREVIEW_PASSAGES]
    ]
    return listing


def _eval_dataset_to_export_dict(ds: EvalDataset, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": ds.id,
        "passages": [{"pid": p.pid, "title": p.title, "text": p.text} for p in ds.passages],
        "items": [],
    }
    extra = extra or {}
    for key in ("description", "source", "created_at"):
        if extra.get(key):
            payload[key] = extra[key]
    items: list[dict[str, Any]] = []
    for item in ds.items:
        row: dict[str, Any] = {
            "qid": item.qid,
            "question": item.question,
            "pids": list(item.pids),
            "answer": item.answer,
        }
        if item.meta:
            row["meta"] = dict(item.meta)
        items.append(row)
    payload["items"] = items
    return payload


def dump_dataset_export(dataset_id: str) -> Path | dict[str, Any]:
    """导出完整数据集：本地 JSON 原样返回，其余转成可再导入的 KnowSphere JSON。"""
    ensure_dataset_available(dataset_id)
    json_path = _json_dataset_path(dataset_id)
    if json_path.exists():
        return json_path
    if dataset_id == "squad_v2":
        from evals.datasets.squad_dev import is_squad_dev_cached, squad_dev_cache_path

        if is_squad_dev_cached():
            return squad_dev_cache_path()
    ds = load_dataset(dataset_id)
    try:
        preview = get_dataset_preview(dataset_id)
    except Exception:  # noqa: BLE001
        preview = {}
    return _eval_dataset_to_export_dict(
        ds,
        extra={k: preview.get(k) for k in ("description", "source", "created_at")},
    )


def list_datasets() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _SAMPLES_ROOT.mkdir(parents=True, exist_ok=True)
    for path in sorted(_SAMPLES_ROOT.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            out.append(_json_listing(path))
        except Exception:  # noqa: BLE001 — 坏文件跳过内容解析
            out.append(
                {
                    "id": path.stem,
                    "format": "json",
                    "kind": "rag",
                    "description": "JSON 数据集",
                    "source": None,
                    "created_at": None,
                    "item_count": 0,
                    "passage_count": 0,
                    "builtin": path.stem in BUILTIN_JSON_IDS,
                    "online": False,
                }
            )
    for path in sorted(_SAMPLES_ROOT.glob("*/queries.parquet")):
        ds_id = path.parent.name
        out.append(
            {
                "id": ds_id,
                "format": "parquet",
                "kind": "rag",
                "description": f"Parquet 数据集 {ds_id}",
                "source": None,
                "created_at": None,
                "item_count": 0,
                "passage_count": 0,
                "builtin": False,
                "online": False,
            }
        )
    removed = _load_removed()
    if "hotpot" not in removed:
        out.append(
            {
                "id": "hotpot",
                "format": "hotpot",
                "kind": "rag",
                "description": "HotpotQA distractor（在线加载，RAGAS / rag_bench）",
                "source": "hotpotqa/hotpot_qa",
                "created_at": None,
                "item_count": 0,
                "passage_count": 0,
                "builtin": True,
                "online": True,
            }
        )
    if "squad_v2" not in removed and not any(d["id"] == "squad_v2" for d in out):
        squad_entry: dict[str, Any] = {
            "id": "squad_v2",
            "format": "squad",
            "kind": "rag",
            "description": "SQuAD 2.0 validation（dev-v2.0，点击预览同步数据）",
            "source": "squad_v2",
            "created_at": None,
            "item_count": 0,
            "passage_count": 0,
            "builtin": True,
            "online": True,
        }
        try:
            from evals.datasets.squad_dev import is_squad_dev_cached, squad_dev_stats

            if is_squad_dev_cached():
                st = squad_dev_stats()
                squad_entry["item_count"] = st.get("item_count", 0)
                squad_entry["passage_count"] = st.get("passage_count", 0)
                squad_entry["description"] = (
                    f"SQuAD 2.0 validation。{st['item_count']} 题 / {st['passage_count']} 段"
                )
        except Exception:  # noqa: BLE001
            pass
        out.append(squad_entry)
    return out


def get_dataset_contexts(
    dataset_id: str,
    *,
    offset: int = 0,
    limit: int = 5,
    title: str | None = None,
) -> dict[str, Any]:
    """按 context 分页返回段落及其问答（SQuAD / 本地集通用）。"""
    ensure_dataset_available(dataset_id)
    if dataset_id == "squad_v2":
        from evals.datasets.squad_dev import get_squad_dev_contexts

        return get_squad_dev_contexts(offset=offset, limit=limit, title=title)
    path = _json_dataset_path(dataset_id)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        if is_squad_article(raw):
            from evals.datasets.squad_dev import get_squad_article_contexts

            return get_squad_article_contexts(raw, dataset_id=dataset_id, offset=offset, limit=limit)
        from evals.datasets.squad_dev import knowsphere_contexts_from_raw

        return knowsphere_contexts_from_raw(raw, dataset_id=dataset_id, offset=offset, limit=limit)
    raise FileNotFoundError(f"未知数据集: {dataset_id}")


def list_squad_v2_articles() -> list[dict[str, Any]]:
    from evals.datasets.squad_dev import squad_dev_articles

    return squad_dev_articles()


def sync_squad_v2_dataset(*, force: bool = False) -> dict[str, Any]:
    from evals.datasets.squad_dev import ensure_squad_dev_cached, squad_dev_stats

    ensure_squad_dev_cached(force=force)
    stats = squad_dev_stats()
    return {"id": "squad_v2", **stats}


def load_dataset(dataset_id: str, *, sample_limit: int | None = None, seed: int = 42) -> EvalDataset:
    ensure_dataset_available(dataset_id)
    if dataset_id == "hotpot":
        from evals.datasets.hotpot import load_hotpot_dataset

        return load_hotpot_dataset(sample_limit=sample_limit, seed=seed)
    if dataset_id == "squad_v2":
        from evals.datasets.squad import load_squad_v2_dataset

        return load_squad_v2_dataset(sample_limit=sample_limit, seed=seed)
    json_path = _SAMPLES_ROOT / f"{dataset_id}.json"
    if json_path.exists():
        return _load_json(json_path, sample_limit=sample_limit)
    parquet_dir = _SAMPLES_ROOT / dataset_id
    if (parquet_dir / "queries.parquet").exists():
        return _load_parquet(parquet_dir, sample_limit=sample_limit)
    raise FileNotFoundError(f"未知数据集: {dataset_id}")


def _load_json(path: Path, *, sample_limit: int | None) -> EvalDataset:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if is_squad_article(raw):
        return squad_article_to_eval_dataset(
            raw,
            dataset_id=str(raw.get("id") or path.stem),
            sample_limit=sample_limit,
        )
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
            "is_impossible",
            "answers",
            "squad_id",
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
    if sample_limit is not None and sample_limit < len(items):
        # RAG/SQuAD 风格：有 passage 归属时按段整抽，避免同一 context 被拆成半题
        if passages and any(it.pids for it in items):
            from evals.datasets.squad import sample_items_by_passage

            pass_rows = [{"pid": p.pid, "title": p.title, "text": p.text} for p in passages]
            item_rows = [
                {
                    "qid": it.qid,
                    "question": it.question,
                    "pids": it.pids,
                    "answer": it.answer,
                    "meta": it.meta,
                }
                for it in items
            ]
            pass_rows, item_rows = sample_items_by_passage(
                pass_rows, item_rows, sample_limit=sample_limit, seed=42
            )
            pid_to_text = {int(p["pid"]): str(p["text"]) for p in pass_rows}
            passages = [
                Passage(pid=int(p["pid"]), title=str(p.get("title") or f"passage_{p['pid']}"), text=str(p["text"]))
                for p in pass_rows
            ]
            items = [
                QAPair(
                    qid=int(row["qid"]),
                    question=str(row["question"]),
                    pids=[int(x) for x in row.get("pids") or []],
                    passages=[pid_to_text.get(int(pid), "") for pid in (row.get("pids") or [])],
                    answer=str(row.get("answer") or ""),
                    meta=dict(row.get("meta") or {}),
                )
                for row in item_rows
            ]
        else:
            items = items[:sample_limit]
    return EvalDataset(
        id=str(raw.get("id") or path.stem),
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
    return EvalDataset(id=dir_path.name, passages=passages, items=items)
