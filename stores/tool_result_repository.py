"""L1 工具结果外置：Postgres 落库与按 ref_id 取回。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from config.settings import settings

logger = logging.getLogger(__name__)


class ToolResultRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.postgres_dsn

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(
            self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=2
        ) as conn:
            yield conn

    def insert(
        self,
        *,
        ref_id: str,
        thread_id: str,
        owner: str,
        tool_name: str,
        original_length: int,
        summary: str,
        preview: str,
        payload: str,
        expires_at: datetime | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tool_result_refs
                    (ref_id, thread_id, owner, tool_name, original_length,
                     summary, preview, payload, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ref_id) DO NOTHING
                """,
                (
                    ref_id,
                    thread_id,
                    owner,
                    tool_name,
                    original_length,
                    summary,
                    preview,
                    payload,
                    expires_at,
                ),
            )

    def get(self, ref_id: str) -> dict[str, Any] | None:
        if not ref_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT ref_id, thread_id, owner, tool_name, original_length,
                       summary, preview, payload, expires_at
                FROM tool_result_refs
                WHERE ref_id = %s
                  AND (expires_at IS NULL OR expires_at > now())
                """,
                (ref_id,),
            ).fetchone()
        return dict(row) if row else None
