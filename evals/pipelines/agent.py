"""rag_agent：完整 LangGraph 智能推理（ReAct + last_sources）。"""

from __future__ import annotations

import time
from typing import Any

from agents.agent import build_agent
from config.settings import settings
from evals.corpus import map_retrieval_ids
from evals.metrics.aggregate import compute_sample_metrics, metric_input_from_item
from evals.retry import call_with_tpm_retry
from evals.schemas import QAPair, SampleResult
from prompts import build_system_prompt
from tools.retrieval.doc_retrieval import doc_retrieval


def eval_system_prompt(metric_layers: list[str] | None = None) -> str:
    """评测与产品共用 WeKnora 风格 Agent 系统提示（仅绑定检索工具）。"""
    _ = metric_layers
    return build_system_prompt(enable_citation=False, tool_names=["doc_retrieval"])


EVAL_SYSTEM_PROMPT = eval_system_prompt()


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
        invoke_cfg = {
            "configurable": {"kb_ids": [kb_id]},
            "recursion_limit": settings.agent_max_steps,
        }
        model_id = (_kwargs.get("model") if isinstance(_kwargs.get("model"), str) else None) or None
        if model_id:
            invoke_cfg["configurable"]["chat_model_id"] = model_id
        result = call_with_tpm_retry(
            lambda: graph.invoke(
                {"messages": [{"role": "user", "content": item.question}]},
                config=invoke_cfg,
            )
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
