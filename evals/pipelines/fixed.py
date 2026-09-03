"""rag_fixed：检索 + 一次 LLM 生成（对齐 WeKnora Pipeline rag）。"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from evals.corpus import map_retrieval_ids
from evals.metrics.aggregate import compute_sample_metrics, metric_input_from_item
from evals.pipelines.agent import eval_system_prompt
from evals.schemas import QAPair, SampleResult
from models import create_chat_model
from tools.retrieval.doc_retrieval import doc_retrieval

_RAG_PROMPT = """你是 KnowSphere 评测助手。仅依据下列检索上下文回答问题，简洁准确。
若上下文不足以回答，请说明未找到相关信息。

【检索上下文】
{context}

【问题】
{question}"""

_SQUAD_RAG_PROMPT = """Use ONLY the retrieved context.
If the answer is present, reply with a short span copied from the context. No extra words.
If the context does not contain the answer, reply exactly: unanswerable

【Context】
{context}

【Question】
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
        if metric_layers and "squad" in metric_layers:
            system = eval_system_prompt(metric_layers)
            template = _SQUAD_RAG_PROMPT
        else:
            system = "评测模式：基于检索上下文作答。"
            template = _RAG_PROMPT
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=template.format(context=context or "（无）", question=item.question)),
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
