"""Service 异常 → HTTPException 映射。"""

from __future__ import annotations

from fastapi import HTTPException

from services.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceError,
    UnavailableError,
)

def map_service_error(
    exc: Exception,
    *,
    default_status: int = 400,
    default_prefix: str | None = None,
) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, BadRequestError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, UnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ServiceError):
        return HTTPException(status_code=default_status, detail=str(exc))
    if default_prefix:
        return HTTPException(status_code=default_status, detail=f"{default_prefix}: {exc}")
    if isinstance(exc, HTTPException):
        return exc
    return HTTPException(status_code=default_status, detail=str(exc))
