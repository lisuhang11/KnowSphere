"""MinIO 对象存储（原始文档与解析图片统一走对象存储，不落本地 data/）。

storage_key 约定（与图片链路一致）：
  {owner}/{kb_id}/{document_id}/{filename}

documents.stored_name 存上述 key（历史本地文件名无 `/` 时走 legacy 磁盘回退）。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from config.settings import get_current_owner, settings

logger = logging.getLogger(__name__)

class ObjectStoreError(Exception):
    """对象存储操作失败。"""

class MinioObjectStore:
    """MinIO 单桶封装：文档原文件 + 解析图片共用。"""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        region: str | None = None,
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.secure = secure
        self.region = region
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from minio import Minio
            except ImportError as exc:
                raise ObjectStoreError("minio 未安装，请执行 pip install minio") from exc
            self._client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
                region=self.region,
            )
        return self._client

    def ensure_bucket(self) -> None:
        client = self.client
        if not client.bucket_exists(self.bucket):
            client.make_bucket(self.bucket)
            logger.info("MinIO bucket %s 已创建", self.bucket)

    def put_bytes(
        self, data: bytes, storage_key: str, content_type: str = "application/octet-stream"
    ) -> str:
        if not data:
            raise ObjectStoreError(f"空对象无法上传: {storage_key}")
        client = self.client
        client.put_object(
            self.bucket,
            storage_key,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.debug("MinIO 已上传 %s (%d bytes)", storage_key, len(data))
        return storage_key

    def get_bytes(self, storage_key: str) -> tuple[bytes, str]:
        client = self.client
        try:
            response = client.get_object(self.bucket, storage_key)
            try:
                data = response.read()
            finally:
                response.close()
                response.release_conn
        except Exception as exc:
            raise ObjectStoreError(f"MinIO 读取失败 {storage_key}: {exc}") from exc
        try:
            stat = client.stat_object(self.bucket, storage_key)
            content_type = stat.content_type or "application/octet-stream"
        except Exception:
            content_type = "application/octet-stream"
        return data, content_type

    def delete_object(self, storage_key: str) -> None:
        if not storage_key:
            return
        try:
            self.client.remove_object(self.bucket, storage_key)
        except Exception as exc:
            logger.warning("MinIO 删除失败 %s: %s", storage_key, exc)

    def delete_objects(self, storage_keys: list[str]) -> None:
        for key in storage_keys:
            self.delete_object(key)

def build_document_storage_key(
    owner: str,
    kb_id: int,
    document_id: str,
    file_name: str,
) -> str:
    """原始上传文件的 MinIO key（basename 防路径穿越）。"""
    safe = Path(file_name or "upload").name or "upload"
    return f"{owner}/{kb_id}/{document_id}/{safe}"

def inline_content_disposition(file_name: str) -> str:
    """Content-Disposition: inline，支持中文等非 ASCII 文件名（RFC 5987）。"""
    safe_ascii = Path(file_name or "file").name.encode("ascii", "ignore").decode() or "file"
    encoded = quote(Path(file_name or "file").name)
    return f'inline; filename="{safe_ascii}"; filename*=UTF-8\'\'{encoded}'


def guess_content_type(file_name: str) -> str:
    content_type, _ = mimetypes.guess_type(file_name)
    return content_type or "application/octet-stream"

def is_minio_storage_key(stored_name: str | None) -> bool:
    """区分 MinIO key 与历史本地磁盘名（{document_id}_{file_name}）。"""
    if not stored_name:
        return False
    return "/" in stored_name

def materialize_document_path(
    stored_name: str | None,
    file_name: str,
    legacy_file_path: str | None = None,
) -> tuple[str, bool]:
    """将文档还原为本地可读路径供解析器使用。返回 (path, is_temp)。"""
    if is_minio_storage_key(stored_name):
        store = require_object_store()
        data, _ = store.get_bytes(stored_name)
        suffix = Path(file_name).suffix or ".bin"
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="ks-doc-")
        try:
            with open(fd, "wb") as f:
                f.write(data)
        except Exception:
            Path(path).unlink(missing_ok=True)
            raise
        return path, True

    if legacy_file_path and Path(legacy_file_path).is_file:
        return legacy_file_path, False

    if stored_name:
        legacy = Path(settings.upload_dir) / Path(stored_name).name
        if legacy.is_file:
            return str(legacy), False

    raise FileNotFoundError("原始文件不在对象存储且本地无副本，请重新上传")

def read_document_text(stored_name: str | None, file_name: str) -> str:
    """读取 md/txt 原文（MinIO 或 legacy 本地）。"""
    path, is_temp = materialize_document_path(stored_name, file_name)
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    finally:
        if is_temp:
            Path(path).unlink(missing_ok=True)


def read_document_bytes(stored_name: str | None, file_name: str) -> tuple[bytes, str]:
    """读取原始文件字节（MinIO 或 legacy 本地），返回 (data, content_type)。"""
    path, is_temp = materialize_document_path(stored_name, file_name)
    try:
        data = Path(path).read_bytes()
    finally:
        if is_temp:
            Path(path).unlink(missing_ok=True)
    return data, guess_content_type(file_name)

def collect_document_storage_keys(
    stored_name: str | None,
    image_refs: list[dict] | None,
) -> list[str]:
    keys: list[str] = []
    if is_minio_storage_key(stored_name):
        keys.append(stored_name)
    for ref in image_refs or []:
        sk = ref.get("storage_key")
        if sk:
            keys.append(str(sk))
    return keys

@lru_cache(maxsize=1)
def get_object_store() -> MinioObjectStore | None:
    endpoint = settings.minio_endpoint.strip()
    if not endpoint:
        return None
    return MinioObjectStore(
        endpoint=endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket=settings.minio_bucket,
        secure=settings.minio_secure,
        region=None,
    )

def require_object_store() -> MinioObjectStore:
    store = get_object_store()
    if store is None:
        raise ObjectStoreError(
            "MinIO 未配置：请设置 MINIO_ENDPOINT 并启动 docker compose minio 服务"
        )
    store.ensure_bucket()
    return store

__all__ = [
    "ObjectStoreError",
    "MinioObjectStore",
    "build_document_storage_key",
    "guess_content_type",
    "is_minio_storage_key",
    "materialize_document_path",
    "read_document_text",
    "read_document_bytes",
    "collect_document_storage_keys",
    "get_object_store",
    "require_object_store",
]
