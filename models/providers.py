"""模型服务商目录（对齐 WeKnora）。

DB 语义：
- models.source: ``local`` | ``remote``（部署位置）
- parameters.provider: 厂商标识（siliconflow / aliyun / generic / ollama …）

本地固定走 Ollama，不出现在远程厂商列表里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_TYPES = ("KnowledgeQA", "Embedding", "Rerank", "VLLM", "ASR")
MODEL_SOURCES = ("local", "remote")

# 旧版把厂商写在 source 列；启动时迁到 parameters.provider
_LEGACY_SOURCE_TO_PROVIDER = {
    "siliconflow": "siliconflow",
    "openai_compatible": "generic",
    "generic": "generic",
    "openai": "openai",
    "aliyun": "aliyun",
    "zhipu": "zhipu",
    "deepseek": "deepseek",
    "moonshot": "moonshot",
    "volcengine": "volcengine",
    "hunyuan": "hunyuan",
    "qianfan": "qianfan",
    "openrouter": "openrouter",
    "jina": "jina",
    "ollama": "ollama",
}

PROVIDER_ALIASES = {
    "openai_compatible": "generic",
}


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    name: str
    description: str
    types: tuple[str, ...]
    default_urls: dict[str, str]
    requires_auth: bool = True
    kind: str = "remote"  # remote | local
    rerank_style: str = "openai"  # openai | aliyun | none


_CHAT_VLLM = ("KnowledgeQA", "VLLM")
_CHAT_EMB_VLLM = ("KnowledgeQA", "Embedding", "VLLM")
_FULL = ("KnowledgeQA", "Embedding", "Rerank", "VLLM", "ASR")
_QA_EMB_RR_VLLM = ("KnowledgeQA", "Embedding", "Rerank", "VLLM")

PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="generic",
        name="自定义 (OpenAI 兼容接口)",
        description="任意 OpenAI 兼容服务（vLLM、OneAPI、Azure 兼容网关等），自填 Base URL",
        types=_FULL,
        default_urls={},
        requires_auth=False,
    ),
    ProviderSpec(
        id="siliconflow",
        name="硅基流动 SiliconFlow",
        description="Qwen、DeepSeek、BGE 等；API 兼容 OpenAI 格式",
        types=_FULL,
        default_urls={t: "https://api.siliconflow.cn/v1" for t in _FULL},
    ),
    ProviderSpec(
        id="openai",
        name="OpenAI",
        description="gpt-4o、text-embedding-3 等",
        types=_FULL,
        default_urls={t: "https://api.openai.com/v1" for t in _FULL},
    ),
    ProviderSpec(
        id="aliyun",
        name="阿里云 DashScope",
        description="qwen-plus、text-embedding-v3、gte-rerank 等",
        types=_QA_EMB_RR_VLLM,
        default_urls={
            "KnowledgeQA": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "Embedding": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "VLLM": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "Rerank": "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        },
        rerank_style="aliyun",
    ),
    ProviderSpec(
        id="zhipu",
        name="智谱 BigModel",
        description="glm-4、embedding-3、rerank 等",
        types=_QA_EMB_RR_VLLM,
        default_urls={
            "KnowledgeQA": "https://open.bigmodel.cn/api/paas/v4",
            "Embedding": "https://open.bigmodel.cn/api/paas/v4",
            "VLLM": "https://open.bigmodel.cn/api/paas/v4",
            "Rerank": "https://open.bigmodel.cn/api/paas/v4/rerank",
        },
    ),
    ProviderSpec(
        id="deepseek",
        name="DeepSeek",
        description="deepseek-chat、deepseek-reasoner",
        types=("KnowledgeQA",),
        default_urls={"KnowledgeQA": "https://api.deepseek.com/v1"},
        rerank_style="none",
    ),
    ProviderSpec(
        id="moonshot",
        name="月之暗面 Moonshot",
        description="kimi-k2、moonshot-v1-vision 等",
        types=_CHAT_VLLM,
        default_urls={t: "https://api.moonshot.ai/v1" for t in _CHAT_VLLM},
        rerank_style="none",
    ),
    ProviderSpec(
        id="volcengine",
        name="火山引擎 Volcengine",
        description="豆包 Doubao chat / embedding",
        types=_CHAT_EMB_VLLM,
        default_urls={t: "https://ark.cn-beijing.volces.com/api/v3" for t in _CHAT_EMB_VLLM},
        rerank_style="none",
    ),
    ProviderSpec(
        id="hunyuan",
        name="腾讯混元 Hunyuan",
        description="hunyuan-pro、hunyuan-embedding 等",
        types=("KnowledgeQA", "Embedding"),
        default_urls={
            "KnowledgeQA": "https://api.hunyuan.cloud.tencent.com/v1",
            "Embedding": "https://api.hunyuan.cloud.tencent.com/v1",
        },
        rerank_style="none",
    ),
    ProviderSpec(
        id="qianfan",
        name="百度千帆",
        description="ERNIE、embedding-v1、bce-reranker 等",
        types=_QA_EMB_RR_VLLM,
        default_urls={t: "https://qianfan.baidubce.com/v2" for t in _QA_EMB_RR_VLLM},
    ),
    ProviderSpec(
        id="openrouter",
        name="OpenRouter",
        description="聚合多家模型的路由网关",
        types=_CHAT_EMB_VLLM,
        default_urls={t: "https://openrouter.ai/api/v1" for t in _CHAT_EMB_VLLM},
        rerank_style="none",
    ),
    ProviderSpec(
        id="jina",
        name="Jina",
        description="jina-embeddings、jina-reranker",
        types=("Embedding", "Rerank"),
        default_urls={
            "Embedding": "https://api.jina.ai/v1",
            "Rerank": "https://api.jina.ai/v1",
        },
    ),
    ProviderSpec(
        id="ollama",
        name="Ollama（本地）",
        description="本机或内网 Ollama，走 OpenAI 兼容 /v1",
        types=("KnowledgeQA", "Embedding", "VLLM"),
        default_urls={},
        requires_auth=False,
        kind="local",
        rerank_style="none",
    ),
)

_BY_ID: dict[str, ProviderSpec] = {p.id: p for p in PROVIDERS}


def normalize_provider(raw: str | None) -> str:
    """把旧别名（openai_compatible）收成目录 id。"""
    name = (raw or "").strip()
    return PROVIDER_ALIASES.get(name, name)


def get_provider(provider_id: str | None) -> ProviderSpec | None:
    return _BY_ID.get(normalize_provider(provider_id))


def list_remote_providers(model_type: str | None = None) -> list[ProviderSpec]:
    """远程厂商目录；Ollama 走 source=local，不出现在此列表。"""
    out: list[ProviderSpec] = []
    for spec in PROVIDERS:
        if spec.kind != "remote":
            continue
        if model_type and model_type not in spec.types:
            continue
        out.append(spec)
    return out


def default_base_url(provider_id: str | None, model_type: str) -> str:
    spec = get_provider(provider_id)
    if spec is None:
        return ""
    if spec.default_urls.get(model_type):
        return spec.default_urls[model_type]
    if spec.default_urls:
        return next(iter(spec.default_urls.values()))
    return ""


def runtime_provider(source: str | None, parameters: dict[str, Any] | None) -> str:
    """运行时厂商：local 固定 ollama；remote 读 parameters.provider。"""
    params = parameters or {}
    if (source or "").strip() == "local":
        return "ollama"
    stored = normalize_provider(params.get("provider") if isinstance(params.get("provider"), str) else None)
    if stored:
        return stored
    # 未迁完的旧行：source 仍是厂商名
    legacy = _LEGACY_SOURCE_TO_PROVIDER.get((source or "").strip())
    if legacy:
        return legacy
    return "generic"


def provider_supports_type(provider_id: str | None, model_type: str) -> bool:
    spec = get_provider(provider_id)
    return bool(spec and model_type in spec.types)


def spec_to_public(spec: ProviderSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "types": list(spec.types),
        "default_urls": dict(spec.default_urls),
        "requires_auth": spec.requires_auth,
        "kind": spec.kind,
        # 兼容旧前端字段
        "source": spec.id,
        "base_url": spec.default_urls.get("KnowledgeQA") or next(iter(spec.default_urls.values()), None),
    }
