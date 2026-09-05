"""评测 REST API（对齐  POST/GET /evaluation）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from api import celery_app
from api.tasks import run_evaluation_task
from config.settings import get_current_owner, settings
from evals.config import default_metric_layers
from evals.datasets import (
    delete_json_dataset,
    dump_dataset_export,
    ensure_dataset_available,
    get_dataset_contexts,
    get_dataset_preview,
    list_datasets,
    list_squad_v2_articles,
    patch_json_dataset,
    save_json_dataset,
    sync_squad_v2_dataset,
    validate_json_dataset,
)
from evals.schemas import EvalConfig
from utils.eval_store import EvalStore

evaluation_router = APIRouter(prefix="/evaluation", tags=["evaluation"])

class EvaluationRequest(BaseModel):
    dataset_id: str = "campus_demo"
    suite: Literal["rag_bench", "rag_quality", "intent_bench"] = "rag_bench"
    pipeline_profile: Literal["rag_fixed", "rag_agent", "intent"] = "rag_fixed"
    sample_limit: int | None = Field(default=None, ge=1, le=500)
    kb_template_id: int | None = None
    chat_model_id: str | None = None
    embedding_model_id: str | None = None
    ragas_model_id: str | None = None
    rerank_model_id: str | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    metrics: list[str] | None = Field(
        default=None,
        description="指标层：retrieval / generation / ragas / intent / squad；缺省随 suite 与数据集选择",
    )
    workers: int = Field(default=4, ge=1, le=16)

class DatasetUploadRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    description: str | None = None
    source: str | None = None
    overwrite: bool = False
    title: str | None = None
    paragraphs: list[dict[str, Any]] | None = None
    passages: list[dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None


class DatasetPatchRequest(BaseModel):
    description: str | None = None
    source: str | None = None

def _build_config(req: EvaluationRequest) -> EvalConfig:
    owner = get_current_owner() or settings.default_owner
    overrides = dict(req.config_overrides)
    if req.chat_model_id:
        overrides.setdefault("chat_model", req.chat_model_id)
    if req.embedding_model_id:
        overrides.setdefault("embedding_model", req.embedding_model_id)
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
        sample_limit=req.sample_limit,
        kb_template_id=req.kb_template_id,
        chat_model_id=req.chat_model_id,
        embedding_model_id=req.embedding_model_id,
        ragas_model_id=req.ragas_model_id,
        rerank_model_id=req.rerank_model_id,
        config_overrides=overrides,
        metric_layers=req.metrics or default_metric_layers(req.suite, req.dataset_id),
        workers=req.workers,
        owner=owner,
    )

@evaluation_router.get("/tasks")
def list_evaluation_tasks(
    limit: int = Query(200, ge=1, le=200),
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
        req.pipeline_profile = "rag_agent"
    if req.suite == "intent_bench" and req.pipeline_profile not in ("intent", "rag_agent", "rag_fixed"):
        raise HTTPException(status_code=400, detail="intent_bench 将强制使用 intent pipeline")
    if req.suite in ("rag_quality", "intent_bench", "rag_bench"):
        try:
            from evals.datasets import load_dataset

            ensure_dataset_available(req.dataset_id)
            if req.dataset_id not in ("hotpot", "squad_v2"):
                ds = load_dataset(req.dataset_id, sample_limit=1)
                if req.suite == "intent_bench" and not (
                    ds.items and (ds.items[0].meta or {}).get("intent_gt")
                ):
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
    try:
        async_result = run_evaluation_task.delay(task["id"])
        store.update_task(task["id"], celery_task_id=async_result.id)
        task["celery_task_id"] = async_result.id
    except Exception as exc:
        store.delete_task(task["id"])
        raise HTTPException(
            status_code=503,
            detail=(
                "评测任务入队失败，请确认 Redis 与 Celery worker 已启动。"
                " 启动 Redis: docker compose up -d redis；"
                " 启动 worker: uv run celery -A api.celery_app.celery worker -B --loglevel=info -Q documents。"
                f" 原因: {exc}"
            ),
        ) from exc
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
    overwrite = bool(payload.pop("overwrite", False))
    if req.id:
        payload["id"] = req.id
    try:
        validate_json_dataset(payload)
        saved = save_json_dataset(payload, overwrite=overwrite)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": saved}

@evaluation_router.get("/datasets/{dataset_id}/export")
def export_dataset(dataset_id: str) -> Response:
    """下载完整数据集 JSON（本地文件原样，在线集转成可再导入格式）。"""
    try:
        payload = dump_dataset_export(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    filename = f"{dataset_id}.json"
    if isinstance(payload, Path):
        return FileResponse(
            payload,
            media_type="application/json; charset=utf-8",
            filename=filename,
        )
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@evaluation_router.get("/datasets/{dataset_id}/contexts")
def get_dataset_contexts_view(
    dataset_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(5, ge=1, le=50),
    title: str | None = Query(None, description="SQuAD 文章 title 过滤"),
) -> dict[str, Any]:
    try:
        data = get_dataset_contexts(dataset_id, offset=offset, limit=limit, title=title)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "data": data}


@evaluation_router.get("/datasets/squad_v2/articles")
def get_squad_v2_articles() -> dict[str, Any]:
    try:
        items = list_squad_v2_articles()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "data": items}


@evaluation_router.post("/datasets/squad_v2/sync")
def sync_squad_v2(force: bool = Query(False, description="强制重新下载")) -> dict[str, Any]:
    try:
        saved = sync_squad_v2_dataset(force=force)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"success": True, "data": saved}


@evaluation_router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        preview = get_dataset_preview(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": preview}

@evaluation_router.patch("/datasets/{dataset_id}")
def patch_dataset(dataset_id: str, req: DatasetPatchRequest) -> dict[str, Any]:
    if req.description is None and req.source is None:
        raise HTTPException(status_code=400, detail="请提供 description 或 source")
    try:
        saved = patch_json_dataset(dataset_id, description=req.description, source=req.source)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": saved}

@evaluation_router.delete("/datasets/{dataset_id}")
def remove_dataset(dataset_id: str) -> dict[str, Any]:
    try:
        delete_json_dataset(dataset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "data": {"id": dataset_id}}

def _interrupt_evaluation(store: EvalStore, task: dict[str, Any]) -> None:
    was_running = task["status"] == "running"
    store.mark_cancelled(task["id"], err_msg="用户中断" if was_running else "用户取消")
    celery_id = task.get("celery_task_id")
    if celery_id:
        try:
            celery_app.celery.control.revoke(celery_id, terminate=was_running)
        except Exception:
            pass


@evaluation_router.post("/{task_id}/cancel")
def cancel_evaluation(task_id: str) -> dict[str, Any]:
    """取消排队中的任务，或中断正在运行的评测。已有题目时会直接汇总到评测结果。"""
    from evals.finalize import NoEvalSamples, finalize_task_results

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"当前状态为 {task['status']}，无法取消")
    _interrupt_evaluation(store, task)
    try:
        updated = finalize_task_results(task_id, partial=True)
    except (NoEvalSamples, FileNotFoundError):
        updated = store.get_task(task_id)
    return {"success": True, "data": updated}


@evaluation_router.post("/{task_id}/results")
def produce_evaluation_results(task_id: str) -> dict[str, Any]:
    """产出评测结果：运行中先中断，再按已完成题目汇总；已中断/已完成则直接汇总。"""
    from evals.finalize import NoEvalSamples, finalize_task_results

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] == "failed":
        raise HTTPException(status_code=400, detail="失败任务无法产出结果")
    if task["status"] in ("pending", "running"):
        _interrupt_evaluation(store, task)
        partial = True
    elif task["status"] == "cancelled":
        partial = True
    else:
        partial = bool((task.get("metric_summary") or {}).get("partial"))
    try:
        updated = finalize_task_results(task_id, partial=partial or None)
    except NoEvalSamples as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"success": True, "data": updated}


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


class RagasScoreRequest(BaseModel):
    ragas_model_id: str = Field(min_length=1, description="RAGAS 评分用对话模型 ID")


@evaluation_router.post("/{task_id}/ragas")
def start_ragas_score(task_id: str, req: RagasScoreRequest) -> dict[str, Any]:
    """对已收集的 RAG 轨迹做离线 RAGAS 打分。"""
    from api.tasks import run_ragas_score_task
    from evals.runners.ragas_runner import samples_to_ragas_rows

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["suite"] != "rag_quality":
        raise HTTPException(status_code=400, detail="只有 rag_quality 任务可以离线 RAGAS 打分")
    if task["status"] == "running":
        raise HTTPException(status_code=400, detail="任务正在运行，请等待收集完成后再打分")
    if task["status"] not in ("success", "cancelled"):
        raise HTTPException(status_code=400, detail=f"当前状态为 {task['status']}，无法打分")
    rows = samples_to_ragas_rows(store.list_all_samples(task_id))
    if not rows:
        raise HTTPException(status_code=400, detail="没有可打分的题目：需要至少一题 Agent 成功作答")
    try:
        async_result = run_ragas_score_task.delay(task_id, req.ragas_model_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "RAGAS 打分入队失败，请确认 Redis 与 Celery worker 已启动。"
                f" 原因: {exc}"
            ),
        ) from exc
    prev = dict(task.get("metric_summary") or {})
    snap = dict(task.get("config_snapshot") or {})
    snap["ragas_model_id"] = req.ragas_model_id
    store.update_task(
        task_id,
        status="running",
        celery_task_id=async_result.id,
        config_snapshot=snap,
        metric_summary={
            **prev,
            "phase": "ragas",
            "ragas_finished": 0,
            "ragas_total": len(rows),
            "ragas_error": None,
            "ready_for_ragas": True,
        },
        started=True,
        only_if_active=False,
        clear_err_msg=True,
    )
    task = store.get_task(task_id)
    return {"success": True, "data": task}


class RetryFailedRequest(BaseModel):
    qids: list[int] | None = None


@evaluation_router.post("/{task_id}/retry")
def retry_failed_evaluation(task_id: str, req: RetryFailedRequest = RetryFailedRequest()) -> dict[str, Any]:
    """重跑失败题（运行出错，或 rag_quality 空答），写回原任务。"""
    from api.tasks import run_eval_retry_task
    from evals.failed import retryable_qids

    store = EvalStore()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] == "running":
        raise HTTPException(status_code=400, detail="任务正在运行，请等待完成后再重试失败题")
    if task["status"] not in ("success", "cancelled", "failed"):
        raise HTTPException(status_code=400, detail=f"当前状态为 {task['status']}，无法重试")
    samples = store.list_all_samples(task_id)
    failed = retryable_qids(samples, suite=task["suite"])
    if req.qids:
        want = {int(q) for q in req.qids}
        target = [q for q in failed if q in want]
        missing = [q for q in req.qids if int(q) not in set(failed)]
        if missing and not target:
            raise HTTPException(status_code=400, detail="指定题目不是失败题，无需重试")
    else:
        target = failed
    if not target:
        raise HTTPException(status_code=400, detail="没有失败题可重试")
    try:
        async_result = run_eval_retry_task.delay(task_id, target)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "失败题重试入队失败，请确认 Redis 与 Celery worker 已启动。"
                f" 原因: {exc}"
            ),
        ) from exc
    prev = dict(task.get("metric_summary") or {})
    store.update_task(
        task_id,
        status="running",
        celery_task_id=async_result.id,
        metric_summary={
            **prev,
            "phase": "agent" if task["suite"] == "rag_quality" else "eval",
            "retry_finished": 0,
            "retry_total": len(target),
        },
        started=True,
        only_if_active=False,
        clear_err_msg=True,
    )
    updated = store.get_task(task_id)
    return {"success": True, "data": updated}


@evaluation_router.delete("/{task_id}")
def remove_evaluation(task_id: str) -> dict[str, Any]:
    store = EvalStore()
    if not store.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    store.delete_task(task_id)
    return {"success": True, "data": {"id": task_id}}
