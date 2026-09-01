"""Ollama 本地服务探测（原生 /api，推理仍走 OpenAI 兼容 /v1）。"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import settings


def ollama_host() -> str:
    return settings.ollama_base_url.rstrip("/")


def fetch_ollama_status(timeout: float = 3.0) -> dict[str, Any]:
    host = ollama_host()
    try:
        resp = httpx.get(f"{host}/api/version", timeout=timeout)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return {
            "ok": True,
            "host": host,
            "version": data.get("version") if isinstance(data, dict) else None,
            "message": "Ollama 可用",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "host": host,
            "version": None,
            "message": f"无法连接 Ollama（{host}）: {exc}",
        }


def list_ollama_models(timeout: float = 8.0) -> dict[str, Any]:
    host = ollama_host()
    try:
        resp = httpx.get(f"{host}/api/tags", timeout=timeout)
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        raw = payload.get("models") if isinstance(payload, dict) else None
        models: list[dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if not name:
                continue
            models.append(
                {
                    "name": str(name),
                    "size": item.get("size"),
                    "modified_at": item.get("modified_at") or item.get("modifiedAt"),
                }
            )
        return {"ok": True, "host": host, "models": models, "message": f"已列出 {len(models)} 个模型"}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "host": host,
            "models": [],
            "message": f"无法获取 Ollama 模型列表（{host}）: {exc}",
        }
