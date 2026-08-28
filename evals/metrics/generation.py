"""生成指标：BLEU / ROUGE（轻量实现，无额外依赖）。"""

from __future__ import annotations

import math
import re
from collections import Counter

from evals.schemas import GenerationMetrics

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    text = (text or "").strip().lower()
    if not text:
        return []
    if _CJK.search(text):
        return list(text.replace(" ", ""))
    return text.split()


def _ngrams(tokens: list[str], n: int) -> Counter[tuple[str, ...]]:
    if len(tokens) < n:
        return Counter()
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _bleu(ref: list[str], hyp: list[str], max_n: int) -> float:
    if not hyp:
        return 0.0
    weights = [1.0 / max_n] * max_n
    log_avg = 0.0
    for n in range(1, max_n + 1):
        ref_ngrams = _ngrams(ref, n)
        hyp_ngrams = _ngrams(hyp, n)
        if not hyp_ngrams:
            return 0.0
        overlap = sum(min(hyp_ngrams[ng], ref_ngrams[ng]) for ng in hyp_ngrams)
        precision = overlap / max(sum(hyp_ngrams.values()), 1)
        if precision == 0:
            return 0.0
        log_avg += weights[n - 1] * math.log(precision)
    bp = 1.0 if len(hyp) >= len(ref) else math.exp(1 - len(ref) / max(len(hyp), 1))
    return bp * math.exp(log_avg)


def _lcs_len(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]


def _rouge_f1(ref: list[str], hyp: list[str], n: int) -> float:
    ref_ngrams = _ngrams(ref, n)
    hyp_ngrams = _ngrams(hyp, n)
    if not ref_ngrams or not hyp_ngrams:
        return 0.0
    overlap = sum(min(hyp_ngrams[ng], ref_ngrams[ng]) for ng in hyp_ngrams)
    p = overlap / max(sum(hyp_ngrams.values()), 1)
    r = overlap / max(sum(ref_ngrams.values()), 1)
    return 2 * p * r / (p + r + 1e-8)


def _rouge_l_f1(ref: list[str], hyp: list[str]) -> float:
    lcs = _lcs_len(ref, hyp)
    if lcs == 0:
        return 0.0
    p = lcs / max(len(hyp), 1)
    r = lcs / max(len(ref), 1)
    return 2 * p * r / (p + r + 1e-8)


def compute_generation_metrics(reference: str, response: str) -> GenerationMetrics:
    ref = _tokenize(reference)
    hyp = _tokenize(response)
    return GenerationMetrics(
        bleu1=_bleu(ref, hyp, 1),
        bleu2=_bleu(ref, hyp, 2),
        bleu4=_bleu(ref, hyp, 4),
        rouge1=_rouge_f1(ref, hyp, 1),
        rouge2=_rouge_f1(ref, hyp, 2),
        rougel=_rouge_l_f1(ref, hyp),
    )
