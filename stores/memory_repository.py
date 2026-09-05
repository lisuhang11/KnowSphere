"""长期记忆表：画像 / 兴趣 / 文档亲和（对齐 WeKnora memory_items + memory_doc_affinity）。"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from config.settings import settings

logger = logging.getLogger(__name__)


def new_memory_id() -> str:
    return f"mem-{uuid.uuid4().hex}"


class MemoryStore:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.postgres_dsn

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(
            self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=2
        ) as conn:
            yield conn

    def list_active_by_kinds(
        self, owner: str, kinds: list[str], limit: int = 30
    ) -> list[dict[str, Any]]:
        if not owner or not kinds:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, owner, kind, content, origin, importance
                FROM memory_items
                WHERE owner = %s AND status = 'active' AND kind = ANY(%s)
                ORDER BY importance DESC, updated_at DESC
                LIMIT %s
                """,
                (owner, kinds, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_item(
        self,
        *,
        owner: str,
        kind: str,
        content: str,
        normalized_key: str,
        origin: str = "explicit",
        importance: int = 3,
        source_session_id: str = "",
    ) -> dict[str, Any] | None:
        if not owner or not content:
            return None
        item_id = new_memory_id()
        with self._conn() as conn:
            if normalized_key:
                row = conn.execute(
                    """
                    UPDATE memory_items
                    SET content = %s,
                        importance = GREATEST(importance, %s),
                        updated_at = now()
                    WHERE owner = %s AND kind = %s AND normalized_key = %s
                      AND status = 'active'
                    RETURNING id, owner, kind, content, origin, importance
                    """,
                    (content, importance, owner, kind, normalized_key),
                ).fetchone()
                if row:
                    return dict(row)
            row = conn.execute(
                """
                INSERT INTO memory_items (
                    id, owner, kind, content, normalized_key, origin,
                    status, importance, source_session_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                RETURNING id, owner, kind, content, origin, importance
                """,
                (
                    item_id,
                    owner,
                    kind,
                    content,
                    normalized_key,
                    origin,
                    importance,
                    source_session_id,
                ),
            ).fetchone()
        return dict(row) if row else None

    def bump_doc_affinity(
        self, owner: str, document_id: str, title: str
    ) -> None:
        if not owner or not document_id:
            return
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memory_doc_affinity (owner, document_id, title, hits, updated_at)
                VALUES (%s, %s, %s, 1, now())
                ON CONFLICT (owner, document_id) DO UPDATE SET
                    title = CASE
                        WHEN EXCLUDED.title <> '' THEN EXCLUDED.title
                        ELSE memory_doc_affinity.title
                    END,
                    hits = memory_doc_affinity.hits + 1,
                    updated_at = now()
                """,
                (owner, document_id, title),
            )

    def top_doc_affinity(self, owner: str, limit: int = 5) -> list[dict[str, Any]]:
        if not owner:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT document_id, title, hits
                FROM memory_doc_affinity
                WHERE owner = %s
                ORDER BY hits DESC, updated_at DESC
                LIMIT %s
                """,
                (owner, limit),
            ).fetchall()
        return [dict(r) for r in rows]
