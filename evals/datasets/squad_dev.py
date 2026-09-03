"""SQuAD 2.0 dev-v2.0 官方 JSON：下载缓存、统计与按 context 分页浏览。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from evals.datasets.squad import SQUAD_DEV_V2_URLS, _unique_texts, fetch_squad_official, is_squad_article

_CACHE_ROOT = Path(__file__).resolve().parents[2] / "data" / "cache"
_DEV_CACHE_PATH = _CACHE_ROOT / "squad_dev-v2.0.json"


def squad_dev_cache_path() -> Path:
    return _DEV_CACHE_PATH


def is_squad_dev_cached() -> bool:
    return _DEV_CACHE_PATH.exists()


def ensure_squad_dev_cached(*, force: bool = False) -> Path:
    """下载并缓存官方 dev-v2.0.json（若尚未存在）。"""
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    if force and _DEV_CACHE_PATH.exists():
        _DEV_CACHE_PATH.unlink()
        _flatten_official.cache_clear()
    if not _DEV_CACHE_PATH.exists():
        raw = fetch_squad_official()
        _DEV_CACHE_PATH.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        _flatten_official.cache_clear()
    return _DEV_CACHE_PATH


def load_squad_dev_raw() -> dict[str, Any]:
    path = ensure_squad_dev_cached()
    return json.loads(path.read_text(encoding="utf-8"))


def _qa_row(qa: dict[str, Any]) -> dict[str, Any]:
    answers = [str(a.get("text") or "") for a in qa.get("answers") or [] if isinstance(a, dict)]
    golds = _unique_texts(answers)
    impossible = bool(qa.get("is_impossible")) or not golds
    return {
        "id": str(qa.get("id") or ""),
        "question": str(qa.get("question") or ""),
        "answers": golds,
        "answer": "" if impossible else golds[0],
        "is_impossible": impossible,
    }


def _context_row(*, index: int, article_title: str, context: str, qas: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [_qa_row(qa) for qa in qas if isinstance(qa, dict)]
    noans = sum(1 for row in rows if row["is_impossible"])
    return {
        "index": index,
        "article_title": article_title,
        "context": context,
        "qas": rows,
        "question_count": len(rows),
        "hasans_count": max(len(rows) - noans, 0),
        "noans_count": noans,
    }


@lru_cache(maxsize=1)
def _flatten_official(raw_json: str) -> tuple[list[dict[str, Any]], list[str]]:
    raw = json.loads(raw_json)
    contexts: list[dict[str, Any]] = []
    articles: list[str] = []
    ctx_idx = 0
    for article in raw.get("data") or []:
        art_title = str(article.get("title") or "unknown")
        articles.append(art_title)
        for para in article.get("paragraphs") or []:
            context = str(para.get("context") or "").strip()
            if not context:
                continue
            qas = [qa for qa in (para.get("qas") or []) if isinstance(qa, dict)]
            if not qas:
                continue
            contexts.append(
                _context_row(index=ctx_idx, article_title=art_title, context=context, qas=qas)
            )
            ctx_idx += 1
    return contexts, articles


def _flatten_squad_article(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    title = str(raw.get("title") or "unknown")
    contexts: list[dict[str, Any]] = []
    ctx_idx = 0
    for para in raw.get("paragraphs") or []:
        context = str(para.get("context") or "").strip()
        if not context:
            continue
        qas = [qa for qa in (para.get("qas") or []) if isinstance(qa, dict)]
        if not qas:
            continue
        contexts.append(_context_row(index=ctx_idx, article_title=title, context=context, qas=qas))
        ctx_idx += 1
    return contexts, [title]


def squad_dev_stats() -> dict[str, Any]:
    path = ensure_squad_dev_cached()
    contexts, articles = _flatten_official(path.read_text(encoding="utf-8"))
    n_q = sum(c["question_count"] for c in contexts)
    n_no = sum(c["noans_count"] for c in contexts)
    return {
        "item_count": n_q,
        "passage_count": len(contexts),
        "article_count": len(articles),
        "hasans_count": max(n_q - n_no, 0),
        "noans_count": n_no,
        "cached": True,
        "cache_path": str(path),
    }


def squad_dev_articles() -> list[dict[str, Any]]:
    path = ensure_squad_dev_cached()
    contexts, article_titles = _flatten_official(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    q_counts: dict[str, int] = {}
    for ctx in contexts:
        title = ctx["article_title"]
        counts[title] = counts.get(title, 0) + 1
        q_counts[title] = q_counts.get(title, 0) + ctx["question_count"]
    return [
        {"title": title, "context_count": counts.get(title, 0), "question_count": q_counts.get(title, 0)}
        for title in article_titles
    ]


def paginate_squad_contexts(
    contexts: list[dict[str, Any]],
    *,
    offset: int,
    limit: int,
    title: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if title:
        filtered = [c for c in contexts if c["article_title"] == title]
    else:
        filtered = contexts
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return page, total


def get_squad_dev_contexts(*, offset: int = 0, limit: int = 5, title: str | None = None) -> dict[str, Any]:
    path = ensure_squad_dev_cached()
    contexts, _ = _flatten_official(path.read_text(encoding="utf-8"))
    page, total = paginate_squad_contexts(contexts, offset=offset, limit=limit, title=title)
    stats = squad_dev_stats()
    return {
        "dataset_id": "squad_v2",
        "view": "contexts",
        "offset": offset,
        "limit": limit,
        "total_contexts": total if title else stats["passage_count"],
        "total_questions": stats["item_count"],
        "title_filter": title,
        "contexts": page,
    }


def get_squad_article_contexts(
    raw: dict[str, Any],
    *,
    dataset_id: str,
    offset: int = 0,
    limit: int = 5,
) -> dict[str, Any]:
    if not is_squad_article(raw):
        raise ValueError("非 SQuAD article 格式")
    contexts, _ = _flatten_squad_article(raw)
    page, total = paginate_squad_contexts(contexts, offset=offset, limit=limit)
    n_q = sum(c["question_count"] for c in contexts)
    n_no = sum(c["noans_count"] for c in contexts)
    return {
        "dataset_id": dataset_id,
        "view": "contexts",
        "offset": offset,
        "limit": limit,
        "total_contexts": total,
        "total_questions": n_q,
        "title_filter": str(raw.get("title") or None),
        "stats": {
            "item_count": n_q,
            "passage_count": len(contexts),
            "hasans_count": max(n_q - n_no, 0),
            "noans_count": n_no,
        },
        "contexts": page,
    }


def knowsphere_contexts_from_raw(
    raw: dict[str, Any],
    *,
    dataset_id: str,
    offset: int = 0,
    limit: int = 5,
) -> dict[str, Any]:
    """旧 passages/items 格式：按 passage 分组展示。"""
    passages = {int(p["pid"]): p for p in raw.get("passages") or [] if "pid" in p}
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in raw.get("items") or []:
        pids = row.get("pids") or []
        pid = int(pids[0]) if pids else -1
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        impossible = bool(meta.get("is_impossible") or row.get("is_impossible"))
        answer = str(row.get("answer") or "")
        if not answer and not impossible and meta.get("intent_gt"):
            answer = str(meta.get("intent_gt"))
        grouped.setdefault(pid, []).append(
            {
                "id": str(row.get("qid")),
                "question": str(row.get("question") or ""),
                "answers": [answer] if answer else [],
                "answer": answer,
                "is_impossible": impossible,
                "intent_gt": meta.get("intent_gt"),
            }
        )
    contexts: list[dict[str, Any]] = []
    for idx, pid in enumerate(sorted(grouped.keys())):
        p = passages.get(pid, {})
        text = str(p.get("text") or "")
        qas = grouped[pid]
        noans = sum(1 for q in qas if q.get("is_impossible"))
        contexts.append(
            {
                "index": idx,
                "article_title": str(p.get("title") or f"passage_{pid}"),
                "context": text,
                "qas": qas,
                "question_count": len(qas),
                "hasans_count": max(len(qas) - noans, 0),
                "noans_count": noans,
            }
        )
    page, total = paginate_squad_contexts(contexts, offset=offset, limit=limit)
    n_q = sum(c["question_count"] for c in contexts)
    return {
        "dataset_id": dataset_id,
        "view": "contexts",
        "offset": offset,
        "limit": limit,
        "total_contexts": total,
        "total_questions": n_q,
        "title_filter": None,
        "contexts": page,
    }
