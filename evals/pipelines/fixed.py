"""rag_fixed：检索 + 一次 LLM 生成（对齐 WeKnora Pipeline rag）。"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from evals.corpus import map_retrieval_ids
from evals.schemas import QAPair, SampleResult
from evals.metrics.aggregate import compute_sample_metrics
from evals.schemas import MetricInput
from models import create_chat_model
from tools.retrieval.doc_retrieval import doc_retrieval

_RAG_PROMPT = """你是 KnowSphere 评测助手。仅依据下列检索上下文回答问题，简洁准确。
若上下文不足以回答，请说明未找到相关信息。

【检索上下文】
{context}

【问题】
{question}"""


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
            SystemMessage(content="评测模式：基于检索上下文作答。"),
            HumanMessage(content=_RAG_PROMPT.format(context=context or "（无）", question=item.question)),
        ]
        response = llm.invoke(messages)
        text = response.content if isinstance(response.content, str) else str(response.content)
        metrics = compute_sample_metrics(
            MetricInput(
                retrieval_gt=[item.pids],
                retrieval_ids=retrieval_ids,
                generated_text=text,
                generated_gt=item.answer,
            ),
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
    except Exception as exc:
        return SampleResult(
            qid=item.qid,
            question=item.question,
            reference=item.answer,
            response="",
            retrieval_ids=[],
            retrieval_gt=item.pids,
            metrics=compute_sample_metrics(
                MetricInput([item.pids], [], "", item.answer), layers=metric_layers
            ),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=str(exc),
        )
