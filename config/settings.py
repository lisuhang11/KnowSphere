"""集中配置：从环境变量读取（.env），全部经 pydantic-settings 校验。"""

from __future__ import annotations

import contextvars

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 请求级 owner 隔离：contextvars 天然按线程/协程隔离，
# 评测并行跑题时各线程互不污染；产品链路不设置，回退 settings.default_owner。
_current_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_owner", default=None
)

def set_current_owner(owner: str | None) -> None:
    """设置当前执行上下文的 owner；None 表示清除。"""
    _current_owner.set(owner)

def get_current_owner() -> str | None:
    """读取当前上下文的 owner；未设置返回 None（调用方回退 default_owner）。"""
    return _current_owner.get()

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # SiliconFlow
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    # 模型选择
    chat_provider: str = "siliconflow"
    # 默认 chat 模型：Qwen3.5-35B-A3B 工具调用稳定；Qwen3-32B 绑定工具时偶尔不触发（幻觉回答）
    chat_model: str = "Qwen/Qwen3.5-35B-A3B"
    embedding_provider: str = "siliconflow"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024  # bge-m3 输出维度

    # 存储
    postgres_dsn: str = "postgresql://knowsphere:knowsphere@localhost:5432/knowsphere"
    # 历史本地上传目录（仅存量文档无 MinIO key 时回退读取，新上传不再写入）
    upload_dir: str = "data/uploads"

    # 对象存储（MinIO）：原始文档 + 解析图片（上传必需 MINIO_ENDPOINT）
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "knowsphere"
    minio_secure: bool = False

    # 文档解析（Q1 内嵌引擎）：OCR 开关与解析引擎
    ocr_enabled: bool = True  # 扫描 PDF / 图片直传时是否走 PaddleOCR
    parse_engine: str = "builtin"  # builtin | markitdown

    # 异步任务（Celery + Redis 队列）
    redis_url: str = "redis://localhost:6379/0"
    # 处理中超时（分钟）：超过即被 housekeeping 兜底置 failed，可手动重试
    processing_timeout_minutes: int = 30
    # 文档摄取时每批 embedding 的 chunk 数
    embedding_batch_size: int = 32

    # 切块与检索
    chunk_size: int = 600
    chunk_overlap: int = 90  # 15%
    enable_parent_child: bool = False
    parent_chunk_size: int = 4096
    child_chunk_size: int = 384
    retrieval_top_k: int = 6
    hybrid_lex_weight: float = 0.4  # RRF 融合时词法路权重保留位（当前 RRF 不使用）
    default_owner: str = "default"  # 单租户共享，二期权限用

    # 重排（SiliconFlow /v1/rerank）
    rerank_provider: str = "siliconflow"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_enabled: bool = True  # 评测对比可置 false 走纯 RRF
    retrieval_candidate_k: int = 30  # 进 rerank 的召回候选池大小

    # MMR 多样性选择（rerank 之后、返回之前）
    mmr_enabled: bool = True  # 关闭则直接取精排 top_k
    mmr_lambda: float = 0.7  # 相关性/多样性权衡：越大越偏相关性
    # 冗余度混合权重：0=纯 embedding 余弦（语义级去重）；(0,1] 时按该比例叠加
    # 词面 Jaccard（2-gram token 重叠，词面去重维度）
    mmr_jaccard_weight: float = 0.0

    # Query understanding
    enable_rewrite: bool = True
    max_rewrite_rounds: int = 5
    query_understand_model: str = ""  # 空 = 复用 chat_model

    # 会话图片
    chat_images_enabled: bool = True
    chat_vlm_model_id: str = ""  # 空 = 使用 models 表默认 VLLM
    vlm_model: str = ""  # 可选：与 chat_model 类似，启动时 seed 为内置 VLLM 记录
    chat_attachment_ttl_hours: int = 24
    chat_attachment_wait_sec: int = 60

    # 本地 query expansion：首次召回不足时并行补搜（无 LLM）
    query_expansion_enabled: bool = True
    query_expansion_max_variants: int = 5

    # Multi-Query LLM 子查询：首轮单路召回不足（< 扩展阈值）时再触发，额外 1 次 LLM
    multi_query_enabled: bool = False
    multi_query_count: int = 3  # LLM 生成的子查询数（不含原始 query 本身）

    # 引用协议：强制模型用 [[cN]] 句柄引用检索结果，
    # 后端展开为可点击角标；关闭则自由输出、不展开、不发 citation_meta
    citation_enabled: bool = True

    # LangGraph agent 最大步数（recursion_limit，含 agent/tools 往返）
    agent_max_steps: int = 25

    # 知识图谱（Neo4j；关闭时抽取/检索全部 no-op）
    neo4j_enable: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password"

    # 模型管理（models 表）：api_key 加密主密钥（AES-256-GCM）
    # 生产必须设置 MASTER_KEY；未设置（保持默认值）时降级为可逆 base64 仅限开发。
    # 变更 MASTER_KEY 后需运行: MASTER_KEY_NEW=<新密钥> python -m scripts.reencrypt_models
    model_master_key: str = Field(
        default="knowsphere-dev-master-key-change-me",
        validation_alias="MASTER_KEY",
    )

settings = Settings()
