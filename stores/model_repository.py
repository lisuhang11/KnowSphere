"""models 表持久化。"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config.settings import settings
from models.providers import (
    MODEL_SOURCES,
    MODEL_TYPES,
    get_provider,
    normalize_provider,
    provider_supports_type,
    runtime_provider,
)
from stores.common import load_jsonb
from utils.crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

# parameters 中需要加密存储的字段
SECRET_FIELDS = ("api_key",)

_MODEL_ID_PREFIX = "model-"

def new_model_id() -> str:
    return f"{_MODEL_ID_PREFIX}{uuid.uuid4().hex}"

def is_model_ref(ref: str) -> bool:
    """判断引用是否为 models 表 ID（而非裸模型名）。"""
    return isinstance(ref, str) and ref.startswith(_MODEL_ID_PREFIX)

class ModelStore():
    def __init__(self) -> None:
        self.dsn = settings.postgres_dsn

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as conn:
            yield conn

    # ---------------------------------------------------------------- schema

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS models (
                    id          VARCHAR(64) PRIMARY KEY,
                    name        TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    type        TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    parameters  JSONB NOT NULL DEFAULT '{}'::jsonb,
                    is_default  BOOLEAN NOT NULL DEFAULT FALSE,
                    is_builtin  BOOLEAN NOT NULL DEFAULT FALSE,
                    status      TEXT NOT NULL DEFAULT 'active',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_models_type ON models(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_models_default ON models(type) WHERE is_default")
            self._migrate_source_to_local_remote(conn)
            conn.execute("DROP INDEX IF EXISTS idx_models_name_source_type")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_models_name_type_source_provider
                ON models (name, type, source, COALESCE(parameters->>'provider', ''))
                """
            )

    @staticmethod
    def _migrate_source_to_local_remote(conn) -> None:
        """旧版 source=厂商名 → source=local|remote + parameters.provider。"""
        conn.execute(
            """
            UPDATE models
            SET parameters = COALESCE(parameters, '{}'::jsonb) || jsonb_build_object(
                'provider',
                CASE source
                    WHEN 'openai_compatible' THEN 'generic'
                    WHEN 'ollama' THEN 'ollama'
                    ELSE source
                END
            )
            WHERE source NOT IN ('local', 'remote')
              AND COALESCE(parameters->>'provider', '') = ''
            """
        )
        conn.execute(
            """
            UPDATE models
            SET source = CASE WHEN source = 'ollama' THEN 'local' ELSE 'remote' END
            WHERE source NOT IN ('local', 'remote')
            """
        )
        conn.execute(
            """
            UPDATE models
            SET parameters = jsonb_set(parameters, '{provider}', '"generic"')
            WHERE parameters->>'provider' = 'openai_compatible'
            """
        )
        conn.execute(
            """
            UPDATE models
            SET parameters = COALESCE(parameters, '{}'::jsonb) || '{"provider": "ollama"}'::jsonb
            WHERE source = 'local' AND COALESCE(parameters->>'provider', '') = ''
            """
        )
        conn.execute(
            """
            UPDATE models
            SET parameters = COALESCE(parameters, '{}'::jsonb) || '{"provider": "generic"}'::jsonb
            WHERE source = 'remote' AND COALESCE(parameters->>'provider', '') = ''
            """
        )

    # ---------------------------------------------------------------- seed

    def seed_builtin_models(self) -> None:
        """把 .env 的 chat/embedding/rerank 模型注册为内置记录（幂等），
        并把存量 knowledge_bases.embedding_model_id 的模型名迁移为模型 ID。"""
        entries: list[tuple[str, str, str, str]] = []
        if settings.chat_model:
            entries.append(
                (settings.chat_model, "KnowledgeQA", settings.siliconflow_base_url, settings.siliconflow_api_key)
            )
        if settings.embedding_model:
            entries.append(
                (settings.embedding_model, "Embedding", settings.siliconflow_base_url, settings.siliconflow_api_key)
            )
        if settings.rerank_enabled and settings.rerank_model:
            entries.append(
                (settings.rerank_model, "Rerank", settings.siliconflow_base_url, settings.siliconflow_api_key)
            )
        if settings.vlm_model:
            entries.append(
                (settings.vlm_model, "VLLM", settings.siliconflow_base_url, settings.siliconflow_api_key)
            )
        if settings.asr_model:
            entries.append(
                (settings.asr_model, "ASR", settings.siliconflow_base_url, settings.siliconflow_api_key)
            )

        for name, mtype, base_url, api_key in entries:
            existing = self.get_model_by_name_type(name, mtype)
            if existing:
                # 内置模型凭证随 .env 同步：避免密钥轮换后 DB 仍用旧密文导致 401
                if existing.get("is_builtin"):
                    sync_params: dict[str, Any] = {"base_url": base_url, "provider": "siliconflow"}
                    if api_key:
                        self.update_credentials(existing["id"], {"api_key": api_key})
                    self.update_model(existing["id"], parameters=sync_params)
                continue
            params: dict[str, Any] = {
                "model": name,
                "base_url": base_url,
                "provider": "siliconflow",
            }
            if mtype == "VLLM":
                params["supports_vision"] = True
            if api_key:
                params["api_key"] = encrypt_secret(api_key)
            self.create_model(
                name=name,
                type_=mtype,
                source="remote",
                display_name=name,
                description="内置模型（来自 .env 配置）",
                parameters=params,
                is_builtin=True,
                is_default=False,
            )

        # .env 是事实默认：某 type 尚无默认时，把 .env 对应模型设为默认
        for name, mtype, *_ in entries:
            rec = self.get_model_by_name_type(name, mtype)
            if rec and not self.get_default_model(mtype):
                self.set_default(rec["id"])

        # 迁移存量 KB：embedding_model_id 若为裸模型名（旧版存 name），更新为内置模型 ID
        embed = self.get_model_by_name_type(settings.embedding_model, "Embedding") if settings.embedding_model else None
        if embed:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE knowledge_bases SET embedding_model_id=%s WHERE embedding_model_id=%s",
                    (embed["id"], settings.embedding_model),
                )
                if cur.rowcount:
                    logger.info("迁移 %s 个知识库的 embedding_model_id 到模型 ID", cur.rowcount)

    # ---------------------------------------------------------------- helpers

    def _row_to_model(self, row: dict) -> dict:
        params = dict(load_jsonb(row["parameters"] or {}) or {})
        for f in SECRET_FIELDS:
            if params.get(f):
                params[f] = decrypt_secret(str(params[f]))
        row = dict(row)
        row["parameters"] = params
        return row

    # ---------------------------------------------------------------- queries

    def list_models(
        self,
        type_: Optional[str] = None,
        source: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM models WHERE status != 'deleted'"
        args: list[Any] = []
        if type_:
            sql += " AND type = %s"
            args.append(type_)
        if source:
            sql += " AND source = %s"
            args.append(source)
        if provider:
            sql += " AND COALESCE(parameters->>'provider', '') = %s"
            args.append(normalize_provider(provider))
        sql += " ORDER BY type, is_default DESC, created_at"
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row_to_model(r) for r in rows]

    def get_model(self, model_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM models WHERE id = %s AND status != 'deleted'", (model_id,)
            ).fetchone()
        return self._row_to_model(row) if row else None

    def get_model_by_name_type(
        self,
        name: str,
        type_: str,
        source: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[dict]:
        sql = "SELECT * FROM models WHERE name = %s AND type = %s AND status != 'deleted'"
        args: list[Any] = [name, type_]
        if source:
            sql += " AND source = %s"
            args.append(source)
        if provider:
            sql += " AND COALESCE(parameters->>'provider', '') = %s"
            args.append(normalize_provider(provider))
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        return self._row_to_model(row) if row else None

    def get_default_model(self, type_: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM models WHERE type = %s AND is_default AND status != 'deleted' "
                "ORDER BY created_at LIMIT 1",
                (type_,),
            ).fetchone()
        return self._row_to_model(row) if row else None

    # ---------------------------------------------------------------- writes

    def create_model(
        self,
        name: str,
        type_: str,
        source: str,
        display_name: str = "",
        description: str = "",
        parameters: Optional[dict] = None,
        is_default: bool = False,
        is_builtin: bool = False,
    ) -> dict:
        if type_ not in MODEL_TYPES:
            raise ValueError(f"不支持的模型类型: {type_}")
        if source not in MODEL_SOURCES:
            raise ValueError(f"不支持的来源: {source}，可选: {', '.join(MODEL_SOURCES)}")
        if not name or not name.strip():
            raise ValueError("模型名不能为空")

        params = {k: v for k, v in (parameters or {}).items() if v is not None}
        provider_id = runtime_provider(source, params)
        spec = get_provider(provider_id)
        if spec is None:
            raise ValueError(f"不支持的 provider: {provider_id}")
        if source == "local":
            if type_ == "Rerank":
                raise ValueError("本地 Ollama 不支持 Rerank 模型")
            provider_id = "ollama"
        elif not provider_supports_type(provider_id, type_):
            raise ValueError(f"provider '{provider_id}' 不支持类型 {type_}")
        params["provider"] = provider_id
        if self.get_model_by_name_type(name, type_, source=source, provider=provider_id):
            raise ValueError(f"已存在同类型同名模型: {name} ({type_})")

        model_id = new_model_id()
        for f in SECRET_FIELDS:
            if params.get(f):
                params[f] = encrypt_secret(str(params[f]))
        try:
            with self._conn() as conn:
                with conn.transaction():
                    if is_default:
                        conn.execute("UPDATE models SET is_default = FALSE WHERE type = %s", (type_,))
                    conn.execute(
                        """
                        INSERT INTO models (id, name, display_name, type, source, description, parameters, is_default, is_builtin)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (model_id, name.strip(), display_name or name.strip(), type_, source, description, Jsonb(params), is_default, is_builtin),
                    )
        except UniqueViolation as exc:
            raise ValueError(f"已存在同类型同名模型: {name} ({type_})") from exc
        rec = self.get_model(model_id)
        if rec is None:
            raise ValueError("模型写入失败，请重试")
        return rec

    def update_model(
        self,
        model_id: str,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[dict] = None,
        is_default: Optional[bool] = None,
        status: Optional[str] = None,
    ) -> dict:
        rec = self.get_model(model_id)
        if rec is None:
            raise ValueError(f"模型不存在: {model_id}")
        sets: list[str] = []
        args: list[Any] = []
        if display_name is not None:
            sets.append("display_name = %s")
            args.append(display_name)
        if description is not None:
            sets.append("description = %s")
            args.append(description)
        if status is not None:
            sets.append("status = %s")
            args.append(status)
        if parameters is not None:
            merged = dict(rec["parameters"])
            for k, v in parameters.items():
                if v is None:
                    continue
                if k in SECRET_FIELDS and v:
                    merged[k] = encrypt_secret(str(v))
                else:
                    merged[k] = v
            sets.append("parameters = %s")
            args.append(merged)
        if is_default is not None:
            sets.append("is_default = %s")
            args.append(bool(is_default))
        if not sets:
            return rec
        sets.append("updated_at = now()")
        args.append(model_id)
        with self._conn() as conn:
            with conn.transaction():
                if is_default:
                    conn.execute("UPDATE models SET is_default = FALSE WHERE type = %s", (rec["type"],))
                args_with_jsonb = [Jsonb(v) if isinstance(v, dict) else v for v in args]
                conn.execute(f"UPDATE models SET {', '.join(sets)} WHERE id = %s", args_with_jsonb)
        updated = self.get_model(model_id)
        if updated is None:
            raise ValueError(f"模型不存在: {model_id}")
        return updated

    def set_default(self, model_id: str) -> dict:
        return self.update_model(model_id, is_default=True)

    def update_credentials(self, model_id: str, credentials: dict) -> dict:
        """更新凭证字段（api_key 等）；空字符串清空，None 表示保留。"""
        rec = self.get_model(model_id)
        if rec is None:
            raise ValueError(f"模型不存在: {model_id}")
        merged = dict(rec["parameters"])
        for k, v in credentials.items():
            if k not in SECRET_FIELDS or v is None:
                continue
            if v == "":
                merged.pop(k, None)
            else:
                merged[k] = encrypt_secret(str(v))
        with self._conn() as conn:
            conn.execute(
                "UPDATE models SET parameters = %s, updated_at = now() WHERE id = %s",
                (Jsonb(merged), model_id),
            )
        return self.get_model(model_id)  # type: ignore[return-value]

    def clear_credential_field(self, model_id: str, field: str) -> dict:
        if field not in SECRET_FIELDS:
            raise ValueError(f"字段不是凭证字段: {field}")
        rec = self.get_model(model_id)
        if rec is None:
            raise ValueError(f"模型不存在: {model_id}")
        merged = dict(rec["parameters"])
        merged.pop(field, None)
        with self._conn() as conn:
            conn.execute(
                "UPDATE models SET parameters = %s, updated_at = now() WHERE id = %s",
                (Jsonb(merged), model_id),
            )
        return self.get_model(model_id)  # type: ignore[return-value]

    def delete_model(self, model_id: str) -> None:
        """软删除。内置模型、默认模型、被知识库引用的模型不可删除。"""
        rec = self.get_model(model_id)
        if rec is None:
            raise ValueError(f"模型不存在: {model_id}")
        if rec["is_builtin"]:
            raise ValueError("内置模型不可删除")
        if rec["is_default"]:
            raise ValueError("默认模型不可删除，请先设置其他模型为默认")
        refs = self.count_model_references(model_id)
        total = refs["embedding"] + refs["summary"] + refs.get("asr_kb", 0) + refs.get("asr_agent", 0)
        if total:
            parts = []
            if refs["embedding"]:
                parts.append(f"{refs['embedding']} 个知识库 embedding")
            if refs["summary"]:
                parts.append(f"{refs['summary']} 个知识库摘要/对话")
            if refs.get("asr_kb"):
                parts.append(f"{refs['asr_kb']} 个知识库 ASR")
            if refs.get("asr_agent"):
                parts.append(f"{refs['asr_agent']} 个智能体 ASR")
            raise ValueError(f"模型正被引用（{', '.join(parts)}），不可删除")
        with self._conn() as conn:
            conn.execute(
                "UPDATE models SET status = 'deleted', updated_at = now() WHERE id = %s",
                (model_id,),
            )

    # ---------------------------------------------------------------- usage

    def is_embedding_model_id_valid(self, model_id: str) -> bool:
        """知识库创建时校验 embedding 模型引用：存在、类型正确、未禁用。"""
        rec = self.get_model(model_id)
        return (
            rec is not None
            and rec["type"] == "Embedding"
            and rec.get("status") not in ("deleted", "disabled")
        )

    def is_knowledgeqa_model_id_valid(self, model_id: str) -> bool:
        rec = self.get_model(model_id)
        return (
            rec is not None
            and rec["type"] == "KnowledgeQA"
            and rec.get("status") not in ("deleted", "disabled")
        )

    def is_vllm_model_id_valid(self, model_id: str) -> bool:
        rec = self.get_model(model_id)
        return (
            rec is not None
            and rec["type"] == "VLLM"
            and rec.get("status") not in ("deleted", "disabled")
        )

    def is_asr_model_id_valid(self, model_id: str) -> bool:
        rec = self.get_model(model_id)
        return (
            rec is not None
            and rec["type"] == "ASR"
            and rec.get("status") not in ("deleted", "disabled")
        )

    def resolve_asr_model_id(self, explicit: str | None = None) -> str | None:
        """智能体/知识库指定 > 全局 chat_asr_model_id > 默认 ASR > 目录中第一个可用。"""
        mid = (explicit or "").strip()
        if mid and self.is_asr_model_id_valid(mid):
            return mid
        sid = (settings.chat_asr_model_id or "").strip()
        if sid and self.is_asr_model_id_valid(sid):
            return sid
        rec = self.get_default_model("ASR")
        if rec and rec.get("status") not in ("deleted", "disabled"):
            return rec["id"]
        for m in self.list_models(type_="ASR"):
            if m.get("status") not in ("deleted", "disabled"):
                return m["id"]
        return None

    def has_usable_asr(self) -> bool:
        """是否存在可用于音频转写的 ASR。"""
        return self.resolve_asr_model_id() is not None

    def has_usable_vlm(self) -> bool:
        """是否存在可用于聊天图片理解的 VLLM（对齐 WeKnora：无 VLM 不允许传图）。"""
        sid = (settings.chat_vlm_model_id or "").strip()
        if sid and self.is_vllm_model_id_valid(sid):
            return True
        rec = self.get_default_model("VLLM")
        if rec and rec.get("status") not in ("deleted", "disabled"):
            return True
        for m in self.list_models(type_="VLLM"):
            if m.get("status") not in ("deleted", "disabled"):
                return True
        return False

    def count_model_references(self, model_id: str) -> dict[str, int]:
        with self._conn() as conn:
            emb = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_bases WHERE embedding_model_id = %s",
                (model_id,),
            ).fetchone()
            summary = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_bases WHERE summary_model_id = %s",
                (model_id,),
            ).fetchone()
            asr_kb = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_bases WHERE asr_model_id = %s",
                (model_id,),
            ).fetchone()
            asr_agent = conn.execute(
                "SELECT COUNT(*) AS c FROM agents WHERE asr_model_id = %s",
                (model_id,),
            ).fetchone()
        return {
            "embedding": int(emb["c"] or 0) if emb else 0,
            "summary": int(summary["c"] or 0) if summary else 0,
            "asr_kb": int(asr_kb["c"] or 0) if asr_kb else 0,
            "asr_agent": int(asr_agent["c"] or 0) if asr_agent else 0,
        }

    def resolve_embedding_model_name(self, model_id: str) -> Optional[str]:
        """models 表 ID -> 实际模型名（供维度解析等场景）；查不到返回 None。"""
        rec = self.get_model(model_id)
        if rec and rec.get("parameters"):
            return (rec["parameters"] or {}).get("model")
        return None
