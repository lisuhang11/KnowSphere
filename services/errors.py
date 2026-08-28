"""Service 层领域异常（由 API 映射为 HTTP 状态码）。"""

from __future__ import annotations

class ServiceError(Exception):
    """业务逻辑错误基类。"""

class NotFoundError(ServiceError):
    """资源不存在。"""

class BadRequestError(ServiceError):
    """参数或状态不合法。"""

class UnavailableError(ServiceError):
    """依赖不可用（如 MinIO 未配置）。"""

class ConflictError(ServiceError):
    """资源状态冲突（如正在处理中）。"""
