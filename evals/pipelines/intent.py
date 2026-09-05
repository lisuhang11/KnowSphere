"""intent_bench：仅跑 query_understand，评测意图分类与检索路由。"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage

from agents.nodes.query_understand import query_understand
from evals.metrics.intent import compute_intent_metrics
from evals.retry import call_with_tpm_retry
from evals.schemas import QAPair, SampleMetrics, SampleResult
from schemas.query import needs_retrieval


def _meta_bool(meta: dict[str, Any], key: str, default: bool = False) -> bool:
    val = meta.get(key, default)
    return bool(val)


def run_intent_item(item: QAPair, *, chat_model_id: str | None = None) -> SampleResult:
    """对单题调用 query_understand，产出意图指标。"""
    t0 = time.perf_counter()
    meta = dict(item.meta or {})
    intent_gt = str(meta.get("intent_gt") or "").strip()
    history = meta.get("history") or []
    if not isinstance(history, list):
        history = []
    history_pairs = [
        {"query": str(h.get("query") or ""), "answer": str(h.get("answer") or "")}
        for h in history
        if isinstance(h, dict)
    ]
    kb_selected = _meta_bool(meta, "kb_selected", True)
    has_images = _meta_bool(meta, "has_images", False)
    has_attachments = _meta_bool(meta, "has_attachments", False)
    needs_gt = meta.get("needs_retrieval_gt")
    if needs_gt is not None:
        needs_gt = bool(needs_gt)

    state = {
        "current_query": item.question,
        "history_pairs": history_pairs,
        "kb_selected": kb_selected,
        "has_images": has_images,
        "has_attachments": has_attachments,
        "messages": [HumanMessage(content=item.question)],
        "rewrite_query": item.question,
    }

    try:
        from utils.observability import attach_langfuse

        invoke_cfg = attach_langfuse(
            {"configurable": {"chat_model_id": chat_model_id}} if chat_model_id else {},
            name="eval_intent",
            user_id="eval",
            session_id=f"eval-intent-{item.qid}",
            tags=["eval", "intent"],
        )
        out = call_with_tpm_retry(
            lambda: query_understand(
                state,
                invoke_cfg,
            )
        )
        pred = str(out.get("intent") or "").strip()
        rewrite = str(out.get("rewrite_query") or item.question).strip()
        intent_metrics = compute_intent_metrics(
            intent_gt=intent_gt,
            intent_pred=pred,
            question=item.question,
            kb_selected=kb_selected,
            history_pairs=history_pairs,
            needs_retrieval_gt=needs_gt,
            has_images=has_images,
            has_attachments=has_attachments,
        )
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=intent_gt,
            response=pred,
            retrieval_ids=[],
            retrieval_gt=[],
            metrics=SampleMetrics(intent=intent_metrics),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            details={"rewrite_pred": rewrite, "kb_selected": kb_selected},
        )
    except Exception as exc:
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=intent_gt,
            response="",
            retrieval_ids=[],
            retrieval_gt=[],
            metrics=SampleMetrics(
                intent=compute_intent_metrics(
                    intent_gt=intent_gt,
                    intent_pred="",
                    question=item.question,
                    kb_selected=kb_selected,
                    history_pairs=history_pairs,
                    needs_retrieval_gt=needs_gt,
                    has_images=has_images,
                    has_attachments=has_attachments,
                )
            ),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=str(exc),
        )


def expected_needs_retrieval(item: QAPair) -> bool:
    """调试/校验用：根据 gold 意图推导是否应检索。"""
    meta = item.meta or {}
    if "needs_retrieval_gt" in meta:
        return bool(meta["needs_retrieval_gt"])
    return needs_retrieval(
        str(meta.get("intent_gt") or None),
        bool(meta.get("kb_selected", True)),
    )
