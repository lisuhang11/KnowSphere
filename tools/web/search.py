"""web_search：联网搜索。优先 DuckDuckGo；国内网络常不可达时回退 Bing。"""

from __future__ import annotations

import base64
import logging
from typing import Annotated
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from langgraph.prebuilt import ToolRuntime

from config.settings import settings
from schemas import RetrievalResult, Source
from tools.events import emit_citation_sources, emit_thinking, emit_tool_call, emit_tool_result
from utils.run_config import web_search_enabled_from_config

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _timeout_sec() -> float:
    return max(3.0, float(settings.web_search_timeout_sec or 8))


def _looks_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def unwrap_bing_href(raw: str) -> str:
    """还原 Bing 跳转链 `https://www.bing.com/ck/a?...&u=a1...` 中的目标 URL。"""
    href = (raw or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if "bing.com" not in host or "/ck/a" not in (parsed.path or ""):
        return href
    u_vals = parse_qs(parsed.query).get("u", [])
    if not u_vals:
        return href
    token = u_vals[0]
    if len(token) <= 2:
        return href
    payload = token[2:]
    padding = "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(payload + padding).decode()
    except Exception:
        return href


def _hit(title: str, href: str, body: str) -> dict[str, str] | None:
    url = (href or "").strip()
    name = (title or "").strip() or url
    if not url and not name:
        return None
    return {"title": name, "href": url, "body": (body or "").strip()}


def _ddgs_results(query: str, max_results: int) -> list[dict[str, str]]:
    """只打 DuckDuckGo 自身，避免 auto 先撞 Wikipedia 等被墙源。"""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]

    rows: list[dict[str, str]] = []
    region = "cn-zh" if _looks_cjk(query) else "wt-wt"
    with DDGS(timeout=int(_timeout_sec())) as ddg:
        items = ddg.text(
            query,
            max_results=max_results,
            region=region,
            backend="duckduckgo",
        ) or []
        for item in items:
            hit = _hit(
                str(item.get("title") or ""),
                str(item.get("href") or item.get("url") or ""),
                str(item.get("body") or item.get("snippet") or ""),
            )
            if hit:
                rows.append(hit)
    return rows


def _bing_results(query: str, max_results: int) -> list[dict[str, str]]:
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8" if _looks_cjk(query) else "en-US,en;q=0.9",
    }
    params: dict[str, str] = {"q": query}
    if _looks_cjk(query):
        params["setlang"] = "zh-CN"
        params["cc"] = "CN"
    with httpx.Client(
        timeout=_timeout_sec(),
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = client.get("https://www.bing.com/search", params=params)
        resp.raise_for_status()
        html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    for item in soup.select("li.b_algo"):
        anchor = item.select_one("h2 a")
        if anchor is None:
            continue
        href = unwrap_bing_href(str(anchor.get("href") or ""))
        title = anchor.get_text(" ", strip=True)
        caption = item.select_one("p")
        body = caption.get_text(" ", strip=True) if caption is not None else ""
        hit = _hit(title, href, body)
        if not hit:
            continue
        rows.append(hit)
        if len(rows) >= max_results:
            break
    return rows


def search_web_hits(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
    """DuckDuckGo 优先；空结果或异常时回退 Bing。返回 (hits, engine_or_error)。"""
    errors: list[str] = []
    try:
        hits = _ddgs_results(query, max_results)
        if hits:
            return hits, "duckduckgo"
        errors.append("DuckDuckGo 无结果")
    except Exception as exc:
        logger.info("DuckDuckGo 搜索失败，回退 Bing: %s", exc)
        errors.append(f"DuckDuckGo: {exc}")
    try:
        hits = _bing_results(query, max_results)
        if hits:
            return hits, "bing"
        errors.append("Bing 无结果")
    except Exception as exc:
        logger.warning("Bing 搜索失败: %s", exc)
        errors.append(f"Bing: {exc}")
    return [], "；".join(errors) or "未找到网页结果"


@tool
def web_search(
    query: str,
    max_results: int = 5,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,  # noqa: B008
) -> dict:
    """联网搜索公开网页，返回标题、链接与摘要。

    适合实时新闻、天气、库外资料、需要最新信息的问题。
    知识库内的人物/项目/文档请用 doc_retrieval，不要用本工具顶替。
    """
    writer = getattr(runtime, "stream_writer", None) if runtime is not None else None
    if not web_search_enabled_from_config(config):
        return RetrievalResult(
            query=query,
            sources=[],
            note="本轮未开启联网搜索。请打开输入框的联网开关，或改用知识库检索。",
        ).model_dump()

    q = (query or "").strip()
    emit_tool_call("web_search", q, writer)
    emit_thinking(f"正在联网搜索：{q}", writer)
    k = max_results if max_results and max_results > 0 else settings.web_search_max_results
    k = min(max(k, 1), 10)

    hits, engine = search_web_hits(q, k)
    sources = [
        Source(
            document_id=h["href"] or h["title"],
            file_name=h["title"] or h["href"],
            chunk_index=0,
            score=1.0,
            snippet=(h["body"] or "")[:400],
        )
        for h in hits
    ]
    if sources:
        emit_tool_result(
            "web_search",
            f"命中 {len(sources)} 条网页（{engine}）",
            success=True,
            writer=writer,
        )
        emit_citation_sources(sources, writer)
        return RetrievalResult(query=q, sources=sources, note=f"来源：{engine}").model_dump()

    emit_tool_result("web_search", f"未找到网页结果（{engine}）", success=False, writer=writer)
    return RetrievalResult(
        query=q,
        sources=[],
        note=(
            f"联网搜索未找到相关结果（{engine}）。"
            "请改用更短的关键词再搜一次，或提示用户检查网络后重试。"
        ),
    ).model_dump()
