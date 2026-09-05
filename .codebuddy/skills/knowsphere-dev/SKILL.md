---
name: knowsphere-dev
description: KnowSphere 项目本地开发技能。用于启动/停止整套服务（pgvector+FastAPI(内嵌 LangGraph)+前端）、初始化数据库、摄入文档、构建前端、排查服务问题。当用户要求"启动服务""跑起来""开发调试 KnowSphere"或遇到 API/前端/对话服务相关问题时使用。
---

# KnowSphere Dev

## Overview

KnowSphere 是基于 LangGraph + FastAPI + Vue3(TDesign) 的 BYOD 知识问答项目。本技能提供整套服务的一键启动/停止、数据初始化、构建与常见开发操作的准确步骤。

**架构要点**：LangGraph graph 以"库"方式嵌入 FastAPI 进程内运行（`api/chat.py` 编译 graph），对话走 `/sessions/*`（`api/sessions.py`），检查点用 AsyncPostgresSaver 持久化到 Postgres。

## 服务拓扑与端口

| 服务 | 端口 | 启动方式 |
|---|---|---|
| postgres (pgvector) | 5432 | `docker compose up -d postgres` |
| FastAPI（上传+摄取+对话） | 8000 | `uv run uvicorn api.main:app --host 0.0.0.0 --port 8000` |
| 前端 Vite dev | 5173 | `npm run dev`（在 frontend/） |

前端代理：`/api` → 8000（rewrite 去前缀）。见 `frontend/vite.config.ts`。

## 快速启动（推荐）

直接执行一键脚本：

```bash
bash .codebuddy/skills/knowsphere-dev/scripts/start_all.sh
```

脚本会按序：检查依赖 → 启动 postgres → 初始化表结构 → 摄入样例文档（如未摄入且 key 有效）→ 后台启动 API / 前端，并打印端口就绪状态。

## 手动启动步骤

### 1. 环境准备（首次）

```bash
cp .env.example .env        # 填入 SILICONFLOW_API_KEY；可选 LANGFUSE_*
uv sync                     # 若需 pytest/ruff 等开发工具再 --extra dev
cd frontend && npm install
```

注意：无有效 `SILICONFLOW_API_KEY` 时，摄入文档/对话/embedding 会 401 失败，但其余服务可正常启动。

### 2. 基础服务 + 初始化

```bash
docker compose up -d postgres
uv run python -c "from utils.vector_store import ChunkStore; ChunkStore().init_schema()"
uv run python -m ingestion.ingest data/sample/园区导览.md   # 摄入样例
```

ks_threads 表与 LangGraph checkpoint 表由 API 启动时自动创建（幂等），无需手动初始化。

### 3. 启动两个应用进程（均后台运行）

```bash
cd /workspace && nohup uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 > /tmp/knowsphere-api.log 2>&1 &
cd /workspace/frontend && nohup npm run dev > /tmp/knowsphere-front.log 2>&1 &
```

日志均落 `/tmp/knowsphere-*.log`，排查问题先看对应日志。

### 4. 验证就绪

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/docs   # 期望 200
curl -s http://localhost:8000/sessions?limit=10   # 期望 [] 或会话列表
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/       # 期望 200
```

## 常用操作

### 构建前端（修改 .vue/.ts 后验证）

```bash
cd frontend && npm run build
```

### 停止服务

```bash
pkill -f "uvicorn api.main" ; pkill -f "vite"
docker compose stop
```

### 上传文档（API）

```bash
curl -X POST http://localhost:8000/upload -F "file=@data/sample/园区导览.md"
```

### 运行评测

```bash
uv run python -m evals.run_eval --n 20          # 默认 n=50
```

## 关键实现背景（改动时必读）

- **对话运行时**（`api/chat.py` 编译 graph，`api/sessions.py` 暴露 `/sessions/*`）：lifespan 中初始化 AsyncPostgresSaver（Postgres 不可用降级 MemorySaver）。流式问答 `POST /sessions/{id}/runs/stream`，前端 `api/sessions.ts`。客户端断开即终止本次运行。
- **文档详情抽屉**（`frontend/src/views/documents/detail.vue`）：三视图（原文预览/合并全文/分块视图）。合并全文按 `chunk_index` 顺序拼接，用保守精确匹配去重（`overlapLen`，窗口 200、阈值 12），因后端 chunk 无 `start_at/end_at` 位置元数据，无法精确还原重叠。改切块逻辑时注意保持此兼容。
- **试切块抽屉**（`ChunkPreviewDrawer.vue`）复用后端 `POST /preview-chunking` 与 `create_splitter()`，入参来自配置 `CHUNK_SIZE/CHUNK_OVERLAP`。
- **检索**：混合检索（向量 + pg_trgm 词法）+ rerank + MMR + multi-query，配置见 `.env`。
- 更完整的架构与命令见 `references/dev-guide.md`。

## 注意事项

- `.codebuddy/` 目录存放项目数据，不要删除。
- `langgraph.json` 与 `graph/graph.py` 仍保留：需要 LangGraph Studio 图调试时可选 `uv run langgraph dev`（须先停掉 8000 端口冲突无，二者独立端口，仅注意 checkpoint 不同存储），但日常开发不需要。
- 修改 `pyproject.toml` 依赖后执行 `uv sync`；无网络时可用 `uv sync --offline`。
