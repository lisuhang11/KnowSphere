"""评测 REST API（对齐  POST/GET /evaluation）。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.tasks import run_evaluation_task
from config.settings import get_current_owner, settings
from evals.datasets import list_datasets, save_json_dataset, validate_json_dataset
from evals.schemas import EvalConfig
from utils.eval_store import EvalStore

evaluation_router = APIRouter(prefix="/evaluation", tags=["evaluation"])

class EvaluationRequest(BaseModel):
    dataset_id: str = "campus_demo"
    suite: Literal["rag_bench", "rag_quality", "intent_bench"] = "rag_bench"
    pipeline_profile: Literal["rag_fixed", "rag_agent", "intent"] = "rag_fixed"
    corpus_mode: Literal["shared", "isolated"] | None = None
    sample_limit: int | None = Field(default=None, ge=1, le=500)
    kb_template_id: int | None = None
    chat_model_id: str | None = None
    rerank_model_id: str | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    metrics: list[str] | None = Field(
        default=None,
        description="指标层：retrieval / generation / ragas / intent；缺省随 suite 选择",
    )
    workers: int = Field(default=4, ge=1, le=16)

class DatasetUploadRequest(BaseModel):
    id: str | None = None
    corpus_mode: Literal["shared", "isolated"] = "shared"
    passages: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]]

def _default_metrics(suite: str) -> list[str]:
    if suite == "rag_quality":
        return ["ragas"]
    if suite == "intent_bench":
        return ["intent"]
    return ["retrieval", "generation"]

def _build_config(req: EvaluationRequest) -> EvalConfig:
    owner = get_current_owner() or settings.default_owner
    overrides = dict(req.config_overrides)
    if req.chat_model_id:
        overrides.setdefault("chat_model", req.chat_model_id)
    if req.rerank_model_id:
        overrides.setdefault("rerank_model", req.rerank_model_id)
    if req.suite == "intent_bench":
        profile: Literal["rag_fixed", "rag_agent", "intent"] = "intent"
    elif req.suite == "rag_quality":
        profile = "rag_agent"
    else:
        profile = req.pipeline_profile
    return EvalConfig(
        dataset_id=req.dataset_id,
        suite=req.suite,
        pipeline_profile=profile,
        corpus_mode=req.corpus_mode or "shared",
        sample_limit=req.sample_limit,
        kb_template_id=req.kb_template_id,
        chat_model_id=req.chat_model_id,
        rerank_model_id=req.rerank_model_id,
        config_overrides=overrides,
        metric_layers=req.metrics or _default_metrics(req.suite),
        workers=req.workers,
        owner=owner,
    )

@evaluation_router.get("/tasks")
def list_evaluation_tasks(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    owner = get_current_owner() or settings.default_owner
    store = EvalStore()
    tasks = store.list_tasks(owner=owner, limit=limit, offset=offset)
    total = store.count_tasks(owner=owner)
    return {"success": True, "data": {"items": tasks, "total": total}}

@evaluation_router.post("")
def create_evaluation(req: EvaluationRequest) -> dict[str, Any]:
    """创建评测任务（异步 Celery 执行）。"""
    if req.suite == "rag_quality" and req.pipeline_profile == "rag_fixed":
        raise HTTPException(status_code=400, detail="rag_quality 仅支持 pipeline_profile=rag_agent")
    if req.suite == "intent_bench" and req.pipeline_profile not in ("intent", "rag_agent", "rag_fixed"):
        raise HTTPException(status_code=400, detail="intent_bench 将强制使用 intent pipeline")
    if req.suite in ("rag_quality", "intent_bench", "rag_bench"):
        try:
            from evals.datasets import load_dataset

            if req.dataset_id != "hotpot":
                ds = load_dataset(req.dataset_id, sample_limit=1)
                if req.suite == "intent_bench":
                    if not (ds.items and (ds.items[0].meta or {}).get("intent_gt")):
                        raise HTTPException(
                            status_code=400,
                            detail="intent_bench 需要含 meta.intent_gt 的意图数据集（如 intent_demo）",
                        )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
    config = _build_config(req)
    store = EvalStore()
    task = store.create_task(config)
    run_evaluation_task.delay(task["id"])
    return {"success": True, "data": task}

@evaluation_router.get("")
def get_evaluation(task_id: str = Query(..., description="任务 ID")) -> dict[str, Any]:
    """查询评测任务状态与指标。"""
    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task}

@evaluation_router.get("/datasets")
def get_datasets() -> dict[str, Any]:
    return {"success": True, "data": list_datasets()}

@evaluation_router.post("/datasets")
def upload_dataset(req: DatasetUploadRequest) -> dict[str, Any]:
    """上传 JSON 评测数据集（保存到 evals/datasets/samples/{id}.json）。"""
    payload = req.model_dump()
    if req.id:
        payload["id"] = req.id
    try:
        validate_json_dataset(payload)
        saved = save_json_dataset(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": saved}

@evaluation_router.get("/{task_id}/samples")
def get_evaluation_samples(
    task_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = EvalStore()
    if not store.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = store.list_samples(task_id, limit=limit, offset=offset)
    total = store.count_samples(task_id)
    return {"success": True, "data": {"items": rows, "total": total}}
