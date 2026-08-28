"""模型凭证与就绪检查（models 表为主，.env 仅作 siliconflow 兜底）。"""

from __future__ import annotations

from typing import Any

from config.settings import settings
from utils.model_store import ModelStore, is_model_ref

PLACEHOLDER_KEYS = frozenset({"", "sk-xxx", "lsv2-xxx", "EMPTY"})

def _env_api_key_ok() -> bool:
    key = (settings.siliconflow_api_key or "").strip()
    return bool(key) and key not in PLACEHOLDER_KEYS

def _key_from_record(rec: dict[str, Any] | None) -> str | None:
    if not rec:
        return settings.siliconflow_api_key.strip() if _env_api_key_ok else None
    params = rec.get("parameters") or {}
    key = params.get("api_key")
    if isinstance(key, str) and key.strip() and key.strip() not in PLACEHOLDER_KEYS:
        return key.strip()
    if rec.get("source") in ("siliconflow", "openai_compatible") and _env_api_key_ok:
        return settings.siliconflow_api_key.strip()
    return None

def get_active_model(ref: str | None, model_type: str) -> dict[str, Any] | None:
    """按 ID 或类型默认取 active 模型记录。"""
    store = ModelStore()
    if ref and is_model_ref(ref):
        rec = store.get_model(ref)
        if rec is None or rec.get("status") in ("deleted", "disabled"):
            return None
        if rec["type"] != model_type:
            return None
        return rec
    return store.get_default_model(model_type)

def model_has_usable_key(ref: str | None, model_type: str) -> bool:
    rec = get_active_model(ref, model_type)
    if rec is not None:
        return _key_from_record(rec) is not None
    if ref and not is_model_ref(ref):
        return _env_api_key_ok
    return _env_api_key_ok

def validate_model_for_use(
    ref: str | None,
    model_type: str,
    *,
    label: str,
    require_key: bool = True,
) -> dict[str, Any] | None:
    """校验模型存在、类型匹配、未禁用；require_key 时要求可用 API Key。"""
    if ref and not is_model_ref(ref):
        if require_key and not _env_api_key_ok:
            raise ValueError(
                f"{label} 使用裸模型名「{ref}」且未在模型管理中配置 API Key，"
                "请在「模型管理」中选择模型 ID 并配置密钥"
            )
        return None
    rec = get_active_model(ref, model_type)
    if rec is None:
        if ref:
            raise ValueError(f"{label} 模型不存在、已禁用或类型不匹配: {ref}")
        if require_key and not _env_api_key_ok:
            raise ValueError(
                f"未配置可用的默认{label}模型：请在「模型管理」中创建并设为默认，或配置 API Key"
            )
        return None
    if require_key and _key_from_record(rec) is None:
        name = rec.get("display_name") or rec.get("name") or ref
        raise ValueError(
            f"「{name}」未配置 API Key：请在「模型管理」中编辑该模型并保存密钥"
        )
    return rec

def ensure_embedding_model_ready(embedding_model_id: str) -> None:
    validate_model_for_use(embedding_model_id, "Embedding", label="向量化", require_key=True)

def ensure_knowledgeqa_model_ready(model_id: str | None, label: str = "问答") -> None:
    validate_model_for_use(model_id, "KnowledgeQA", label=label, require_key=True)
