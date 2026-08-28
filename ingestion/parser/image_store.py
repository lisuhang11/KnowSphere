"""MinIO 图片存储（解析阶段产出；key 与原始文档共用 utils.object_store 约定）。"""

from __future__ import annotations

import logging

from ingestion.parser.base_parser import ImageRef, ParserError
from utils.object_store import get_object_store

logger = logging.getLogger(__name__)


class MinioImageStore:
    """图片代理读取：委托 MinioObjectStore。"""

    def __init__(self, store):
        self._store = store

    def get_image(self, storage_key: str) -> tuple[bytes, str]:
        try:
            return self._store.get_bytes(storage_key)
        except Exception as exc:
            raise ParserError(str(exc)) from exc


def get_image_store() -> MinioImageStore | None:
    store = get_object_store()
    if store is None:
        return None
    return MinioImageStore(store)


def upload_parse_images(
    image_refs: list[ImageRef],
    kb_id: str,
    document_id: str,
    tenant: str = "default",
) -> list[dict]:
    """把 ParseResult 收集的图片上传 MinIO，回填 storage_key。"""
    store = get_object_store()
    result: list[dict] = []
    for ref in image_refs:
        if not ref.data:
            result.append(ref.to_dict())
            continue
        storage_key = f"{tenant}/{kb_id}/{document_id}/{ref.filename}"
        if store is not None:
            try:
                store.put_bytes(
                    ref.data,
                    storage_key,
                    ref.mime_type or "image/jpeg",
                )
                ref.storage_key = storage_key
            except Exception as exc:
                logger.warning("图片上传失败 %s: %s", ref.filename, exc)
        else:
            logger.debug("MinIO 未配置，跳过图片上传 %s", ref.filename)
        result.append(ref.to_dict())
    return result


__all__ = ["MinioImageStore", "get_image_store", "upload_parse_images"]
