"""SQuAD 2.0 → EvalDataset（shared 语料，段落即 passage）。

支持：
- 官方 explorer JSON（data[].paragraphs[].qas）
- HuggingFace `squad_v2` 扁平行
- 按 title 过滤（如 Normans）后写成 samples/*.json
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from evals.schemas import EvalDataset, Passage, QAPair

SQUAD_DEV_V2_URLS = (
    "https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v2.0.json",
    "https://raw.githubusercontent.com/rajpurkar/SQuAD-explorer/master/dataset/dev-v2.0.json",
)
_SAMPLES_ROOT = Path(__file__).resolve().parent / "samples"
_RESERVED_IDS = frozenset({"hotpot", "squad_v2"})


def _looks_like_parquet(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"PAR1"
    except OSError:
        return False


def _unique_texts(texts: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _norm_title(value: str) -> str:
    return str(value or "").strip().lower().replace("_", " ")


def generate_dataset_id() -> str:
    """生成唯一数据集 id：ds_YYYYMMDD_HHMMSS_xxxx。"""
    now = datetime.now(UTC)
    suffix = secrets.token_hex(2)
    return f"ds_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def is_squad_article(raw: dict[str, Any]) -> bool:
    paragraphs = raw.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        return False
    first = paragraphs[0]
    return isinstance(first, dict) and "context" in first and "qas" in first


def validate_squad_article(raw: dict[str, Any]) -> None:
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("SQuAD 数据集缺少 title")
    paragraphs = raw.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ValueError("paragraphs 须为非空数组")
    has_qa = False
    for idx, para in enumerate(paragraphs):
        if not isinstance(para, dict):
            raise ValueError(f"paragraphs[{idx}] 须为对象")
        if not str(para.get("context") or "").strip():
            raise ValueError(f"paragraphs[{idx}] 缺少 context")
        qas = para.get("qas")
        if not isinstance(qas, list):
            raise ValueError(f"paragraphs[{idx}].qas 须为数组")
        for qa in qas:
            if not isinstance(qa, dict):
                continue
            if not str(qa.get("question") or "").strip():
                raise ValueError("qas 须含 question")
            has_qa = True
    if not has_qa:
        raise ValueError("至少需要一个问答")


def squad_article_counts(raw: dict[str, Any]) -> tuple[int, int, int, int]:
    """返回 (题数, 段数, HasAns, NoAns)。"""
    n_passages = 0
    n_items = 0
    n_noans = 0
    for para in raw.get("paragraphs") or []:
        if not str(para.get("context") or "").strip():
            continue
        n_passages += 1
        for qa in para.get("qas") or []:
            if not isinstance(qa, dict):
                continue
            if not str(qa.get("question") or "").strip():
                continue
            n_items += 1
            answers = qa.get("answers") or []
            impossible = bool(qa.get("is_impossible")) or not answers
            if impossible:
                n_noans += 1
    n_has = max(n_items - n_noans, 0)
    return n_items, n_passages, n_has, n_noans


def _convert_squad_articles(
    articles: list[dict[str, Any]],
    *,
    dataset_id: str,
    sample_limit: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    passages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    pid = 0
    qid = 0
    for article in articles:
        art_title = str(article.get("title") or "unknown")
        for para in article.get("paragraphs") or []:
            context = str(para.get("context") or "").strip()
            if not context:
                continue
            passages.append({"pid": pid, "title": f"{art_title}#{pid}", "text": context})
            for qa in para.get("qas") or []:
                ans_objs = qa.get("answers") or []
                answers = [str(a.get("text") or "") for a in ans_objs if isinstance(a, dict)]
                items.append(
                    _item_dict(
                        qid=qid,
                        question=str(qa.get("question") or ""),
                        pid=pid,
                        answers=answers,
                        is_impossible=bool(qa.get("is_impossible")),
                        squad_id=str(qa.get("id") or f"q{qid}"),
                        title=art_title,
                    )
                )
                qid += 1
            pid += 1
    return _finalize_dataset(dataset_id, passages, items, sample_limit=sample_limit, seed=seed)


def squad_article_to_eval_dataset(
    raw: dict[str, Any],
    *,
    dataset_id: str = "",
    sample_limit: int | None = None,
    seed: int = 42,
) -> EvalDataset:
    validate_squad_article(raw)
    ds_id = dataset_id or str(raw.get("id") or "squad")
    payload = _convert_squad_articles(
        [{"title": raw["title"], "paragraphs": raw["paragraphs"]}],
        dataset_id=ds_id,
        sample_limit=sample_limit,
        seed=seed,
    )
    return dataset_from_payload(payload)


def _item_dict(
    *,
    qid: int,
    question: str,
    pid: int,
    answers: list[str],
    is_impossible: bool,
    squad_id: str,
    title: str,
) -> dict[str, Any]:
    golds = _unique_texts(answers)
    impossible = bool(is_impossible) or not golds
    return {
        "qid": qid,
        "question": str(question or "").strip(),
        "pids": [pid],
        "answer": "" if impossible else golds[0],
        "meta": {
            "squad_id": squad_id,
            "is_impossible": impossible,
            "answers": [] if impossible else golds,
            "title": title,
        },
    }


def convert_squad_official(
    raw: dict[str, Any],
    *,
    dataset_id: str,
    title: str | None = None,
    sample_limit: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """官方 SQuAD JSON → KnowSphere JSON 数据集。"""
    articles = list(raw.get("data") or [])
    if title:
        wanted = _norm_title(title)
        articles = [a for a in articles if _norm_title(str(a.get("title") or "")) == wanted]
        if not articles:
            raise ValueError(f"SQuAD 中没有 title={title!r} 的文章")

    return _convert_squad_articles(
        articles,
        dataset_id=dataset_id,
        sample_limit=sample_limit,
        seed=seed,
    )


def convert_squad_hf_rows(
    rows: Iterable[dict[str, Any]],
    *,
    dataset_id: str,
    title: str | None = None,
    sample_limit: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """HuggingFace squad_v2 扁平行 → KnowSphere JSON 数据集。"""
    wanted = _norm_title(title) if title else None
    context_to_pid: dict[str, int] = {}
    passages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    qid = 0
    for row in rows:
        art_title = str(row.get("title") or "unknown")
        if wanted and _norm_title(art_title) != wanted:
            continue
        context = str(row.get("context") or "").strip()
        if not context:
            continue
        pid = context_to_pid.get(context)
        if pid is None:
            pid = len(passages)
            context_to_pid[context] = pid
            passages.append({"pid": pid, "title": f"{art_title}#{pid}", "text": context})
        answers_obj = row.get("answers") or {}
        if isinstance(answers_obj, dict):
            texts = answers_obj.get("text")
        else:
            texts = None
        if texts is None:
            answer_list: list[str] = []
        else:
            answer_list = [str(t) for t in list(texts)]
        items.append(
            _item_dict(
                qid=qid,
                question=str(row.get("question") or ""),
                pid=pid,
                answers=answer_list,
                is_impossible=not answer_list,
                squad_id=str(row.get("id") or f"q{qid}"),
                title=art_title,
            )
        )
        qid += 1
    if title and not items:
        raise ValueError(f"SQuAD 中没有 title={title!r} 的文章")
    return _finalize_dataset(dataset_id, passages, items, sample_limit=sample_limit, seed=seed)


def _finalize_dataset(
    dataset_id: str,
    passages: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    sample_limit: int | None,
    seed: int,
) -> dict[str, Any]:
    if sample_limit is not None:
        rng = random.Random(seed)
        if sample_limit < len(items):
            items = rng.sample(items, sample_limit)
            items.sort(key=lambda row: int(row["qid"]))
        keep_pids = {int(pid) for row in items for pid in row.get("pids") or []}
        passages = [p for p in passages if int(p["pid"]) in keep_pids]
        for i, row in enumerate(items):
            row["qid"] = i
    if not passages or not items:
        raise ValueError("转换结果为空：没有可用段落或问题")
    n_has = sum(1 for row in items if not row.get("meta", {}).get("is_impossible"))
    n_no = len(items) - n_has
    title = None
    if items:
        title = (items[0].get("meta") or {}).get("title")
    source = f"squad_v2:{title}" if title else "squad_v2"
    if title:
        description = (
            f"SQuAD 2.0 · {title}。{len(items)} 题 / {len(passages)} 段"
            f"（HasAns={n_has}, NoAns={n_no}），用于单文档阅读理解与拒答评测。"
        )
    else:
        description = (
            f"SQuAD 2.0 validation。{len(items)} 题 / {len(passages)} 段"
            f"（HasAns={n_has}, NoAns={n_no}）。"
        )
    return {
        "id": dataset_id,
        "description": description,
        "source": source,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "passages": passages,
        "items": items,
    }


def dataset_from_payload(payload: dict[str, Any]) -> EvalDataset:
    passages = [
        Passage(pid=int(p["pid"]), title=str(p.get("title") or f"passage_{p['pid']}"), text=str(p["text"]))
        for p in payload.get("passages") or []
    ]
    pid_to_text = {p.pid: p.text for p in passages}
    items: list[QAPair] = []
    for row in payload.get("items") or []:
        pids = [int(x) for x in row.get("pids") or []]
        items.append(
            QAPair(
                qid=int(row["qid"]),
                question=str(row["question"]),
                pids=pids,
                passages=[pid_to_text.get(pid, "") for pid in pids],
                answer=str(row.get("answer") or ""),
                meta=dict(row.get("meta") or {}),
            )
        )
    return EvalDataset(
        id=str(payload.get("id") or "squad"),
        passages=passages,
        items=items,
    )


def fetch_squad_official(url: str | None = None) -> dict[str, Any]:
    urls = (url,) if url else SQUAD_DEV_V2_URLS
    last_error: Exception | None = None
    for item in urls:
        try:
            with urlopen(item, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — 逐源尝试下载
            last_error = exc
    raise RuntimeError(f"无法下载 SQuAD 官方 JSON，请改用 --source。最后错误: {last_error}")


def load_squad_official(
    source: str | Path | None = None,
    *,
    dataset_id: str,
    title: str | None = None,
    sample_limit: int | None = None,
    seed: int = 42,
) -> EvalDataset:
    if source is None:
        raw = fetch_squad_official()
    else:
        raw = json.loads(Path(source).read_text(encoding="utf-8"))
    payload = convert_squad_official(
        raw, dataset_id=dataset_id, title=title, sample_limit=sample_limit, seed=seed
    )
    return dataset_from_payload(payload)


def load_squad_v2_dataset(
    *,
    sample_limit: int | None = None,
    seed: int = 42,
    title: str | None = None,
) -> EvalDataset:
    """加载 SQuAD 2.0 validation：优先本地缓存的官方 dev-v2.0.json。"""
    from evals.datasets.squad_dev import is_squad_dev_cached, load_squad_dev_raw

    if is_squad_dev_cached():
        raw = load_squad_dev_raw()
        payload = convert_squad_official(
            raw, dataset_id="squad_v2", title=title, sample_limit=sample_limit, seed=seed
        )
        return dataset_from_payload(payload)
    import os

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    import datasets

    last_error: Exception | None = None
    ds = None
    for name in ("rajpurkar/squad_v2", "squad_v2"):
        try:
            ds = datasets.load_dataset(name, split="validation")
            break
        except Exception as exc:  # noqa: BLE001 — 数据集 id 在不同 Hub 版本上不一致
            last_error = exc
    if ds is None:
        raise RuntimeError(f"无法加载 HuggingFace squad_v2: {last_error}")
    if title:
        wanted = _norm_title(title)
        ds = ds.filter(lambda row: _norm_title(row.get("title") or "") == wanted)
    payload = convert_squad_hf_rows(
        ds, dataset_id="squad_v2", title=None, sample_limit=sample_limit, seed=seed
    )
    return dataset_from_payload(payload)


def payload_from_source(
    source: Path,
    *,
    dataset_id: str,
    title: str | None = None,
    sample_limit: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    if source.suffix.lower() == ".parquet" or _looks_like_parquet(source):
        import pandas as pd

        df = pd.read_parquet(source)
        return convert_squad_hf_rows(
            df.to_dict(orient="records"),
            dataset_id=dataset_id,
            title=title,
            sample_limit=sample_limit,
            seed=seed,
        )
    raw = json.loads(source.read_text(encoding="utf-8"))
    return convert_squad_official(
        raw, dataset_id=dataset_id, title=title, sample_limit=sample_limit, seed=seed
    )


def dump_squad_json(
    payload: dict[str, Any],
    dest: Path,
    *,
    overwrite: bool = False,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"数据集已存在: {dest}")
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="将 SQuAD 2.0 转为 KnowSphere JSON 评测集")
    parser.add_argument("--source", help="本地官方 JSON 或 HuggingFace parquet；缺省则下载 dev-v2.0.json")
    parser.add_argument("--title", default="Normans", help="文章 title，空字符串表示全量")
    parser.add_argument("--id", dest="dataset_id", default="squad_normans")
    parser.add_argument("--out", default=None, help="输出路径，默认 samples/{id}.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.dataset_id in _RESERVED_IDS:
        raise SystemExit(f"保留 id 不可写出: {args.dataset_id}")

    title = args.title.strip() or None
    if args.source:
        payload = payload_from_source(
            Path(args.source),
            dataset_id=args.dataset_id,
            title=title,
            sample_limit=args.limit,
            seed=args.seed,
        )
    else:
        print("下载 SQuAD official JSON")
        raw = fetch_squad_official()
        payload = convert_squad_official(
            raw,
            dataset_id=args.dataset_id,
            title=title,
            sample_limit=args.limit,
            seed=args.seed,
        )
    out = Path(args.out) if args.out else _SAMPLES_ROOT / f"{args.dataset_id}.json"
    dump_squad_json(payload, out, overwrite=args.overwrite)
    n_has = sum(1 for row in payload["items"] if not row["meta"]["is_impossible"])
    n_no = len(payload["items"]) - n_has
    print(f"已写入 {out}：{len(payload['items'])} 题（HasAns={n_has}, NoAns={n_no}）/ {len(payload['passages'])} 段")


if __name__ == "__main__":
    main()
