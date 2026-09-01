# KnowSphere

基于 **LangGraph + LangSmith** 的 BYOD（Bring Your Own Document）知识问答助手。

用户上传 PDF / Markdown / TXT 文档 → 自动切块向量化入库 → 单智能体 ReAct 检索回答（带来源引用）。全链路 LangSmith tracing / 监控 / 数据集评测。

## 架构

```
用户上传 ──► FastAPI (POST /upload) ──► 摄取: 切块(600字/15%) → bge-m3 向量化 → pgvector
                                                      │ 全程 @traceable
用户提问 ──► FastAPI /sessions/* (api/sessions.py) ──► LangGraph graph（进程内运行）──► doc_retrieval(混合检索+来源)
评测    ──► python -m evals.run_eval ──► HotpotQA 抽样 + RAGAS 四指标（SiliconFlow judge）
```

> LangGraph 以"库"方式嵌入 FastAPI 进程运行（`api/chat.py` 编译 graph，`api/sessions.py` 暴露 `/sessions`）。对话检查点通过 AsyncPostgresSaver 持久化到 Postgres。

```
KnowSphere/
├── langgraph.json        # Studio 调试入口（可选）
├── pyproject.toml        # uv 依赖
├── docker-compose.yml    # pgvector + api（graph 内嵌）
├── agents/               # 单智能体组装
├── graph/                # 图编译导出（langgraph.json 指向这里，仅调试用）
├── models/               # 大模型工厂（SiliconFlow 实现）
├── prompts/              # 提示词（git 管理，不上 Hub）
├── states/               # 状态/配置类型
├── schemas/              # Pydantic schema（来源引用等）
├── tools/                # doc_retrieval 检索工具
├── ingestion/            # 摄取管道（CLI + API 共用）
├── api/                  # 上传端点 + 嵌入式对话路由（api/chat.py）
├── evals/                # RAGAS + HotpotQA 评测
├── config/               # 环境配置
└── utils/                # pgvector 存储层等
```

## 快速开始

### 1. 依赖与配置

```bash
uv sync                      # 或 pip install -e ".[dev]"
cp .env.example .env         # 填入 SILICONFLOW_API_KEY / LANGSMITH_API_KEY
```

模型默认走 SiliconFlow：chat `Qwen/Qwen3.5-35B-A3B`、embedding `BAAI/bge-m3`（模型 ID 可在 `.env` 覆盖，以 SiliconFlow 控制台为准）。

### 2. 启动基础服务

```bash
docker compose up -d postgres redis minio   # 上传文档依赖 MinIO
```

### 3. 初始化表结构并摄入样例文档

```bash
python -c "from utils.vector_store import ChunkStore; ChunkStore.init_schema"
python -m ingestion.ingest data/sample/园区导览.md
```

### 4. 启动后端（含嵌入式 LangGraph 对话）

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

graph 在 FastAPI 进程内运行（`api/chat.py`），启动时自动创建会话表与 checkpoint 表；Postgres 不可用时自动降级内存模式（重启丢对话历史）。

### 5. 启动前端

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

### 6. 上传文档（API 方式）

```bash
curl -X POST http://localhost:8000/upload -F "file=@data/sample/园区导览.md"
# → {"document_id":"...","file_name":"...","chunk_count":N}
```

对话走 FastAPI 的 `/sessions/*` 路由；需要 LangGraph Studio 时可选 `langgraph dev`（仅调试）。

## 评测（RAGAS + HotpotQA）

```bash
python -m evals.run_eval                      # 默认 validation 抽 50 题，seed=42
python -m evals.run_eval --n 100 --seed 7     # 自定义抽样
```

- 数据：HuggingFace `hotpot_qa`（distractor，多跳推理），每题 10 段（2 金标 + 8 干扰）按 `chunks.owner` 隔离摄取，跑完自动清理
- 指标：`faithfulness` / `answer_relevancy` / `context_precision` / `context_recall`
- judge / embedding 走 SiliconFlow（Qwen3.5-35B-A3B / bge-m3），非 RAGAS 默认 OpenAI；已关闭 thinking 保证结构化输出
- 评测 agent 只挂 `doc_retrieval`、英文作答（产品中文提示词不动；`answer_relevancy` 反向生成依赖同语种 embedding）
- 输出：控制台指标均值 + `data/ragas_report.csv` 逐题明细
- 网络：国内默认兜底 `HF_ENDPOINT=https://hf-mirror.com`（已设官方源则不覆盖）
- 注意：HotpotQA 为英文段落直接入库，不覆盖 PDF 解析 / 中文切块链路——该盲区已知并接受

## 关键设计说明

- **混合检索**：向量余弦 + pg_trgm 词法相似度加权（`HYBRID_LEX_WEIGHT`）。中文场景 pg_trgm 免装分词扩展；数据量大后可换 pg_jieba 或 Qdrant 稀疏向量。
- **单租户共享**：`chunks.owner` 字段已预留，二期加权限只需检索时过滤 owner。
- **引用来源**：doc_retrieval 返回带 `file_name#chunk_index` 的来源，系统提示强制回答时标注。
- **模型工厂**：`models/` 按 WeKnora 语义区分 `source=local|remote` 与 `parameters.provider`（硅基流动 / OpenAI / 阿里云 / 智谱 / DeepSeek / Kimi / 火山 / 混元 / 千帆 / OpenRouter / Jina / 自定义兼容口）；本地走 Ollama。新增远程厂商在 `models/providers.py` 登记即可。

## 模型管理

- **入口**：前端侧边栏「模型管理」；后端 `GET/POST /models`、`GET/PUT/DELETE /models/{id}`、`POST /models/{id}/debug`（测试连接）、`PUT /models/{id}/credentials`（凭证子资源，读接口永不回显密钥）、`GET /models/providers`。
- **类型**：`Embedding` / `Rerank` / `KnowledgeQA` / `VLLM` / `ASR`。
- **来源**：`source=remote` 配远程厂商；`source=local` 固定 Ollama（OpenAI 兼容 `/v1`，Rerank 不可用）。启动时会把旧行 `source=siliconflow|openai_compatible` 迁成 `remote` + `parameters.provider`。
- **存储**：`models` 表（`parameters` JSONB），api_key 用 AES-256-GCM 加密（`MASTER_KEY` 环境变量，未设置时降级为可逆 base64 仅限开发）。
- **内置种子**：启动时自动把 `.env` 的 chat/embedding/rerank 模型注册为 `is_builtin` 记录（幂等），并把存量知识库 `embedding_model_id` 的裸模型名迁移为模型 ID。
- **运行时解析顺序**：显式模型 ID → models 表 `is_default`（每类型一个）→ `.env` 兜底；裸模型名直接使用（兼容旧数据）。
- **删除保护**：内置模型、默认模型、被知识库引用的模型不可删除。
- **密钥轮换**：`MASTER_KEY_NEW=<新密钥> python -m scripts.reencrypt_models` 后更新 `.env` 并重启。

## 已知事项

- SiliconFlow 模型 ID 以控制台为准，`CHAT_MODEL` / `EMBEDDING_MODEL` 随时可换。
- 全局 embedding 默认 `bge-m3`（1024 维）；创建知识库时可给每个库单独指定 embedding 模型，非 1024 维度会自动加 `embedding_{dim}` 向量列与 HNSW 索引（pgvector 上限 2000 维，超限会拒绝创建）。
