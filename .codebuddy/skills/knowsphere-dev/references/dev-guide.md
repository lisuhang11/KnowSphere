# KnowSphere 开发参考

## 架构速览

```
用户上传 ──► FastAPI (POST /upload) ──► 摄取: 切块(600字/15%) → bge-m3 → pgvector
用户提问 ──► FastAPI /sessions/* (api/sessions.py, graph 进程内运行) ──► StateGraph agent ──► doc_retrieval(混合检索+来源)
评测    ──► python -m evals.run_eval ──► HotpotQA + RAGAS(SiliconFlow judge)
```

- 单进程应用：LangGraph 以库方式嵌入 FastAPI（无独立 LangGraph 服务端、无 Redis）。检查点 AsyncPostgresSaver 与业务表同库；Postgres 不可用时降级 MemorySaver。
- 对话走 `/sessions/*`；前端 `frontend/src/api/sessions.ts`。
- 单租户共享：`chunks.owner` 已预留权限过滤字段（当前固定 `default`）。
- 引用来源：`doc_retrieval` 返回 `file_name#chunk_index` 来源，系统提示强制回答标注。

## 目录职责

| 目录 | 职责 |
|---|---|
| `agents/` | 单智能体组装（create_react_agent，支持传入 checkpointer） |
| `api/sessions.py` | 会话 CRUD + SSE（`/sessions`）；`api/chat.py` 仅 Agent 运行时 |
| `graph/graph.py` | 图编译导出（langgraph.json 指向，仅 Studio 调试用） |
| `models/` | 模型工厂（SiliconFlow） |
| `prompts/` | 系统提示词 |
| `tools/retrieval/doc_retrieval.py` | 混合检索工具 |
| `ingestion/` | 摄取管道（CLI + API 共用） |
| `api/main.py` | FastAPI 上传端点 + 文档/分块/预览接口 + chat 路由挂载 |
| `utils/vector_store.py` | pgvector 存储层、list_chunks |
| `utils/tokens.py` | token 估算 |
| `frontend/src/views/documents/` | 文档列表、详情抽屉、试切块抽屉 |

## 后端接口

完整命名规范见 [`docs/API_SPEC.md`](../../docs/API_SPEC.md)。下表为**当前已实现路径**（非规范目标路径）。

### 文档（api/main.py）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/documents` | 文档列表（含 chunk_count） |
| GET | `/documents/{id}/chunks?page=&page_size=` | 分页分块（单页上限 100） |
| GET | `/documents/{id}/preview` | 原文预览（仅 md/txt，其余 400） |
| POST | `/preview-chunking` | 试切块（复用 create_splitter，64K 字符限制） |
| POST | `/upload` | 上传文档并触发摄取 |

### 对话（api/sessions.py，`/sessions`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/sessions` | 创建会话（body: `{title, kb_ids}`） |
| GET | `/sessions?limit=` | 列出会话 |
| GET | `/sessions/{id}` | 会话详情 |
| PUT | `/sessions/{id}` | 更新标题 / kb_ids |
| DELETE | `/sessions/{id}` | 删除会话（元数据 + checkpoint） |
| DELETE | `/sessions/{id}/messages` | 清空消息 |
| GET | `/sessions/{id}/state` | 消息历史 |
| POST | `/sessions/{id}/runs/stream` | SSE 流式问答（body: `{message}` 或 LangGraph `input.messages`） |

前端：`frontend/src/api/sessions.ts`（经 `/api` 代理）。

## 预览功能设计约定

- 详情抽屉三视图 `viewMode: preview | merged | chunks`（`detail.vue`）。
- **合并全文**：按 `chunk_index` 顺序拼接 + 保守精确匹配去重（`overlapLen`：窗口 200 字符、≥12 字符才算重叠）。后端 chunk 无 `start_at/end_at` 位置元数据 → 不做位置重叠还原；改动切块重叠参数时无需调整该逻辑，但拼接结果可能残留极少量重复（视图内有 hint 说明）。
- 分块视图分页每页 20；合并视图全量拉取（每页 100，循环分页）。
- `ChunkPreviewDrawer.vue`：从样例/文件取样试切块，展示参数、统计（含 σ）、可折叠 chunk 卡片。

## 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| SILICONFLOW_API_KEY | - | chat+embedding 全走 SiliconFlow |
| CHAT_MODEL | Qwen/Qwen3-32B | 对话模型 |
| EMBEDDING_MODEL | BAAI/bge-m3 | 向量模型（1024 维） |
| CHUNK_SIZE / CHUNK_OVERLAP | 600 / 90 | 切块参数 |
| RETRIEVAL_TOP_K / HYBRID_LEX_WEIGHT | 6 / 0.4 | 检索参数 |
| RERANK_ENABLED / MMR_ENABLED / MULTI_QUERY_ENABLED | true | 检索增强开关 |
| POSTGRES_DSN | postgresql://knowsphere:knowsphere@localhost:5432/knowsphere | 业务库 + 对话检查点共用 |

## 常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 摄入 401 Token is invalid | `.env` key 无效 | 替换 SILICONFLOW_API_KEY 后重跑 ingest |
| `/documents/{id}/preview` 400 | 非 md/txt | 属预期，PDF 不支持原文预览 |
| 前端 5173 打不开 | vite 未起 | 查 `/tmp/knowsphere-front.log` |
| `/sessions/*` 503 agent 未初始化 | API 正在启动 | 稍等；仍失败查 API 日志 |
| 对话历史重启后丢失 | Postgres 不可用，降级 MemorySaver | 查日志中 "降级 MemorySaver"，恢复 postgres 后重启 API |
| pg 连不上 | docker 未启动 | `docker compose up -d postgres` |
| 上传后一直 pending | Celery worker 未启动 | 另开终端：`uv run celery -A api.celery_app.celery worker -B -Q documents -l info`；并确保 `docker compose up -d redis` |
