"""音频格式判定与 ASR 闸门（对齐 WeKnora：KB + 对话可开关）。"""

from __future__ import annotations

from pathlib import Path

from config.settings import settings

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"})

NO_ASR_AUDIO_UPLOAD_DETAIL = "未配置语音识别（ASR）模型，无法上传音频。请先在模型管理中添加。"
KB_ASR_REQUIRED_DETAIL = "上传音频文件需要设置ASR语音识别模型"
AGENT_AUDIO_DISABLED_DETAIL = "当前智能体未开启音频上传"
CHAT_AUDIO_DISABLED_DETAIL = "音频上传未启用"


def is_audio_filename(file_name: str) -> bool:
    return Path(file_name or "").suffix.lower() in AUDIO_EXTENSIONS


def is_audio_upload(file_name: str, mime_type: str = "") -> bool:
    if is_audio_filename(file_name):
        return True
    return (mime_type or "").lower().startswith("audio/")


def ensure_kb_can_accept_audio(kb: dict) -> None:
    """知识库上传音频：必须开启 ASR 并绑定可用模型。"""
    if not kb.get("asr_enabled"):
        raise ValueError(KB_ASR_REQUIRED_DETAIL)
    mid = (kb.get("asr_model_id") or "").strip()
    if not mid:
        raise ValueError(KB_ASR_REQUIRED_DETAIL)
    from utils.model_store import ModelStore

    if not ModelStore().is_asr_model_id_valid(mid):
        raise ValueError("知识库 ASR 模型不存在或已禁用")


def ensure_chat_can_accept_audio(session_id: str) -> None:
    """对话上传音频：全局开关 + 当前智能体开关 + 可用 ASR。"""
    if not settings.chat_audio_enabled:
        raise ValueError(CHAT_AUDIO_DISABLED_DETAIL)

    import psycopg
    from stores.agent_repository import AgentStore
    from utils.model_store import ModelStore

    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT agent_id FROM ks_threads WHERE thread_id = %s::uuid",
            (session_id,),
        ).fetchone()
    agent_id = (row[0] if row else None) or ""
    if agent_id:
        rec = AgentStore().get_agent(str(agent_id))
        if rec is not None and not rec.get("audio_upload_enabled"):
            raise ValueError(AGENT_AUDIO_DISABLED_DETAIL)
    if not ModelStore().has_usable_asr():
        raise ValueError(NO_ASR_AUDIO_UPLOAD_DETAIL)


def resolve_session_asr_model_id(session_id: str) -> str | None:
    """解析本会话转写用的 ASR 模型：智能体绑定 > 全局默认 > 目录中第一个可用。"""
    import psycopg
    from stores.agent_repository import AgentStore
    from utils.model_store import ModelStore

    with psycopg.connect(settings.postgres_dsn) as conn:
        row = conn.execute(
            "SELECT agent_id FROM ks_threads WHERE thread_id = %s::uuid",
            (session_id,),
        ).fetchone()
    agent_id = (row[0] if row else None) or ""
    explicit = ""
    if agent_id:
        rec = AgentStore().get_agent(str(agent_id))
        if rec:
            explicit = rec.get("asr_model_id") or ""
    return ModelStore().resolve_asr_model_id(explicit)
