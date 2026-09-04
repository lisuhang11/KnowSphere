"""rag_fixed：检索 + 一次 LLM 生成（对齐 WeKnora Pipeline rag）。"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from evals.corpus import map_retrieval_ids
from evals.metrics.aggregate import compute_sample_metrics, metric_input_from_item
from evals.schemas import QAPair, SampleResult
from models import create_chat_model
from prompts.rag_system import build_rag_system_prompt, format_rag_user_message
from tools.retrieval.doc_retrieval import doc_retrieval


def run_rag_fixed(
    item: QAPair,
    *,
    kb_id: int,
    chat_model_kwargs: dict[str, Any] | None = None,
    metric_layers: list[str] | None = None,
) -> SampleResult:
    t0 = time.perf_counter()
    config = {"configurable": {"kb_ids": [kb_id]}}
    try:
        retrieval = doc_retrieval.invoke({"query": item.question}, config=config)
        sources = retrieval.get("sources") or []
        retrieval_ids = map_retrieval_ids(sources, item)
        context = "\n\n".join(
            f"[{i + 1}] {s.get('file_name', '')}: {s.get('snippet', '')}" for i, s in enumerate(sources)
        )
        llm = create_chat_model(**(chat_model_kwargs or {"temperature": 0}))
        messages = [
            SystemMessage(content=build_rag_system_prompt(enable_citation=False)),
            HumanMessage(content=format_rag_user_message(item.question, context)),
        ]
        response = llm.invoke(messages)
        text = response.content if isinstance(response.content, str) else str(response.content)
        metrics = compute_sample_metrics(
            metric_input_from_item(item, generated_text=text, retrieval_ids=retrieval_ids),
            layers=metric_layers,
        )
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=item.answer,
            response=text,
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
