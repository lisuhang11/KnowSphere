"""数据集元数据：保存 / 列表 / 预览 / 补丁 / 删除。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.datasets import (
    BUILTIN_JSON_IDS,
    delete_json_dataset,
    get_dataset_preview,
    list_datasets,
    patch_json_dataset,
    save_json_dataset,
)


def _payload(ds_id: str = "my_eval_set") -> dict:
    return {
        "id": ds_id,
        "description": "园区导览冒烟集",
        "source": "manual",
        "passages": [{"pid": 0, "title": "A", "text": "hello world"}],
        "items": [{"qid": 0, "question": "q?", "pids": [0], "answer": "hello"}],
    }


def _squad_article() -> dict:
    return {
        "title": "Normans",
        "paragraphs": [
            {
                "context": "The Normans were the people who in the 10th and 11th centuries gave their name to Normandy, a region in France.",
                "qas": [
                    {
                        "question": "In what country is Normandy located?",
                        "id": "n1",
                        "answers": [{"text": "France", "answer_start": 110}],
                        "is_impossible": False,
                    },
                    {
                        "question": "What is France a region of?",
                        "id": "n2",
                        "answers": [],
                        "is_impossible": True,
                    },
                ],
            }
        ],
    }


def test_list_datasets_includes_metadata():
    rows = list_datasets()
    by_id = {d["id"]: d for d in rows}
    campus = by_id["campus_demo"]
    assert campus["builtin"] is True
    assert campus["item_count"] == 3
    assert campus["passage_count"] == 3
    assert campus["kind"] == "rag"
    assert by_id["hotpot"]["online"] is True
    assert by_id["squad_v2"]["builtin"] is True


def test_save_patch_preview_delete(monkeypatch, tmp_path: Path):
    import evals.datasets as ds_mod

    monkeypatch.setattr(ds_mod, "_SAMPLES_ROOT", tmp_path)
    saved = save_json_dataset(_payload())
    assert saved["id"] == "my_eval_set"
    assert saved["description"] == "园区导览冒烟集"
    assert saved["source"] == "manual"
    assert saved["created_at"]
    assert (tmp_path / "my_eval_set.json").exists()

    with pytest.raises(ValueError, match="已存在"):
        save_json_dataset(_payload())

    saved2 = save_json_dataset({**_payload(), "description": "覆盖后"}, overwrite=True)
    assert saved2["description"] == "覆盖后"
    assert saved2["created_at"] == saved["created_at"]

    patched = patch_json_dataset("my_eval_set", description="改描述", source="unit-test")
    assert patched["description"] == "改描述"
    assert patched["source"] == "unit-test"

    preview = get_dataset_preview("my_eval_set")
    assert preview["items"][0]["question"] == "q?"
    assert preview["passages"][0]["pid"] == 0
    assert preview["stats"]["item_count"] == 1

    delete_json_dataset("my_eval_set")
    assert not (tmp_path / "my_eval_set.json").exists()
    with pytest.raises(FileNotFoundError):
        delete_json_dataset("my_eval_set")


def test_can_delete_online_virtual(monkeypatch, tmp_path: Path):
    import evals.datasets as ds_mod

    monkeypatch.setattr(ds_mod, "_SAMPLES_ROOT", tmp_path)
    ids = {d["id"] for d in list_datasets()}
    assert "hotpot" in ids
    assert "squad_v2" in ids

    delete_json_dataset("hotpot")
    ids = {d["id"] for d in list_datasets()}
    assert "hotpot" not in ids
    assert "squad_v2" in ids
    with pytest.raises(FileNotFoundError):
        get_dataset_preview("hotpot")
    with pytest.raises(FileNotFoundError):
        delete_json_dataset("hotpot")

    delete_json_dataset("squad_v2")
    ids = {d["id"] for d in list_datasets()}
    assert "squad_v2" not in ids


def test_squad_article_upload_auto_id(monkeypatch, tmp_path: Path):
    import evals.datasets as ds_mod
    from evals.datasets import load_dataset

    monkeypatch.setattr(ds_mod, "_SAMPLES_ROOT", tmp_path)
    saved = save_json_dataset(_squad_article())
    assert saved["id"].startswith("ds_")
    assert saved["format"] == "squad_article"
    assert saved["item_count"] == 2
    assert saved["passage_count"] == 1

    stored = json.loads((tmp_path / f"{saved['id']}.json").read_text(encoding="utf-8"))
    assert stored["title"] == "Normans"
    assert "passages" not in stored
    assert "items" not in stored

    ds = load_dataset(saved["id"])
    assert len(ds.items) == 2
    assert len(ds.passages) == 1
    assert ds.items[0].answer == "France"
    assert ds.items[1].meta.get("is_impossible") is True

    preview = get_dataset_preview(saved["id"])
    assert preview["stats"]["noans_count"] == 1


def test_cannot_overwrite_shipped():
    assert "squad_normans" in BUILTIN_JSON_IDS
    raw = json.loads(
        (Path(__file__).resolve().parents[1] / "evals/datasets/samples/campus_demo.json").read_text(encoding="utf-8")
    )
    with pytest.raises(ValueError, match="不可覆盖"):
        save_json_dataset(raw, overwrite=True)
