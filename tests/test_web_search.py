"""联网搜索：HTML 解析、问句改写、引擎回退（不依赖外网）。"""

from __future__ import annotations

from unittest.mock import patch

from tools.web.search import (
    clean_ddg_url,
    parse_bing_html,
    parse_ddg_api,
    parse_ddg_html,
    search_web_hits,
    unwrap_bing_href,
    web_query_variants,
    web_search,
)

_BING_HTML = """
<html><body>
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://baike.baidu.com/item/%E6%99%AF%E7%94%9C">景甜_百度百科</a></h2>
    <div class="b_caption"><p>景甜，1988年7月21日出生于陕西省西安市。</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="/ck/a?u=a1aHR0cHM6Ly9uZXdzLnFxLmNvbS9qaW5ndGlhbg&amp;ntb=1">景甜近况曝光</a></h2>
    <p>2026年5月，景甜相关新闻。</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://www.bing.com/search?q=spam">站内搜索</a></h2>
    <p>应被过滤</p>
  </li>
</ol>
</body></html>
"""

_DDG_HTML = """
<html><body>
<div class="web-result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpython&amp;rut=xx">Python 3.12</a>
  <a class="result__snippet">Python 3.12 release notes.</a>
</div>
<div class="result">
  <a class="result__a" href="https://docs.python.org/3/">Python docs</a>
  <span class="result__snippet">Official documentation.</span>
</div>
</body></html>
"""


def test_unwrap_bing_href_decodes_ck_token():
    raw = "https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9wYXRo&ntb=1"
    assert unwrap_bing_href(raw) == "https://example.com/path"
    assert unwrap_bing_href("https://news.qq.com/a") == "https://news.qq.com/a"


def test_clean_ddg_url_uddg():
    href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=abc"
    assert clean_ddg_url(href) == "https://example.com/a"
    assert clean_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fex.com") == "https://ex.com"


def test_parse_bing_html_skips_internal_and_unwraps():
    hits = parse_bing_html(_BING_HTML, 10, base_url="https://cn.bing.com")
    urls = [h["href"] for h in hits]
    assert "https://baike.baidu.com/item/%E6%99%AF%E7%94%9C" in urls
    assert any(u.startswith("https://") and "bing.com/search" not in u for u in urls)
    assert all("bing.com/search" not in u for u in urls)
    assert any("news.qq.com" in u or "jingtian" in u for u in urls)


def test_parse_ddg_html_cleans_redirect():
    hits = parse_ddg_html(_DDG_HTML, 5)
    assert hits[0]["href"] == "https://example.com/python"
    assert hits[0]["title"] == "Python 3.12"
    assert hits[1]["href"] == "https://docs.python.org/3/"


def test_parse_ddg_api_abstract_and_related():
    payload = {
        "Heading": "Python",
        "AbstractText": "A programming language.",
        "AbstractURL": "https://www.python.org/",
        "RelatedTopics": [
            {"Text": "Python Software Foundation - org", "FirstURL": "https://www.python.org/psf/"},
            {"Topics": [{"Text": "Tutorial", "FirstURL": "https://docs.python.org/3/tutorial/"}]},
        ],
    }
    hits = parse_ddg_api(payload, 5)
    assert hits[0]["href"] == "https://www.python.org/"
    assert any("tutorial" in h["href"] for h in hits)


def test_web_query_variants_moves_recency_after_entity():
    variants = web_query_variants("最近比较火的景甜")
    assert any(v.startswith("景甜") for v in variants)
    assert "景甜 最近" in variants
    assert "景甜 热搜" in variants
    assert "最近比较火的景甜" in variants
    assert web_query_variants("Python 3.12") == ["Python 3.12"]


def test_web_query_variants_weibo_hot_topic_uses_entity_hot_search():
    q = "微博最近关于景甜比较火的话题是啥"
    variants = web_query_variants(q)
    assert variants[0] == "景甜 热搜"
    assert "景甜 新闻" in variants
    assert q in variants
    from tools.web.search import _content_tokens

    assert _content_tokens(q) == ["景甜"]


def test_junk_weibo_login_filtered():
    from tools.web.search import _hit

    assert _hit("Sina Visitor System - 微博", "https://weibo.com/login.php/", "图片无法显示") is None
    assert _hit("登录 - 微博", "https://passport.weibo.com/sso/signin", "预览") is None
    assert _hit("微博", "https://www.weibo.com/", "随时随地发现新鲜事") is None
    keep = _hit("景甜登上热搜", "https://news.qq.com/a/jingtian", "景甜 热搜")
    assert keep is not None
    assert _hit("相对路径", "/ck/a?u=foo", "摘要") is None
    assert _hit("无主机", "https://", "摘要") is None


def test_search_web_hits_bing_first_skips_ddg(monkeypatch):
    calls: list[str] = []

    def fake_bing(query: str, max_results: int):
        calls.append(f"bing:{query}")
        return [
            {
                "title": "景甜近况",
                "href": "https://news.example.com/jt",
                "body": "景甜 2026 年新闻",
            }
        ]

    def boom(*_a, **_k):
        raise AssertionError("DDG should not run when Bing already has hits")

    monkeypatch.setattr("tools.web.search._bing_results", fake_bing)
    monkeypatch.setattr("tools.web.search._ddg_html_results", boom)
    monkeypatch.setattr("tools.web.search._ddgs_lib_results", boom)

    hits, engine = search_web_hits("最近比较火的景甜", 5)
    assert engine == "bing"
    assert hits and "景甜" in hits[0]["title"]
    assert calls[0].startswith("bing:景甜")


def test_search_web_hits_falls_back_to_ddg_html(monkeypatch):
    monkeypatch.setattr("tools.web.search._bing_results", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "tools.web.search._ddg_html_results",
        lambda q, k: [{"title": "Py", "href": "https://python.org", "body": "docs"}],
    )
    monkeypatch.setattr("tools.web.search._ddg_api_results", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    hits, engine = search_web_hits("Python 3.12", 3)
    assert engine == "duckduckgo-html"
    assert hits[0]["href"] == "https://python.org"


def test_web_search_disabled_returns_note():
    with patch("tools.web.search.web_search_enabled_from_config", return_value=False):
        out = web_search.invoke({"query": "今天天气"})
    assert out["sources"] == []
    assert "未开启联网搜索" in (out.get("note") or "")


def test_web_search_formats_urls_in_note(monkeypatch):
    monkeypatch.setattr(
        "tools.web.search.search_web_hits",
        lambda q, k: (
            [{"title": "Python.org", "href": "https://www.python.org/", "body": "Welcome"}],
            "bing",
        ),
    )
    with patch("tools.web.search.web_search_enabled_from_config", return_value=True):
        out = web_search.invoke({"query": "Python 3.12"})
    note = out.get("note") or ""
    assert "https://www.python.org/" in note
    assert out["sources"][0]["document_id"] == "https://www.python.org/"
    assert out["sources"][0]["file_name"] == "Python.org"
