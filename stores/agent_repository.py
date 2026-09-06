"""智能体持久化。

工具可执行体在代码目录（tools.catalog）；库表只存智能体绑定的工具名。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config.settings import settings
from skills.catalog import known_skill_names, ordered_skill_names, skills_to_public
from stores.common import load_jsonb
from tools.catalog import (
    BUILTIN_AGENT_ID,
    BUILTIN_PPT_AGENT_DESCRIPTION,
    BUILTIN_PPT_AGENT_ID,
    BUILTIN_PPT_AGENT_NAME,
    BUILTIN_PPT_AGENT_PROMPT,
    LEGACY_PPT_AGENT_PROMPT,
    PPT_AGENT_SKILL_NAMES,
    PPT_AGENT_TOOL_NAMES,
    REASONING_TOOL_NAMES,
    known_tool_names,
    ordered_tool_names,
    tools_to_public,
)

logger = logging.getLogger(__name__)

_AGENT_PREFIX = "agent-"

BUILTIN_AGENT_NAME = "智能推理"
BUILTIN_AGENT_DESCRIPTION = "多步思考并调用工具：检索知识库、查询图谱，必要时联网。"


def new_agent_id() -> str:
    return f"{_AGENT_PREFIX}{uuid.uuid4().hex}"


def _as_str_list(value: Any) -> list[str]:
    raw = load_jsonb(value)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip() if item is not None else ""
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


class AgentStore:
    def __init__(self) -> None:
        self.dsn = settings.postgres_dsn

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row) as conn:
            yield conn

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id                VARCHAR(64) PRIMARY KEY,
                    name              TEXT NOT NULL,
                    description       TEXT NOT NULL DEFAULT '',
                    system_prompt     TEXT NOT NULL DEFAULT '',
                    tool_names        JSONB NOT NULL DEFAULT '[]'::jsonb,
                    skill_names       JSONB NOT NULL DEFAULT '[]'::jsonb,
                    max_iterations    INT NOT NULL DEFAULT 25,
                    is_builtin        BOOLEAN NOT NULL DEFAULT FALSE,
                    is_default        BOOLEAN NOT NULL DEFAULT FALSE,
                    status            TEXT NOT NULL DEFAULT 'active',
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            conn.execute(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tool_names "
                "JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
            conn.execute(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS skill_names "
                "JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
            conn.execute(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS audio_upload_enabled "
                "BOOLEAN NOT NULL DEFAULT FALSE"
            )
            conn.execute(
                "ALTER TABLE agents ADD COLUMN IF NOT EXISTS asr_model_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agents_default ON agents (is_default) WHERE is_default"
            )
            self._migrate_legacy_toolkit_bindings(conn)

    def _table_exists(self, conn: psycopg.Connection, name: str) -> bool:
        row = conn.execute("SELECT to_regclass(%s) AS rel", (f"public.{name}",)).fetchone()
        return bool(row and row.get("rel"))

    def _migrate_legacy_toolkit_bindings(self, conn: psycopg.Connection) -> None:
        """把旧的工具包绑定展开成智能体上的 tool_names。"""
        has_toolkits = self._table_exists(conn, "toolkits")
        rows = conn.execute("SELECT * FROM agents").fetchall()
        pack_cache: dict[str, list[str]] = {}
        for row in rows:
            if ordered_tool_names(_as_str_list(row.get("tool_names"))):
                continue
            collected: list[str] = []
            if has_toolkits:
                for tid in _as_str_list(row.get("toolkit_ids")):
                    if tid not in pack_cache:
                        pack = conn.execute(
                            "SELECT tool_names FROM toolkits WHERE id = %s", (tid,)
                        ).fetchone()
                        pack_cache[tid] = _as_str_list(
                            pack.get("tool_names") if pack else None
                        )
                    collected.extend(pack_cache[tid])
            collected.extend(_as_str_list(row.get("extra_tool_names")))
            names = ordered_tool_names(collected)
            if not names:
                continue
            conn.execute(
                "UPDATE agents SET tool_names = %s, updated_at = now() WHERE id = %s",
                (Jsonb(names), row["id"]),
            )

    def seed_builtins(self) -> None:
        """幂等写入内置智能体；工具列表随代码目录刷新，互不混绑。"""
        reasoning_names = list(REASONING_TOOL_NAMES)
        ppt_names = list(PPT_AGENT_TOOL_NAMES)
        with self._conn() as conn:
            existing_ag = conn.execute(
                "SELECT id, is_default FROM agents WHERE id = %s",
                (BUILTIN_AGENT_ID,),
            ).fetchone()
            if existing_ag:
                conn.execute(
                    """
                    UPDATE agents
                    SET tool_names = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (Jsonb(reasoning_names), BUILTIN_AGENT_ID),
                )
            else:
                has_default = conn.execute(
                    "SELECT 1 FROM agents WHERE is_default LIMIT 1"
                ).fetchone()
                conn.execute(
                    """
                    INSERT INTO agents (
                        id, name, description, system_prompt, tool_names, skill_names,
                        max_iterations, is_builtin, is_default
                    )
                    VALUES (%s, %s, %s, '', %s, '[]'::jsonb, %s, TRUE, %s)
                    """,
                    (
                        BUILTIN_AGENT_ID,
                        BUILTIN_AGENT_NAME,
                        BUILTIN_AGENT_DESCRIPTION,
                        Jsonb(reasoning_names),
                        settings.agent_max_steps,
                        has_default is None,
                    ),
                )

            existing_ppt = conn.execute(
                "SELECT id, system_prompt, tool_names, skill_names FROM agents WHERE id = %s",
                (BUILTIN_PPT_AGENT_ID,),
            ).fetchone()
            if existing_ppt:
                conn.execute(
                    """
                    UPDATE agents
                    SET description = CASE
                            WHEN description IN ('', %s) THEN %s
                            ELSE description
                        END,
                        updated_at = now()
                    WHERE id = %s
                    """,
                    (
                        BUILTIN_PPT_AGENT_DESCRIPTION,
                        BUILTIN_PPT_AGENT_DESCRIPTION,
                        BUILTIN_PPT_AGENT_ID,
                    ),
                )
                current_skills = ordered_skill_names(
                    _as_str_list(existing_ppt.get("skill_names"))
                )
                desired_skills = self._ppt_default_skills()
                if not current_skills or set(current_skills) == set(desired_skills):
                    conn.execute(
                        "UPDATE agents SET skill_names = %s, updated_at = now() WHERE id = %s",
                        (Jsonb(desired_skills), BUILTIN_PPT_AGENT_ID),
                    )
                current_prompt = (existing_ppt.get("system_prompt") or "").strip()
                if current_prompt in ("", LEGACY_PPT_AGENT_PROMPT.strip()):
                    conn.execute(
                        "UPDATE agents SET system_prompt = %s, updated_at = now() WHERE id = %s",
                        (BUILTIN_PPT_AGENT_PROMPT, BUILTIN_PPT_AGENT_ID),
                    )
                current_ppt = ordered_tool_names(_as_str_list(existing_ppt.get("tool_names")))
                legacy_ppt = ["write_plan", "doc_retrieval", "generate_pptx"]
                if current_ppt in (legacy_ppt, ppt_names, []):
                    conn.execute(
                        "UPDATE agents SET tool_names = %s, updated_at = now() WHERE id = %s",
                        (Jsonb(ppt_names), BUILTIN_PPT_AGENT_ID),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO agents (
                        id, name, description, system_prompt, tool_names, skill_names,
                        max_iterations, is_builtin, is_default
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, FALSE)
                    """,
                    (
                        BUILTIN_PPT_AGENT_ID,
                        BUILTIN_PPT_AGENT_NAME,
                        BUILTIN_PPT_AGENT_DESCRIPTION,
                        BUILTIN_PPT_AGENT_PROMPT,
                        Jsonb(ppt_names),
                        Jsonb(self._ppt_default_skills()),
                        settings.agent_max_steps,
                    ),
                )

            if not conn.execute("SELECT 1 FROM agents WHERE is_default LIMIT 1").fetchone():
                conn.execute(
                    "UPDATE agents SET is_default = TRUE, updated_at = now() WHERE id = %s",
                    (BUILTIN_AGENT_ID,),
                )

    def _legacy_tool_names(self, row: dict[str, Any]) -> list[str]:
        collected = list(_as_str_list(row.get("extra_tool_names")))
        toolkit_ids = _as_str_list(row.get("toolkit_ids"))
        if not toolkit_ids:
            return ordered_tool_names(collected)
        with self._conn() as conn:
            if not self._table_exists(conn, "toolkits"):
                return ordered_tool_names(collected)
            for tid in toolkit_ids:
                pack = conn.execute(
                    "SELECT tool_names FROM toolkits WHERE id = %s", (tid,)
                ).fetchone()
                if pack:
                    collected.extend(_as_str_list(pack.get("tool_names")))
        return ordered_tool_names(collected)

    def _row_to_agent(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        tool_names = ordered_tool_names(_as_str_list(row.get("tool_names")))
        if not tool_names:
            tool_names = self._legacy_tool_names(row)
        skill_names = ordered_skill_names(_as_str_list(row.get("skill_names")))
        created = row.get("created_at")
        updated = row.get("updated_at")
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description") or "",
            "system_prompt": row.get("system_prompt") or "",
            "tool_names": tool_names,
            "tools": tools_to_public(tool_names),
            "skill_names": skill_names,
            "skills": skills_to_public(skill_names),
            "max_iterations": int(row.get("max_iterations") or settings.agent_max_steps),
            "is_builtin": bool(row.get("is_builtin")),
            "is_default": bool(row.get("is_default")),
            "status": row.get("status") or "active",
            "audio_upload_enabled": bool(row.get("audio_upload_enabled")),
            "asr_model_id": row.get("asr_model_id") or "",
            "created_at": created.isoformat() if created else None,
            "updated_at": updated.isoformat() if updated else None,
        }

    def list_agents(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY is_builtin DESC, is_default DESC, created_at ASC"
            ).fetchall()
        return [rec for rec in (self._row_to_agent(row) for row in rows) if rec]

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM agents WHERE id = %s", (agent_id,)
            ).fetchone()
        return self._row_to_agent(row)

    def get_default_agent(self) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM agents
                WHERE is_default AND status = 'active'
                ORDER BY is_builtin DESC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT * FROM agents WHERE status = 'active'
                    ORDER BY is_builtin DESC, created_at ASC
                    LIMIT 1
                    """
                ).fetchone()
        return self._row_to_agent(row)

    def _validate_tool_names(self, tool_names: list[str] | None) -> list[str]:
        raw = tool_names or []
        unknown = [
            n for n in raw if str(n).strip() and str(n).strip() not in known_tool_names()
        ]
        if unknown:
            raise ValueError(f"未知工具: {', '.join(unknown)}")
        names = ordered_tool_names(raw)
        if not names:
            raise ValueError("请至少选择一个工具")
        return names

    def _ppt_default_skills(self) -> list[str]:
        known = known_skill_names()
        return ordered_skill_names(n for n in PPT_AGENT_SKILL_NAMES if n in known)

    def _validate_skill_names(self, skill_names: list[str] | None) -> list[str]:
        raw = skill_names or []
        unknown = [
            n for n in raw if str(n).strip() and str(n).strip() not in known_skill_names()
        ]
        if unknown:
            raise ValueError(f"未知技能: {', '.join(unknown)}")
        return ordered_skill_names(raw)

    def _clamp_iterations(self, value: int | None) -> int:
        n = int(value if value is not None else settings.agent_max_steps)
        return max(4, min(n, 80))

    def create_agent(
        self,
        name: str,
        description: str = "",
        system_prompt: str = "",
        tool_names: list[str] | None = None,
        skill_names: list[str] | None = None,
        max_iterations: int | None = None,
        *,
        is_default: bool = False,
        is_builtin: bool = False,
        agent_id: str | None = None,
        audio_upload_enabled: bool = False,
        asr_model_id: str = "",
    ) -> dict[str, Any]:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("智能体名称不能为空")
        aid = agent_id or new_agent_id()
        if is_builtin and aid == BUILTIN_PPT_AGENT_ID:
            names = list(PPT_AGENT_TOOL_NAMES)
        elif is_builtin:
            names = list(REASONING_TOOL_NAMES)
        else:
            names = self._validate_tool_names(tool_names)
        skills = self._validate_skill_names(skill_names)
        iterations = self._clamp_iterations(max_iterations)
        with self._conn() as conn, conn.transaction():
            if is_default:
                conn.execute("UPDATE agents SET is_default = FALSE")
            conn.execute(
                """
                    INSERT INTO agents (
                        id, name, description, system_prompt, tool_names, skill_names,
                        max_iterations, is_builtin, is_default,
                        audio_upload_enabled, asr_model_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                (
                    aid,
                    cleaned_name,
                    description or "",
                    system_prompt or "",
                    Jsonb(names),
                    Jsonb(skills),
                    iterations,
                    is_builtin,
                    is_default,
                    bool(audio_upload_enabled),
                    (asr_model_id or "").strip(),
                ),
            )
        rec = self.get_agent(aid)
        if rec is None:
            raise RuntimeError("创建智能体失败")
        return rec

    def update_agent(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        system_prompt: str | None = None,
        tool_names: list[str] | None = None,
        skill_names: list[str] | None = None,
        max_iterations: int | None = None,
        is_default: bool | None = None,
        status: str | None = None,
        audio_upload_enabled: bool | None = None,
        asr_model_id: str | None = None,
    ) -> dict[str, Any]:
        rec = self.get_agent(agent_id)
        if rec is None:
            raise ValueError("智能体不存在")
        sets: list[str] = []
        args: list[Any] = []
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise ValueError("智能体名称不能为空")
            sets.append("name = %s")
            args.append(cleaned)
        if description is not None:
            sets.append("description = %s")
            args.append(description)
        if system_prompt is not None:
            sets.append("system_prompt = %s")
            args.append(system_prompt)
        if tool_names is not None:
            if rec["id"] == BUILTIN_AGENT_ID:
                if ordered_tool_names(tool_names) != list(REASONING_TOOL_NAMES):
                    raise ValueError("内置「智能推理」的工具不可修改")
            else:
                names = self._validate_tool_names(tool_names)
                sets.append("tool_names = %s")
                args.append(Jsonb(names))
        if skill_names is not None:
            skills = self._validate_skill_names(skill_names)
            sets.append("skill_names = %s")
            args.append(Jsonb(skills))
        if max_iterations is not None:
            sets.append("max_iterations = %s")
            args.append(self._clamp_iterations(max_iterations))
        if status is not None:
            if status not in ("active", "disabled"):
                raise ValueError("status 只能是 active 或 disabled")
            if rec["is_builtin"] and status == "disabled":
                raise ValueError("内置智能体不可停用")
            sets.append("status = %s")
            args.append(status)
        if is_default is not None:
            sets.append("is_default = %s")
            args.append(bool(is_default))
        if audio_upload_enabled is not None:
            sets.append("audio_upload_enabled = %s")
            args.append(bool(audio_upload_enabled))
        if asr_model_id is not None:
            sets.append("asr_model_id = %s")
            args.append(asr_model_id.strip())
        if not sets:
            return rec
        sets.append("updated_at = now()")
        args.append(agent_id)
        with self._conn() as conn, conn.transaction():
            if is_default:
                conn.execute("UPDATE agents SET is_default = FALSE")
            conn.execute(
                f"UPDATE agents SET {', '.join(sets)} WHERE id = %s",
                args,
            )
        updated = self.get_agent(agent_id)
        if updated is None:
            raise RuntimeError("更新智能体失败")
        return updated

    def delete_agent(self, agent_id: str) -> None:
        rec = self.get_agent(agent_id)
        if rec is None:
            raise ValueError("智能体不存在")
        if rec["is_builtin"]:
            raise ValueError("内置智能体不可删除")
        if rec["is_default"]:
            raise ValueError("默认智能体不可删除，请先将其它智能体设为默认")
        with self._conn() as conn:
            conn.execute("DELETE FROM agents WHERE id = %s", (agent_id,))
            fallback = conn.execute(
                "SELECT 1 FROM agents WHERE is_default LIMIT 1"
            ).fetchone()
            if fallback is None:
                conn.execute(
                    "UPDATE agents SET is_default = TRUE, updated_at = now() WHERE id = %s",
                    (BUILTIN_AGENT_ID,),
                )
