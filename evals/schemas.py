"""评测领域模型：数据集样本、指标输入/输出、任务配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SuiteName = Literal["rag_bench", "rag_quality", "intent_bench"]
PipelineProfile = Literal["rag_fixed", "rag_agent", "intent"]
TaskStatus = Literal["pending", "running", "success", "failed"]

@dataclass
class Passage:
    pid: int
    title: str
    text: str

@dataclass
class QAPair:
    qid: int
    question: str
    pids: list[int]
    passages: list[str]
    answer: str
    meta: dict[str, Any] = field(default_factory=dict)

@dataclass
class EvalDataset:
    id: str
    passages: list[Passage]
    items: list[QAPair]

@dataclass
class MetricInput:
    retrieval_gt: list[list[int]]
    retrieval_ids: list[int]
    generated_text: str
    generated_gt: str
    answers: list[str] = field(default_factory=list)
    is_impossible: bool = False

@dataclass
class RetrievalMetrics:
    precision: float = 0.0
    recall: float = 0.0
    ndcg3: float = 0.0
    ndcg10: float = 0.0
    mrr: float = 0.0
    map: float = 0.0

@dataclass
class GenerationMetrics:
    bleu1: float = 0.0
    bleu2: float = 0.0
    bleu4: float = 0.0
    rouge1: float = 0.0
    rouge2: float = 0.0
    rougel: float = 0.0

@dataclass
class IntentMetrics:
    """单题意图指标：correct / routing_correct 为 0/1，汇总时取均值。"""

    correct: float = 0.0
    routing_correct: float = 0.0


@dataclass
class SquadMetrics:
    """SQuAD 2.0 单题指标。em/f1 为官方口径；span_hit 看 gold span 是否出现在回答中。"""

    em: float = 0.0
    f1: float = 0.0
    span_hit: float = 0.0
    abstained: float = 0.0
    impossible: float = 0.0


@dataclass
class SampleMetrics:
    retrieval: RetrievalMetrics | None = None
    generation: GenerationMetrics | None = None
    ragas: dict[str, float] | None = None
    intent: IntentMetrics | None = None
    squad: SquadMetrics | None = None

@dataclass
class EvalConfig:
    dataset_id: str
    suite: SuiteName = "rag_bench"
    pipeline_profile: PipelineProfile = "rag_fixed"
    sample_limit: int | None = None
    kb_template_id: int | None = None
    chat_model_id: str | None = None
    rerank_model_id: str | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)
    metric_layers: list[str] = field(default_factory=lambda: ["retrieval", "generation"])
    workers: int = 4
    owner: str = "eval"

    def snapshot(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "suite": self.suite,
            "pipeline_profile": self.pipeline_profile,
            "sample_limit": self.sample_limit,
            "kb_template_id": self.kb_template_id,
            "chat_model_id": self.chat_model_id,
            "rerank_model_id": self.rerank_model_id,
            "config_overrides": self.config_overrides,
            "metric_layers": self.metric_layers,
            "workers": self.workers,
        }

@dataclass
class SampleResult:
    qid: int
    question: str
    reference: str
    response: str
    retrieval_ids: list[int]
    retrieval_gt: list[int]
    metrics: SampleMetrics
    latency_ms: int = 0
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
