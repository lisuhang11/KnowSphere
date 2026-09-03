"""SQuAD 2.0 官方 EM / token F1，外加 span_hit 与拒答判定。"""

from __future__ import annotations

import re
import string
from collections import Counter
from typing import Any

from evals.schemas import MetricInput, SampleResult, SquadMetrics

_ARTICLES = re.compile(r"\b(a|an|the)\b", re.UNICODE)
_ABSTAIN_RES = (
    re.compile(r"^unanswerable\.?$", re.IGNORECASE),
    re.compile(r"^no answer\.?$", re.IGNORECASE),
    re.compile(r"^n/?a\.?$", re.IGNORECASE),
    re.compile(r"^unknown\.?$", re.IGNORECASE),
    re.compile(r"^i do(?: not|n't) know\.?$", re.IGNORECASE),
    re.compile(r"^cannot answer\.?$", re.IGNORECASE),
    re.compile(r"not (?:enough|sufficient) information", re.IGNORECASE),
    re.compile(r"cannot be (?:answered|determined|found)", re.IGNORECASE),
    re.compile(r"(?:passage|context|document)s? (?:do not|does not|don't|didn't) contain", re.IGNORECASE),
    re.compile(r"(?:do not|does not|don't) contain the answer", re.IGNORECASE),
    re.compile(r"无法回答"),
    re.compile(r"不知道"),
    re.compile(r"未找到"),
    re.compile(r"找不到"),
    re.compile(r"没有[足够相]?关"),
)


def normalize_answer(text: str) -> str:
    """官方 evaluate-v2.0.py：小写、去冠词/标点、折叠空白。"""
    value = (text or "").lower()
    value = "".join(ch for ch in value if ch not in string.punctuation)
    value = _ARTICLES.sub(" ", value)
    return " ".join(value.split())


def is_abstain(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    compact = " ".join(stripped.split())
    if normalize_answer(compact) == "":
        return True
    return any(pat.search(compact) for pat in _ABSTAIN_RES)


def _tokens(text: str) -> list[str]:
    norm = normalize_answer(text)
    return norm.split() if norm else []


def exact_match(gold: str, pred: str) -> float:
    return 1.0 if normalize_answer(gold) == normalize_answer(pred) else 0.0


def token_f1(gold: str, pred: str) -> float:
    gold_toks = _tokens(gold)
    pred_toks = _tokens(pred)
    if not gold_toks or not pred_toks:
        return 1.0 if gold_toks == pred_toks else 0.0
    overlap = sum((Counter(gold_toks) & Counter(pred_toks)).values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred_toks)
    recall = overlap / len(gold_toks)
    return 2 * precision * recall / (precision + recall)


def max_over_gold(golds: list[str], pred: str) -> tuple[float, float]:
    refs = golds or [""]
    return (
        max(exact_match(g, pred) for g in refs),
        max(token_f1(g, pred) for g in refs),
    )


def span_contained(golds: list[str], pred: str) -> float:
    pred_norm = normalize_answer(pred)
    if not pred_norm:
        return 0.0
    for gold in golds:
        gold_norm = normalize_answer(gold)
        if gold_norm and gold_norm in pred_norm:
            return 1.0
    return 0.0


def scoring_prediction(text: str) -> str:
    return "" if is_abstain(text) else (text or "")


def gold_answers(inp: MetricInput) -> list[str]:
    if inp.is_impossible:
        return [""]
    answers = [str(a) for a in (inp.answers or []) if str(a).strip()]
    if answers:
        return answers
    if str(inp.generated_gt or "").strip():
        return [inp.generated_gt]
    return [""]


def compute_squad_metrics(inp: MetricInput) -> SquadMetrics:
    golds = gold_answers(inp)
    impossible = bool(inp.is_impossible or golds == [""])
    pred = scoring_prediction(inp.generated_text)
    em, f1 = max_over_gold(golds, pred)
    abstained = 1.0 if is_abstain(inp.generated_text) else 0.0
    if impossible:
        hit = abstained
    else:
        hit = span_contained([g for g in golds if g], inp.generated_text or "")
    return SquadMetrics(
        em=em,
        f1=f1,
        span_hit=hit,
        abstained=abstained,
        impossible=1.0 if impossible else 0.0,
    )


def aggregate_squad_metrics(results: list[SampleResult]) -> dict[str, Any]:
    rows = [r.metrics.squad for r in results if not r.error and r.metrics.squad]
    if not rows:
        return {}
    has_ans = [m for m in rows if m.impossible < 0.5]
    no_ans = [m for m in rows if m.impossible >= 0.5]

    def _mean(items: list[SquadMetrics], attr: str) -> float:
        if not items:
            return 0.0
        return sum(getattr(m, attr) for m in items) / len(items)

    return {
        "em": _mean(rows, "em"),
        "f1": _mean(rows, "f1"),
        "span_hit": _mean(has_ans, "span_hit") if has_ans else 0.0,
        "has_ans_em": _mean(has_ans, "em") if has_ans else 0.0,
        "has_ans_f1": _mean(has_ans, "f1") if has_ans else 0.0,
        "no_ans_acc": _mean(no_ans, "em") if no_ans else 0.0,
        "abstain_rate": _mean(rows, "abstained"),
        "has_ans_count": len(has_ans),
        "no_ans_count": len(no_ans),
    }
