"""SQuAD dev 缓存与 context 分页。"""

from __future__ import annotations

import json

import pytest

from evals.datasets.squad_dev import (
    _flatten_official,
    _flatten_squad_article,
    ensure_squad_dev_cached,
    get_squad_article_contexts,
    get_squad_dev_contexts,
    squad_dev_stats,
)

_MINI = {
    "data": [
        {
            "title": "Normans",
            "paragraphs": [
                {
                    "context": "The Normans were in Normandy, France.",
                    "qas": [
                        {
                            "id": "1",
                            "question": "Where is Normandy?",
                            "answers": [{"text": "France"}],
                            "is_impossible": False,
                        },
                        {
                            "id": "2",
                            "question": "What is France?",
                            "answers": [],
                            "is_impossible": True,
                        },
                    ],
                },
                {
                    "context": "The Duchy was founded in 911.",
                    "qas": [
                        {
                            "id": "3",
                            "question": "When founded?",
                            "answers": [{"text": "911"}],
                            "is_impossible": False,
                        }
                    ],
                },
            ],
        }
    ]
}


def test_flatten_and_paginate_official(tmp_path, monkeypatch):
    import evals.datasets.squad_dev as mod

    cache = tmp_path / "dev.json"
    cache.write_text(json.dumps(_MINI), encoding="utf-8")
    monkeypatch.setattr(mod, "_DEV_CACHE_PATH", cache)
    monkeypatch.setattr(mod, "_CACHE_ROOT", tmp_path)
    mod._flatten_official.cache_clear()

    stats = squad_dev_stats()
    assert stats["passage_count"] == 2
    assert stats["item_count"] == 3
    assert stats["noans_count"] == 1

    page1 = get_squad_dev_contexts(offset=0, limit=1)
    assert len(page1["contexts"]) == 1
    assert page1["contexts"][0]["question_count"] == 2
    assert page1["total_contexts"] == 2

    page2 = get_squad_dev_contexts(offset=1, limit=1)
    assert page2["contexts"][0]["qas"][0]["answer"] == "911"


def test_squad_article_contexts_pagination():
    raw = {
        "title": "Normans",
        "paragraphs": _MINI["data"][0]["paragraphs"],
    }
    page = get_squad_article_contexts(raw, dataset_id="normans", offset=0, limit=1)
    assert page["total_contexts"] == 2
    assert page["contexts"][0]["qas"][0]["answer"] == "France"


def test_ensure_download_writes_cache(tmp_path, monkeypatch):
    import evals.datasets.squad_dev as mod

    cache = tmp_path / "dev.json"
    monkeypatch.setattr(mod, "_DEV_CACHE_PATH", cache)
    monkeypatch.setattr(mod, "_CACHE_ROOT", tmp_path)
    monkeypatch.setattr(mod, "fetch_squad_official", lambda: _MINI)
    mod._flatten_official.cache_clear()

    path = ensure_squad_dev_cached()
    assert path.exists()
    contexts, articles = _flatten_official(path.read_text(encoding="utf-8"))
    assert len(contexts) == 2
    assert articles == ["Normans"]
