"""web_fetch：抓取公开网页正文（带 SSRF 限制）。"""

from __future__ import annotations

import ipaddress
import socket
from typing import Annotated
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from config.settings import settings
from schemas import RetrievalResult, Source
from tools.events import emit_thinking, emit_tool_call, emit_tool_result
from utils.run_config import web_search_enabled_from_config

_BLOCKED_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "metadata.google.internal"}
)


def _host_blocked(host: str) -> bool:
    hostname = (host or "").strip().lower().rstrip(".")
    if not hostname or hostname in _BLOCKED_HOSTS:
        return True
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True
    return False


def _extract_text(html: str, limit: int) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())
    return text[:limit]


@tool
def web_fetch(
    url: str,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,  # noqa: B008
) -> dict:
    """抓取指定公开网页的正文，适合 web_search 之后需要阅读全文时。

    禁止抓取内网、localhost 或非 http(s) 地址。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    if not web_search_enabled_from_config(config):
        return RetrievalResult(
            query=url,
            sources=[],
            note="本轮未开启联网搜索，无法读取网页。",
        ).model_dump()
    raw = (url or "").strip()
    emit_tool_call("web_fetch", raw, writer)
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        emit_tool_result("web_fetch", "URL 无效", success=False, writer=writer)
        return RetrievalResult(
            query=raw,
            sources=[],
            note="仅支持 http/https 网址。",
        ).model_dump()
    if _host_blocked(parsed.hostname or ""):
        emit_tool_result("web_fetch", "已拦截内网地址", success=False, writer=writer)
        return RetrievalResult(
            query=raw,
            sources=[],
            note="出于安全原因，拒绝抓取内网或本机地址。",
        ).model_dump()

    emit_thinking(f"正在读取网页：{raw}", writer)
    try:
        with httpx.Client(
            timeout=settings.web_fetch_timeout_sec,
            follow_redirects=True,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )},
        ) as client:
            resp = client.get(raw)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            encoding = resp.encoding or "utf-8"
            data = resp.content[: settings.web_fetch_max_bytes]
    except Exception as exc:
        emit_tool_result("web_fetch", f"抓取失败：{exc}", success=False, writer=writer)
        return RetrievalResult(
            query=raw,
            sources=[],
            note=f"网页抓取失败：{exc}",
        ).model_dump()

    decoded = data.decode(encoding, errors="ignore")
    if "html" in content_type or not content_type:
        body = _extract_text(decoded, 8000)
    else:
        body = decoded[:8000]

    title = parsed.netloc
    if body:
        soup = BeautifulSoup(decoded, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip() or title

    emit_tool_result("web_fetch", f"已读取 {len(body)} 字", success=bool(body), writer=writer)
    sources = []
    if body:
        sources.append(
            Source(
                document_id=raw,
                file_name=title,
                chunk_index=0,
                score=1.0,
                snippet=body[:400],
            )
        )
    note = body if body else "页面没有可提取的正文。"
    # 全文放 note，便于模型阅读；snippet 仅作引用预览
    result = RetrievalResult(query=raw, sources=sources, note=note).model_dump()
    return result
