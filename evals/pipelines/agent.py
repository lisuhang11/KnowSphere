"""rag_agent：完整 LangGraph 智能推理（ReAct + last_sources）。"""

from __future__ import annotations

import time
from typing import Any

from agents.agent import build_agent
from config.settings import settings
from evals.corpus import map_retrieval_ids
from evals.metrics.aggregate import compute_sample_metrics, metric_input_from_item
from evals.schemas import QAPair, SampleResult
from tools.retrieval.doc_retrieval import doc_retrieval

EVAL_SYSTEM_PROMPT = """You are KnowSphere, evaluated on a document corpus.

Rules:
1. Always call doc_retrieval first; ground every answer in retrieved passages.
2. If the passages do not contain the answer, state clearly what is missing.
3. Answer concisely in the same language as the question.
4. Do not call web_search."""

SQUAD_EVAL_SYSTEM_PROMPT = """You are KnowSphere, evaluated on SQuAD-style extractive QA.

Rules:
1. Always call doc_retrieval first; ground the answer in retrieved passages.
2. If the answer is present, reply with a short span copied from the passages. No extra words.
3. If the passages do not contain the answer, reply exactly: unanswerable
4. Do not explain. Do not call web_search."""


def eval_system_prompt(metric_layers: list[str] | None) -> str:
    if metric_layers and "squad" in metric_layers:
        return SQUAD_EVAL_SYSTEM_PROMPT
    return EVAL_SYSTEM_PROMPT


def _extract(result: dict) -> tuple[str, list[dict]]:
    answer = ""
    for m in result.get("messages") or []:
        if getattr(m, "type", "") == "ai" and m.content:
            answer = m.content if isinstance(m.content, str) else str(m.content)
    sources = [s for s in (result.get("last_sources") or []) if isinstance(s, dict)]
    return answer, sources


def run_rag_agent(
    item: QAPair,
    *,
    kb_id: int,
    agent=None,
    chat_model_kwargs: dict[str, Any] | None = None,
    metric_layers: list[str] | None = None,
) -> SampleResult:
    t0 = time.perf_counter()
    _kwargs = chat_model_kwargs or {"temperature": 0, "extra_body": {"enable_thinking": False}}
    try:
        graph = agent or build_agent(
            system_prompt=eval_system_prompt(metric_layers),
            tools=[doc_retrieval],
            chat_model_kwargs=_kwargs,
        )
        result = graph.invoke(
            {"messages": [{"role": "user", "content": item.question}]},
            config={
                "configurable": {"kb_ids": [kb_id]},
                "recursion_limit": settings.agent_max_steps,
            },
        )
        answer, sources = _extract(result)
        retrieval_ids = map_retrieval_ids(sources, item)
        metrics = compute_sample_metrics(
            metric_input_from_item(item, generated_text=answer, retrieval_ids=retrieval_ids),
            layers=metric_layers,
        )
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=item.answer,
            response=answer,
            retrieval_ids=retrieval_ids,
            retrieval_gt=item.pids,
            metrics=metrics,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as exc:  # noqa: BLE001
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=item.answer,
            response="",
            retrieval_ids=[],
            retrieval_gt=item.pids,
            metrics=compute_sample_metrics(
                metric_input_from_item(item, generated_text="", retrieval_ids=[]),
                layers=metric_layers,
            ),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=str(exc),
        )
