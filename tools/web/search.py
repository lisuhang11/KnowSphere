"""web_search：联网搜索。

对齐 WeKnora：DuckDuckGo 走 HTML 抓取 + Instant Answer API，不依赖易超时的
ddgs 客户端。国内网络通常打不开 DDG，因此 **Bing HTML（cn.bing.com）优先**，
DDG 短超时回退；口语化中文会改写成「实体 + 时效词」再搜。
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Annotated
from urllib.parse import parse_qs, unquote, urljoin, urlparse

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

# 口语/时效词：出现在问句开头时 Bing 常按词典义召回（「最近」→歌曲）
_LEADING_FLUFF = ("你知道", "请问", "帮我查一下", "帮我搜一下", "帮我搜")
_LEADING_CUES = (
    "最近比较火的",
    "最近很火的",
    "比较火的",
    "很火的",
    "最近",
    "最新",
    "今天",
    "昨日",
    "昨天",
    "本周",
    "刚刚",
)
_TRAILING_FLUFF = (
    "相关的事吗",
    "相关的事情",
    "相关的事",
    "相关事件",
    "是什么",
    "怎么样",
    "吗？",
    "吗?",
    "吗",
)
_CUE_TOKENS = frozenset(
    {
        "最近",
        "最新",
        "今天",
        "昨日",
        "昨天",
        "本周",
        "刚刚",
        "比较火",
        "很火",
        "热搜",
        "热议",
        "热门",
        "话题",
        "新闻",
        "实时",
        "相关",
        "微博",
    }
)
_PLATFORM_CUES = frozenset({"微博", "微信", "抖音", "头条", "知乎", "weibo"})
_HOT_MARKERS = ("热搜", "热门", "话题", "比较火", "很火", "热议", "微博", "weibo")
# 从问句里剥出口语/平台词，剩下的才是实体（「微博最近关于景甜…」→ 景甜）
_STOP_SPLIT = (
    "最近比较火的",
    "最近很火的",
    "比较火的",
    "很火的",
    "最近关于",
    "的话题是啥",
    "话题是啥",
    "热门话题",
    "是什么",
    "怎么样",
    "是啥",
    "比较火",
    "很火",
    "热搜",
    "热议",
    "热门",
    "话题",
    "关于",
    "最近",
    "最新",
    "今天",
    "昨日",
    "昨天",
    "本周",
    "刚刚",
    "微博",
    "weibo",
    "你知道",
    "请问",
)
_STOP_SPLIT_RE = re.compile(
    "|".join(sorted((re.escape(s) for s in _STOP_SPLIT), key=len, reverse=True)),
    re.IGNORECASE,
)
_CHUNK_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9._-]{1,}|[0-9]+(?:\.[0-9]+)+")
_JUNK_TITLE_RE = re.compile(
    r"登录|sign\s*in|visitor\s*system|通行证|汉语文字|新华字典|的拼音|的部首|汉语国学",
    re.IGNORECASE,
)


def _timeout_sec() -> float:
    return max(3.0, float(settings.web_search_timeout_sec or 8))


def _ddg_timeout_sec() -> float:
    """DDG 在国内常不可达；短超时避免拖住整轮（ddgs 客户端会把 8s 耗尽）。"""
    return min(3.0, _timeout_sec())


def _looks_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _http_client(timeout: float, *, accept_language: str | None = None) -> httpx.Client:
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": accept_language or "zh-CN,zh;q=0.9,en;q=0.8",
    }
    kwargs: dict = {"timeout": timeout, "follow_redirects": True, "headers": headers}
    proxy = (getattr(settings, "web_search_proxy", "") or "").strip()
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


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
    except (ValueError, UnicodeDecodeError):
        return href


def clean_ddg_url(url_str: str) -> str:
    """还原 DuckDuckGo HTML 跳转链（对齐 WeKnora cleanDDGURL）。"""
    href = (url_str or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = (parsed.netloc or "").lower()
    if "duckduckgo.com" in host and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return unquote(uddg[0])
    if "uddg=" in href:
        token = href.split("uddg=", 1)[1]
        token = token.split("&", 1)[0]
        decoded = unquote(token)
        if decoded.startswith("http"):
            return decoded
    return href


def _url_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "/").rstrip("/") or "/"
    return f"{host}{path}"


def _is_junk_hit(title: str, href: str) -> bool:
    """丢掉登录墙、站点首页、字典页。微博访客页常带「图片无法显示」。"""
    if _JUNK_TITLE_RE.search(title or ""):
        return True
    parsed = urlparse(href or "")
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = (parsed.path or "").lower().rstrip("/") or "/"
    query = (parsed.query or "").lower()
    if any(x in path for x in ("/login", "/signin", "/sso", "/passport")):
        return True
    if "passport." in host:
        return True
    if host.endswith(("weibo.com", "weibo.cn")):
        if path in ("/", "/login.php") or "visitor" in (title or "").lower():
            return True
        if host.startswith("s.weibo.") and "q=" not in query:
            return True
        if host in {"d.weibo.com", "video.weibo.com"} and "login" in path:
            return True
    return False


def _hit(title: str, href: str, body: str) -> dict[str, str] | None:
    url = (href or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    name = (title or "").strip() or url
    if not url or not name:
        return None
    if url.startswith(("javascript:", "about:", "#")):
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if _is_junk_hit(name, url):
        return None
    return {"title": name, "href": url, "body": (body or "").strip()}


def web_query_variants(query: str) -> list[str]:
    """口语/时效词前置的中文问句改成「实体 + 最近」，避免 Bing 按词典召回。

    「最近比较火的景甜」→「景甜 热搜 / 景甜 最近」；
    「微博最近关于景甜比较火的话题」→「景甜 热搜」（不要带「微博」，否则全是登录页）。
    """
    q = " ".join((query or "").split())
    if not q:
        return []
    out: list[str] = []
    entities = _entity_tokens(q)
    lowered = q.lower()
    is_hot = any(m.lower() in lowered for m in _HOT_MARKERS)
    if entities:
        name = " ".join(entities[:2])
        if is_hot:
            out.extend([f"{name} 热搜", f"{name} 新闻", f"{name} 最近"])
        elif any(p in q for p in _PLATFORM_CUES) or "weibo" in lowered:
            out.extend([f"{name} 热搜", f"{name} 新闻"])

    rest = q
    for fluff in _LEADING_FLUFF:
        rest = rest.replace(fluff, " ")
    rest = " ".join(rest.split())
    moved: list[str] = []
    changed = True
    while changed and rest:
        changed = False
        for cue in _LEADING_CUES:
            if rest.startswith(cue):
                moved.append(cue)
                rest = rest[len(cue) :].lstrip("的 ").strip()
                rest = " ".join(rest.split())
                changed = True
                break
    for fluff in _TRAILING_FLUFF:
        if rest.endswith(fluff):
            rest = rest[: -len(fluff)].strip()
    rest = " ".join(rest.split())
    if rest and rest != q:
        if moved:
            recency = "最近" if any(
                any(k in m for k in ("最近", "火", "热", "最新")) for m in moved
            ) else moved[0]
            if recency not in rest:
                out.append(f"{rest} {recency}")
        out.append(rest)
    if entities and " ".join(entities[:2]) not in {q, rest}:
        out.append(" ".join(entities[:2]))
    out.append(q)
    unique: list[str] = []
    seen: set[str] = set()
    for item in out:
        cleaned = " ".join(item.split())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def _query_chunks(query: str) -> list[str]:
    """把连续中文按口语/平台词切开，避免整句变成一个 token。"""
    chunks: list[str] = []
    seen: set[str] = set()
    for part in _STOP_SPLIT_RE.split(query or ""):
        part = " ".join((part or "").split())
        if not part:
            continue
        for match in _CHUNK_RE.finditer(part):
            tok = match.group(0)
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            chunks.append(tok)
    return chunks


def _entity_tokens(query: str) -> list[str]:
    skip = {t.lower() for t in (_CUE_TOKENS | _PLATFORM_CUES)}
    return [tok for tok in _query_chunks(query) if tok.lower() not in skip]


def _content_tokens(query: str) -> list[str]:
    return _entity_tokens(query)


def _hit_score(hit: dict[str, str], tokens: list[str]) -> int:
    if not tokens:
        return 1
    title = hit.get("title") or ""
    blob = f"{title} {hit.get('body') or ''}"
    score = 0
    for tok in tokens:
        if tok in title:
            score += 3
        elif tok in blob:
            score += 1
    return score


def parse_bing_html(html: str, max_results: int, *, base_url: str = "https://www.bing.com") -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in soup.select("li.b_algo"):
        anchor = item.select_one("h2 a") or item.select_one("a[href]")
        if anchor is None:
            continue
        href = unwrap_bing_href(urljoin(base_url, str(anchor.get("href") or "")))
        parsed = urlparse(href)
        host = (parsed.netloc or "").lower()
        if "bing.com" in host and (parsed.path.startswith("/search") or parsed.path.startswith("/ck/")):
            continue
        title = anchor.get_text(" ", strip=True)
        caption = item.select_one(".b_caption p") or item.select_one("p")
        body = caption.get_text(" ", strip=True) if caption is not None else ""
        hit = _hit(title, href, body)
        if not hit or hit["href"] in seen:
            continue
        seen.add(hit["href"])
        rows.append(hit)
        if len(rows) >= max_results:
            break
    return rows


def parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """解析 html.duckduckgo.com/html/（WeKnora .web-result / .result__a）。"""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    nodes = soup.select(".web-result, .result")
    for item in nodes:
        anchor = item.select_one("a.result__a") or item.select_one("a.result-link") or item.select_one("a[href]")
        if anchor is None:
            continue
        href = clean_ddg_url(str(anchor.get("href") or ""))
        title = anchor.get_text(" ", strip=True)
        snippet_node = item.select_one(".result__snippet") or item.select_one("td.result-snippet")
        body = snippet_node.get_text(" ", strip=True) if snippet_node is not None else ""
        hit = _hit(title, href, body)
        if not hit or hit["href"] in seen:
            continue
        seen.add(hit["href"])
        rows.append(hit)
        if len(rows) >= max_results:
            break
    return rows


def parse_ddg_api(payload: dict, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    heading = str(payload.get("Heading") or "").strip()
    abstract = str(payload.get("AbstractText") or "").strip()
    abs_url = str(payload.get("AbstractURL") or "").strip()
    if abstract and abs_url:
        hit = _hit(heading or abs_url, abs_url, abstract)
        if hit:
            rows.append(hit)

    def _walk(topics: list) -> None:
        for topic in topics:
            if len(rows) >= max_results:
                return
            if not isinstance(topic, dict):
                continue
            nested = topic.get("Topics")
            if isinstance(nested, list):
                _walk(nested)
                continue
            text = str(topic.get("Text") or "").strip()
            url = str(topic.get("FirstURL") or "").strip()
            if not text or not url:
                continue
            title = text.split(" - ", 1)[0].strip() or text[:80]
            hit = _hit(title, url, text)
            if hit:
                rows.append(hit)

    _walk(list(payload.get("RelatedTopics") or []))
    _walk(list(payload.get("Results") or []))
    return rows[:max_results]


def _bing_results(query: str, max_results: int) -> list[dict[str, str]]:
    cjk = _looks_cjk(query)
    base = "https://cn.bing.com/search" if cjk else "https://www.bing.com/search"
    params: dict[str, str] = {"q": query}
    lang = "zh-CN,zh;q=0.9,en;q=0.8" if cjk else "en-US,en;q=0.9"
    if cjk:
        params["setlang"] = "zh-CN"
    with _http_client(_timeout_sec(), accept_language=lang) as client:
        resp = client.get(base, params=params)
        resp.raise_for_status()
        html = resp.text
        final_url = str(resp.url)
    return parse_bing_html(html, max(max_results * 3, 12), base_url=final_url)


def _ddg_html_results(query: str, max_results: int) -> list[dict[str, str]]:
    params = {"q": query, "kl": "cn-zh" if _looks_cjk(query) else "wt-wt"}
    with _http_client(_ddg_timeout_sec()) as client:
        resp = client.get("https://html.duckduckgo.com/html/", params=params)
        resp.raise_for_status()
        html = resp.text
    return parse_ddg_html(html, max_results)


def _ddg_api_results(query: str, max_results: int) -> list[dict[str, str]]:
    params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
    with _http_client(_ddg_timeout_sec()) as client:
        resp = client.get("https://api.duckduckgo.com/", params=params)
        resp.raise_for_status()
        payload = resp.json()
    if not isinstance(payload, dict):
        return []
    return parse_ddg_api(payload, max_results)


def _ddgs_lib_results(query: str, max_results: int) -> list[dict[str, str]]:
    """最后手段：ddgs 库。国内常不可达且会重试至超时，故超时压到 3s。"""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]

    rows: list[dict[str, str]] = []
    region = "cn-zh" if _looks_cjk(query) else "wt-wt"
    with DDGS(timeout=int(_ddg_timeout_sec())) as ddg:
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


def _searxng_results(query: str, max_results: int) -> list[dict[str, str]]:
    base = (getattr(settings, "web_search_searxng_url", "") or "").strip().rstrip("/")
    if not base:
        return []
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"无效的 SearXNG 地址: {base}")
    params = {"q": query, "format": "json", "language": "all"}
    with _http_client(_timeout_sec()) as client:
        resp = client.get(f"{base}/search", params=params, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
    rows: list[dict[str, str]] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        hit = _hit(
            str(item.get("title") or ""),
            str(item.get("url") or ""),
            str(item.get("content") or ""),
        )
        if hit:
            rows.append(hit)
        if len(rows) >= max_results:
            break
    return rows


def _merge_hits(
    batches: list[tuple[str, list[dict[str, str]]]],
    query: str,
    max_results: int,
) -> tuple[list[dict[str, str]], str]:
    tokens = _content_tokens(query)
    ranked: list[tuple[int, int, str, dict[str, str]]] = []
    seen: set[str] = set()
    order = 0
    engines: list[str] = []
    for engine, hits in batches:
        if not hits:
            continue
        engines.append(engine)
        for hit in hits:
            key = _url_key(hit["href"])
            if key in seen:
                continue
            seen.add(key)
            ranked.append((_hit_score(hit, tokens), -order, engine, hit))
            order += 1
    if not ranked:
        return [], ""
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    if tokens:
        strong = [row for row in ranked if row[0] > 0]
        if strong:
            ranked = strong
    picked = [row[3] for row in ranked[:max_results]]
    used = []
    for row in ranked[:max_results]:
        if row[2] not in used:
            used.append(row[2])
    return picked, "+".join(used) if used else (engines[0] if engines else "")


def _try_engine(name: str, fn, query: str, max_results: int) -> tuple[list[dict[str, str]], str | None]:
    try:
        hits = fn(query, max_results)
        if hits:
            return hits, None
        return [], f"{name} 无结果"
    except Exception as exc:  # noqa: BLE001 — 引擎失败必须吞掉并回退
        logger.info("%s 搜索失败: %s", name, exc)
        return [], f"{name}: {exc}"


def search_web_hits(query: str, max_results: int) -> tuple[list[dict[str, str]], str]:
    """Bing HTML 优先；可选 SearXNG；DDG HTML/API（WeKnora）短超时回退。"""
    q = (query or "").strip()
    if not q:
        return [], "查询为空"
    k = min(max(max_results, 1), 10)
    variants = web_query_variants(q)
    batches: list[tuple[str, list[dict[str, str]]]] = []
    errors: list[str] = []

    searx = (getattr(settings, "web_search_searxng_url", "") or "").strip()
    if searx:
        hits, err = _try_engine("searxng", _searxng_results, variants[0], k)
        if hits:
            batches.append(("searxng", hits))
        elif err:
            errors.append(err)

    tokens = _content_tokens(q)
    # 实体前置变体先搜，再补原问（原问常被 Bing 按「最近」词典召回）
    for variant in variants[:3]:
        hits, err = _try_engine("bing", _bing_results, variant, k)
        if hits:
            batches.append(("bing", hits))
        elif err:
            errors.append(err)
        merged, engine = _merge_hits(batches, q, k)
        if not merged:
            continue
        top_score = _hit_score(merged[0], tokens) if tokens else 1
        if top_score <= 0:
            continue
        # 标题已命中实体，或条数够用，不必再打原问 / DDG
        if len(merged) >= k or top_score >= 3:
            return merged, engine or "bing"

    merged, engine = _merge_hits(batches, q, k)
    if merged:
        return merged, engine or "bing"

    for name, fn in (
        ("duckduckgo-html", _ddg_html_results),
        ("duckduckgo-api", _ddg_api_results),
        ("duckduckgo", _ddgs_lib_results),
    ):
        hits, err = _try_engine(name, fn, variants[0], k)
        if hits:
            return hits, name
        if err:
            errors.append(err)
    return [], "；".join(errors) or "未找到网页结果"


def _format_hits_note(query: str, hits: list[dict[str, str]], engine: str) -> str:
    lines = [
        "=== 网页搜索结果 ===",
        f"查询：{query}",
        f"来源：{engine}",
        f"共 {len(hits)} 条。标题、URL、摘要可作为证据；需要全文时再 web_fetch。",
        "",
    ]
    for i, hit in enumerate(hits, 1):
        lines.append(f"{i}. {hit['title']}")
        lines.append(f"   URL: {hit['href']}")
        if hit.get("body"):
            lines.append(f"   摘要：{hit['body'][:280]}")
        lines.append("")
    return "\n".join(lines).strip()


@tool
def web_search(
    query: str,
    max_results: int = 5,
    runtime: Annotated[ToolRuntime | None, InjectedToolArg] = None,
    config: Annotated[RunnableConfig, InjectedToolArg] = None,
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
    variants = web_query_variants(q)
    thinking = f"正在联网搜索：{q}"
    if variants and variants[0] != q:
        thinking += f"\n改写检索词：{variants[0]}"
    emit_thinking(thinking, writer)
    k = max_results if max_results and max_results > 0 else settings.web_search_max_results
    k = min(max(k, 1), 10)

    hits, engine = search_web_hits(q, k)
    sources = [
        Source(
            document_id=h["href"],
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
        return RetrievalResult(
            query=q,
            sources=sources,
            note=_format_hits_note(q, hits, engine),
        ).model_dump()

    emit_tool_result("web_search", f"未找到网页结果（{engine}）", success=False, writer=writer)
    return RetrievalResult(
        query=q,
        sources=[],
        note=(
            f"联网搜索未找到相关结果（{engine}）。"
            "请改用更短的关键词（核心人名/事件，去掉「最近/比较火」等口语）再搜一次，"
            "或提示用户检查网络后重试。"
        ),
    ).model_dump()
