"""web_search：DuckDuckGo 失败时回退 Bing。"""

from __future__ import annotations

import base64
from unittest.mock import patch

from tools.web.search import search_web_hits, unwrap_bing_href, web_search


def test_unwrap_bing_href_decodes_ck_link():
    target = "https://www.sohu.com/a/1067894816_122954941"
    token = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    wrapped = f"https://www.bing.com/ck/a?u={token}"
    assert unwrap_bing_href(wrapped) == target
    assert unwrap_bing_href("https://news.qq.com/a/1") == "https://news.qq.com/a/1"
    assert unwrap_bing_href("") == ""


def test_search_prefers_duckduckgo():
    ddg_hits = [{"title": "ddg", "href": "https://d.example/1", "body": "ok"}]
    with (
        patch("tools.web.search._ddgs_results", return_value=ddg_hits),
        patch("tools.web.search._bing_results") as bing,
    ):
        hits, engine = search_web_hits("python", 3)
    assert engine == "duckduckgo"
    assert hits == ddg_hits
    bing.assert_not_called()


def test_search_falls_back_to_bing_html():
    html = """
    <html><body>
      <li class="b_algo">
        <h2><a href="https://news.example/jingtian">狗仔实锤 景甜代孕</a></h2>
        <p>协议曝光</p>
      </li>
    </body></html>
    """

    class _Resp:
        text = html

        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            assert "bing.com/search" in url
            assert params["q"] == "景甜 代孕"
            assert params["cc"] == "CN"
            return _Resp()

    with (
        patch("tools.web.search._ddgs_results", side_effect=RuntimeError("blocked")),
        patch("tools.web.search.httpx.Client", _Client),
    ):
        hits, engine = search_web_hits("景甜 代孕", 5)
    assert engine == "bing"
    assert hits[0]["title"] == "狗仔实锤 景甜代孕"
    assert hits[0]["href"] == "https://news.example/jingtian"
    assert "协议" in hits[0]["body"]


def test_web_search_tool_returns_bing_sources():
    hits = [{"title": "景甜代孕", "href": "https://news.example/a", "body": "协议"}]
    with (
        patch("tools.web.search.web_search_enabled_from_config", return_value=True),
        patch("tools.web.search.search_web_hits", return_value=(hits, "bing")),
    ):
        out = web_search.invoke({"query": "景甜 代孕"})
    assert out["sources"]
    assert out["sources"][0]["document_id"] == "https://news.example/a"
    assert out["note"] == "来源：bing"


def test_web_search_tool_empty_note_includes_engines():
    with (
        patch("tools.web.search.web_search_enabled_from_config", return_value=True),
        patch(
            "tools.web.search.search_web_hits",
            return_value=([], "DuckDuckGo: timeout；Bing 无结果"),
        ),
    ):
        out = web_search.invoke({"query": "foo"})
    assert out["sources"] == []
    assert "未找到" in (out["note"] or "")
    assert "DuckDuckGo" in (out["note"] or "")
