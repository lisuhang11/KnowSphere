"""会话临时附件存储与解析。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg

from config.settings import get_current_owner, settings
from ingestion.parser import ALLOWED_EXTENSIONS
from utils.attachment_chunks import split_attachment_chunks
from utils.object_store import guess_content_type, require_object_store

logger = logging.getLogger(__name__)

STATUS_UPLOADED = "uploaded"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_ATTACHMENT_BYTES = 20 << 20  # 20MB

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}

# create/get/list 必须使用同一组列，否则 _row_to_dict 解包会炸
_ROW_COLUMN_COUNT = 16
_ROW_COLUMNS = (
    "id, session_id, storage_key, file_name, file_type, mime_type, file_size, "
    "status, content, chunks, image_refs, image_description, error_message, "
    "expires_at, created_at, updated_at"
)

def ensure_temporary_attachments_table() -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ks_temporary_attachments (
                id           UUID PRIMARY KEY,
                session_id   UUID NOT NULL,
                storage_key  TEXT NOT NULL,
                file_name    TEXT NOT NULL,
                file_type    TEXT,
                mime_type    TEXT,
                file_size    BIGINT NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'uploaded',
                content      TEXT,
                chunks       JSONB,
                image_refs   JSONB NOT NULL DEFAULT '[]'::jsonb,
                image_description TEXT,
                error_message TEXT,
                expires_at   TIMESTAMPTZ,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "ALTER TABLE ks_temporary_attachments "
            "ADD COLUMN IF NOT EXISTS chunks JSONB"
        )
        conn.execute(
            "ALTER TABLE ks_temporary_attachments "
            "ADD COLUMN IF NOT EXISTS image_refs JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ks_temp_attach_session "
            "ON ks_temporary_attachments (session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ks_temp_attach_expires "
            "ON ks_temporary_attachments (expires_at)"
        )
        conn.commit()

def build_attachment_storage_key(session_id: str, attachment_id: str, file_name: str) -> str:
    owner = get_current_owner() or settings.default_owner
    safe = Path(file_name or "upload").name or "upload"
    return f"{owner}/chat-attachments/{session_id}/{attachment_id}/{safe}"

def attachment_preview_url(session_id: str, attachment_id: str) -> str:
    return f"/api/sessions/{session_id}/attachments/{attachment_id}/preview"

def is_image_attachment(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in _IMAGE_EXTS


def is_image_upload(file_name: str, mime_type: str = "") -> bool:
    """扩展名或 MIME 判定为图片（传图需 VLM，文档附件不需要）。"""
    if is_image_attachment(file_name):
        return True
    return (mime_type or "").lower().startswith("image/")

def validate_attachment_file(file_name: str, file_size: int) -> None:
    ext = Path(file_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件类型: {ext}")
    if file_size <= 0:
        raise ValueError("文件为空")
    if file_size > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"文件不能超过 {MAX_ATTACHMENT_BYTES // (1 << 20)}MB")

class TemporaryAttachmentStore():
    """ks_temporary_attachments CRUD。"""

    def create(
        self,
        *,
        session_id: str,
        file_name: str,
        mime_type: str,
        file_size: int,
        data: bytes,
    ) -> dict[str, Any]:
        validate_attachment_file(file_name, file_size)
        attachment_id = str(uuid.uuid4())
        storage_key = build_attachment_storage_key(session_id, attachment_id, file_name)
        require_object_store().put_bytes(data, storage_key, content_type=mime_type or guess_content_type(file_name))

        ttl_hours = max(1, int(settings.chat_attachment_ttl_hours))
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        ext = Path(file_name).suffix.lower().lstrip(".")

        with psycopg.connect(settings.postgres_dsn) as conn:
            row = conn.execute(
                f"""
                INSERT INTO ks_temporary_attachments (
                    id, session_id, storage_key, file_name, file_type, mime_type,
                    file_size, status, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_ROW_COLUMNS}
                """,
                (
                    attachment_id,
                    session_id,
                    storage_key,
                    file_name,
                    ext,
                    mime_type or guess_content_type(file_name),
                    file_size,
                    STATUS_UPLOADED,
                    expires_at,
                ),
            ).fetchone()
            conn.commit()
        return self._row_to_dict(row)

    def get(self, attachment_id: str, session_id: str) -> dict[str, Any] | None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            row = conn.execute(
                f"""
                SELECT {_ROW_COLUMNS}
                FROM ks_temporary_attachments
                WHERE id = %s AND session_id = %s
                """,
                (attachment_id, session_id),
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_by_session(self, session_id: str) -> list[dict[str, Any]]:
        with psycopg.connect(settings.postgres_dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_ROW_COLUMNS}
                FROM ks_temporary_attachments
                WHERE session_id = %s
                ORDER BY created_at DESC
                """,
                (session_id,),
            ).fetchall()
        return [self._row_to_public(r) for r in rows]

    def mark_processing(self, attachment_id: str) -> None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            conn.execute(
                """
                UPDATE ks_temporary_attachments
                SET status = %s, updated_at = now()
                WHERE id = %s AND status = %s
                """,
                (STATUS_PROCESSING, attachment_id, STATUS_UPLOADED),
            )
            conn.commit()

    def mark_ready(
        self,
        attachment_id: str,
        *,
        content: str,
        image_description: str = "",
        chunks: list[dict[str, Any]] | None = None,
        image_refs: list[dict[str, Any]] | None = None,
    ) -> None:
        chunk_list = chunks if chunks is not None else split_attachment_chunks(content)
        chunks_json = json.dumps(chunk_list, ensure_ascii=False)
        refs_json = json.dumps(image_refs or [], ensure_ascii=False)
        with psycopg.connect(settings.postgres_dsn) as conn:
            conn.execute(
                """
                UPDATE ks_temporary_attachments
                SET status = %s, content = %s, chunks = %s::jsonb,
                    image_refs = %s::jsonb, image_description = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    STATUS_READY,
                    content,
                    chunks_json,
                    refs_json,
                    image_description or None,
                    attachment_id,
                ),
            )
            conn.commit()

    def mark_failed(self, attachment_id: str, error_message: str) -> None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            conn.execute(
                """
                UPDATE ks_temporary_attachments
                SET status = %s, error_message = %s, updated_at = now()
                WHERE id = %s
                """,
                (STATUS_FAILED, error_message[:2000], attachment_id),
            )
            conn.commit()

    def delete(self, attachment_id: str, session_id: str) -> bool:
        with psycopg.connect(settings.postgres_dsn) as conn:
            row = conn.execute(
                "SELECT storage_key, image_refs FROM ks_temporary_attachments "
                "WHERE id = %s AND session_id = %s",
                (attachment_id, session_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM ks_temporary_attachments WHERE id = %s AND session_id = %s",
                (attachment_id, session_id),
            )
            conn.commit()
        keys = [row[0]] if row[0] else []
        for ref in _parse_json_list(row[1]):
            key = (ref.get("storage_key") or "").strip()
            if key:
                keys.append(key)
        try:
            require_object_store().delete_objects(keys)
        except Exception as exc:
            logger.warning("删除临时附件对象失败 %s: %s", attachment_id, exc)
        return True

    def get_extracted_image(
        self, attachment_id: str, session_id: str, filename: str
    ) -> tuple[str, str] | None:
        """返回抽取图片的 (storage_key, mime_type)。"""
        safe = Path(filename or "").name
        if not safe:
            return None
        row = self.get(attachment_id, session_id)
        if not row:
            return None
        for ref in row.get("image_refs") or []:
            if (ref.get("filename") or "") != safe:
                continue
            key = (ref.get("storage_key") or "").strip() or row["storage_key"]
            return key, (ref.get("mime_type") or "")
        return None

    def get_storage_key(self, attachment_id: str, session_id: str) -> tuple[str, str] | None:
        row = self.get(attachment_id, session_id)
        if not row:
            return None
        return row["storage_key"], row["file_name"]

    def list_expired(self, before: datetime | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
        """列出已过期的附件（用于定时清理）。"""
        cutoff = before or datetime.now(timezone.utc)
        with psycopg.connect(settings.postgres_dsn) as conn:
            rows = conn.execute(
                f"""
                SELECT {_ROW_COLUMNS}
                FROM ks_temporary_attachments
                WHERE expires_at IS NOT NULL AND expires_at < %s
                ORDER BY expires_at ASC
                LIMIT %s
                """,
                (cutoff, limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def cleanup_expired(self, *, batch_size: int = 100) -> int:
        """删除过期附件及 MinIO 对象，返回清理数量。"""
        deleted = 0
        while True:
            expired = self.list_expired(limit=batch_size)
            if not expired:
                break
            for row in expired:
                if self.delete(row["id"], row["session_id"]):
                    deleted += 1
            if len(expired) < batch_size:
                break
        return deleted

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        if row is None:
            raise ValueError("empty attachment row")
        if len(row) != _ROW_COLUMN_COUNT:
            raise ValueError(
                f"attachment row expected {_ROW_COLUMN_COUNT} columns, got {len(row)}"
            )
        (
            aid,
            session_id,
            storage_key,
            file_name,
            file_type,
            mime_type,
            file_size,
            status,
            content,
            chunks_raw,
            image_refs_raw,
            image_description,
            error_message,
            expires_at,
            created_at,
            updated_at,
        ) = row
        return {
            "id": str(aid),
            "session_id": str(session_id),
            "storage_key": storage_key,
            "file_name": file_name,
            "file_type": file_type or "",
            "mime_type": mime_type or "",
            "file_size": int(file_size or 0),
            "status": status,
            "content": content,
            "chunks": _parse_json_list(chunks_raw) or None,
            "image_refs": _parse_json_list(image_refs_raw),
            "image_description": image_description or "",
            "error_message": error_message or "",
            "expires_at": expires_at.isoformat() if expires_at else None,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

    @staticmethod
    def _row_to_public(row) -> dict[str, Any]:
        d = TemporaryAttachmentStore._row_to_dict(row)
        d.pop("storage_key", None)
        d.pop("content", None)
        d.pop("chunks", None)
        d["image_refs"] = [
            {k: v for k, v in ref.items() if k != "storage_key"}
            for ref in (d.get("image_refs") or [])
            if isinstance(ref, dict)
        ]
        return d


def _parse_json_list(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
    return []
