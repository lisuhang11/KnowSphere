"""Rerank 适配：URL 归一化与阿里云请求体。"""

from unittest.mock import MagicMock, patch

from models.rerank import AliyunReranker, OpenAICompatReranker, rerank_endpoint


def test_rerank_endpoint_appends_or_keeps():
    assert rerank_endpoint("https://api.siliconflow.cn/v1") == "https://api.siliconflow.cn/v1/rerank"
    assert rerank_endpoint("https://api.jina.ai/v1/") == "https://api.jina.ai/v1/rerank"
    assert (
        rerank_endpoint("https://open.bigmodel.cn/api/paas/v4/rerank")
        == "https://open.bigmodel.cn/api/paas/v4/rerank"
    )
    assert "text-rerank" in rerank_endpoint(
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    )


def test_openai_compat_rerank_posts_cohere_body():
    rr = OpenAICompatReranker(
        model="bge-reranker-v2-m3",
        api_key="sk-test",
        base_url="https://api.siliconflow.cn/v1",
        provider="siliconflow",
    )
    fake = MagicMock()
    fake.json.return_value = {"results": [{"index": 1, "relevance_score": 0.9}]}
    fake.raise_for_status = MagicMock()
    with patch("models.rerank.httpx.post", return_value=fake) as post:
        out = rr.rerank("q", ["a", "b"], top_n=1)
    assert out == [{"index": 1, "relevance_score": 0.9}]
    args, kwargs = post.call_args
    assert args[0] == "https://api.siliconflow.cn/v1/rerank"
    assert kwargs["json"]["query"] == "q"
    assert kwargs["json"]["documents"] == ["a", "b"]


def test_aliyun_rerank_posts_dashscope_body():
    rr = AliyunReranker(model="gte-rerank", api_key="sk-test")
    fake = MagicMock()
    fake.json.return_value = {
        "output": {"results": [{"index": 0, "relevance_score": 0.5}]}
    }
    fake.raise_for_status = MagicMock()
    with patch("models.rerank.httpx.post", return_value=fake) as post:
        out = rr.rerank("q", ["doc"])
    assert out[0]["index"] == 0
    body = post.call_args.kwargs["json"]
    assert "input" in body
    assert body["input"]["query"] == "q"
    assert body["parameters"]["top_n"] == 1
