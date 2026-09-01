"""模型管理 API（/models 端点）。

- POST/GET /models          创建 / 列表
- GET/PUT/DELETE /models/{id}  详情 / 更新 / 删除（内置、默认、被 KB 引用者不可删）
- POST /models/{id}/debug   测试连接（chat/embedding/rerank 实际调用；ASR 仅校验配置）
- PUT /models/{id}/credentials           更新凭证（api_key 等）
- DELETE /models/{id}/credentials/{field} 清空凭证字段
- GET /models/providers     远程厂商目录（source=remote 时选用）
- GET /models/ollama/status|models  本地 Ollama 探测

安全约定：读取接口永不返回凭证明文，仅返回 credentials: {field: bool}。
DB 语义对齐 WeKnora：source = local|remote，厂商在 parameters.provider。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from models import create_chat_model, create_embeddings, create_reranker, create_vlm_model
from models.ollama import fetch_ollama_status, list_ollama_models
from models.openai_compat import ollama_openai_base_url
from models.providers import (
    MODEL_SOURCES,
    MODEL_TYPES,
    default_base_url,
    get_provider,
    list_remote_providers,
    normalize_provider,
    runtime_provider,
    spec_to_public,
)
from utils.message_content import message_text
from utils.model_store import (
    _SECRET_FIELDS,
    ModelStore,
)

router = APIRouter(tags=["models"])

_store = ModelStore()

def _to_public(rec: dict) -> dict:
    """对外输出：剥离凭证明文，替换为 credentials 标记。"""
    params = dict(rec.get("parameters") or {})
    credentials = {f: bool(params.get(f)) for f in _SECRET_FIELDS}
    public_params = {k: v for k, v in params.items() if k not in _SECRET_FIELDS}
    provider_id = runtime_provider(rec["source"], params)
    spec = get_provider(provider_id)
    return {
        "id": rec["id"],
        "name": rec["name"],
        "display_name": rec["display_name"],
        "type": rec["type"],
        "source": rec["source"],
        "provider": provider_id,
        "provider_name": spec.name if spec else provider_id,
        "description": rec["description"],
        "parameters": public_params,
        "is_default": rec["is_default"],
        "is_builtin": rec["is_builtin"],
        "status": rec["status"],
        "credentials": credentials,
        "created_at": rec["created_at"],
        "updated_at": rec["updated_at"],
    }

def _params_from_request(body: ModelCreateRequest) -> dict[str, Any]:
    provider_id = (
        "ollama"
        if body.source == "local"
        else normalize_provider(body.provider or "siliconflow")
    )
    params: dict[str, Any] = {"model": body.model or body.name, "provider": provider_id}
    base_url = (body.base_url or "").strip() or None
    if not base_url:
        if body.source == "local":
            base_url = ollama_openai_base_url()
        else:
            base_url = default_base_url(provider_id, body.type) or None
    if base_url:
        params["base_url"] = base_url
    if body.api_key:
        params["api_key"] = body.api_key
    if body.dimensions is not None:
        params["dimensions"] = body.dimensions
    if body.temperature is not None:
        params["temperature"] = body.temperature
    if body.type == "VLLM":
        params["supports_vision"] = True
    if body.supports_vision is not None:
        params["supports_vision"] = body.supports_vision
    if body.extra_parameters:
        params.update({k: v for k, v in body.extra_parameters.items() if v is not None})
    return params

# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class ModelCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="模型名（如 Qwen/Qwen3.5-35B-A3B）")
    display_name: str | None = Field(default=None, max_length=200)
    type: str = Field(description=f"模型类型: {', '.join(MODEL_TYPES)}")
    source: str = Field(default="remote", description=f"部署位置: {', '.join(MODEL_SOURCES)}")
    provider: str | None = Field(
        default="siliconflow",
        description="厂商标识（remote 时必填；local 固定 ollama）",
    )
    description: str = Field(default="", max_length=500)
    # 连接参数（扁平化，便于前端表单）
    model: str | None = Field(default=None, max_length=200, description="实际调用模型名，缺省用 name")
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    dimensions: int | None = Field(default=None, ge=1, le=10000, description="仅 Embedding：输出维度（缺省创建时实测）")
    temperature: float | None = Field(default=None, ge=0, le=2, description="仅 KnowledgeQA/VLLM")
    supports_vision: bool | None = Field(
        default=None,
        description="KnowledgeQA 是否支持视觉；VLLM 创建时默认 true",
    )
    is_default: bool = False
    extra_parameters: dict[str, Any] | None = None

class ModelUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None
    dimensions: int | None = None
    temperature: float | None = None
    supports_vision: bool | None = None
    is_default: bool | None = None
    status: str | None = Field(default=None, pattern="^(active|disabled)$")

class CredentialsUpdateRequest(BaseModel):
    api_key: str | None = None

class ModelDebugRequest(BaseModel):
    prompt: str = Field(default="请简要描述这张图片的内容。", max_length=2000)
    image_base64: str | None = Field(
        default=None,
        description="VLLM 调试可选测试图（data URI 或纯 base64）",
    )

_DEBUG_TINY_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

def _normalize_debug_image(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text:
        return f"data:image/png;base64,{_DEBUG_TINY_PNG}"
    if text.startswith("data:"):
        return text
    return f"data:image/png;base64,{text}"

# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.get("/models/providers")
def list_providers(type: str | None = Query(default=None, description="按模型类型过滤")) -> list[dict]:
    if type and type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的模型类型: {type}")
    return [spec_to_public(p) for p in list_remote_providers(type)]


@router.get("/models/ollama/status")
def ollama_status() -> dict:
    return fetch_ollama_status()


@router.get("/models/ollama/models")
def ollama_models() -> dict:
    return list_ollama_models()


@router.post("/models")
def create_model(body: ModelCreateRequest) -> dict:
    if body.type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的模型类型: {body.type}，可选: {', '.join(MODEL_TYPES)}")
    if body.source not in MODEL_SOURCES:
        legacy = normalize_provider(body.source)
        if get_provider(legacy):
            body.provider = legacy
            body.source = "local" if legacy == "ollama" else "remote"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的来源: {body.source}，可选: {', '.join(MODEL_SOURCES)}",
            )
    if body.source == "local":
        body.provider = "ollama"
    elif not (body.provider or "").strip():
        raise HTTPException(status_code=400, detail="远程模型必须指定 provider")
    try:
        rec = _store.create_model(
            name=body.name.strip(),
            type_=body.type,
            source=body.source,
            display_name=body.display_name or body.name,
            description=body.description,
            parameters=_params_from_request(body),
            is_default=body.is_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_public(rec)

@router.get("/models")
def list_models(
    type: str | None = None,
    source: str | None = None,
    provider: str | None = None,
) -> list[dict]:
    if type and type not in MODEL_TYPES:
        raise HTTPException(status_code=400, detail=f"不支持的模型类型: {type}")
    if source and source not in MODEL_SOURCES:
        raise HTTPException(status_code=400, detail=f"不支持的来源: {source}")
    recs = _store.list_models(type_=type, source=source, provider=provider)
    return [_to_public(r) for r in recs]

@router.get("/models/{model_id}")
def get_model(model_id: str) -> dict:
    rec = _store.get_model(model_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    return _to_public(rec)

@router.put("/models/{model_id}")
def update_model(model_id: str, body: ModelUpdateRequest) -> dict:
    store = _store
    rec = store.get_model(model_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    parameters: dict[str, Any] | None = None
    touched = any(
        v is not None
        for v in (
            body.model,
            body.base_url,
            body.api_key,
            body.dimensions,
            body.temperature,
            body.supports_vision,
            body.provider,
        )
    )
    if touched:
        params = dict(rec["parameters"])
        if body.model is not None:
            params["model"] = body.model
        if body.base_url is not None:
            params["base_url"] = body.base_url or None
        # PUT /models/:id 不更新 api_key，走 /credentials 子资源
        if body.api_key is not None:
            stored = rec["parameters"].get("api_key")
            if stored:
                params["api_key"] = stored
        if body.provider is not None and rec["source"] == "remote":
            params["provider"] = normalize_provider(body.provider)
        if body.dimensions is not None:
            params["dimensions"] = body.dimensions
        if body.temperature is not None:
            params["temperature"] = body.temperature
        if body.supports_vision is not None:
            params["supports_vision"] = body.supports_vision
        if rec["type"] == "VLLM":
            params["supports_vision"] = True
        parameters = params
    try:
        updated = store.update_model(
            model_id,
            display_name=body.display_name,
            description=body.description,
            parameters=parameters,
            is_default=body.is_default,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_public(updated)

@router.delete("/models/{model_id}")
def delete_model(model_id: str) -> dict:
    try:
        _store.delete_model(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}

@router.put("/models/{model_id}/credentials")
def update_credentials(model_id: str, body: CredentialsUpdateRequest) -> dict:
    if body.api_key is None:
        raise HTTPException(status_code=400, detail="请求体需包含 api_key（空字符串表示清空）")
    try:
        rec = _store.update_credentials(model_id, {"api_key": body.api_key})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_public(rec)

@router.delete("/models/{model_id}/credentials/{field}")
def clear_credential_field(model_id: str, field: str) -> dict:
    try:
        rec = _store.clear_credential_field(model_id, field)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_public(rec)

@router.post("/models/{model_id}/debug")
def debug_model(model_id: str, body: ModelDebugRequest | None = None) -> dict:
    """测试连接。VLLM 使用多模态请求；ASR 仅校验配置完整性。"""
    req = body or ModelDebugRequest
    rec = _store.get_model(model_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    if rec["status"] == "disabled":
        raise HTTPException(status_code=400, detail="模型已禁用，无法测试")
    params = rec.get("parameters") or {}
    mtype = rec["type"]
    model_name = params.get("model")

    if not model_name:
        raise HTTPException(status_code=400, detail="模型名为空，请先补全连接参数")

    start = time.time()
    try:
        if mtype == "KnowledgeQA":
            chat = create_chat_model(model=model_id)
            resp = chat.invoke(req.prompt or "ping")
            message = f"连接成功，模型回复: {(resp.content or '')[:120]}"
        elif mtype == "VLLM":
            llm = create_vlm_model(model=model_id)
            image_uri = _normalize_debug_image(req.image_base64)
            prompt = (req.prompt or "请简要描述这张图片的内容。").strip()
            resp = llm.invoke(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_uri}},
                        ],
                    }
                ]
            )
            snippet = message_text(getattr(resp, "content", ""))[:200]
            message = f"视觉模型连接成功，回复: {snippet or '（空回复）'}"
        elif mtype == "Embedding":
            embeddings = create_embeddings(model=model_id)
            dim = len(embeddings.embed_query("ping"))
            message = f"连接成功，输出维度 {dim}"
        elif mtype == "Rerank":
            reranker = create_reranker(model=model_id)
            result = reranker.rerank("ping", ["a", "b"], top_n=1)
            message = f"连接成功，返回 {len(result)} 条重排结果"
        elif mtype == "ASR":
            missing = [f for f in ("base_url",) if not params.get(f)]
            if missing:
                raise ValueError(f"缺少必填参数: {', '.join(missing)}")
            message = "配置校验通过（ASR 不做网络测试，请以实际识别为准）"
        else:
            raise HTTPException(status_code=400, detail=f"不支持的模型类型: {mtype}")
    except HTTPException:
        raise
    except Exception as exc:
        latency = round((time.time() - start) * 1000)
        raise HTTPException(status_code=400, detail=f"连接失败: {exc}") from exc
    latency = round((time.time() - start) * 1000)
    return {"ok": True, "message": message, "latency_ms": latency}
