"""FastAPI 应用：路由装配 + lifespan（业务路由见各 api/*.py 模块）。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from api import celery_app  # noqa: F401 — 确保 Celery app 与任务注册
from api.chat import close_agent_runtime, init_agent_runtime
from api.chunker import chunker_router
from api.documents import documents_router
from api.evaluation import evaluation_router
from api.knowledge_bases import router as kb_router
from api.agents import router as agents_router
from api.models import router as models_router
from api.sessions import sessions_router
from api.temporary_attachments import attachments_router
from stores.agent_repository import AgentStore
from utils.model_store import ModelStore
from utils.vector_store import ChunkStore

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 schema、模型表、LangGraph 运行时。"""
    chunk_store = ChunkStore()
    await asyncio.to_thread(chunk_store.init_schema)
    try:
        model_store = ModelStore()
        await asyncio.to_thread(model_store.init_schema)
        await asyncio.to_thread(model_store.seed_builtin_models)
    except Exception:
        pass
    try:
        agent_store = AgentStore()
        await asyncio.to_thread(agent_store.init_schema)
        await asyncio.to_thread(agent_store.seed_builtins)
    except Exception:
        pass
    from utils.temporary_attachments import ensure_temporary_attachments_table
    from utils.eval_store import ensure_eval_tables

    await asyncio.to_thread(ensure_temporary_attachments_table)
    await asyncio.to_thread(ensure_eval_tables)
    await init_agent_runtime()
    yield
    await close_agent_runtime()
    from utils.observability import flush_langfuse

    flush_langfuse()

app = FastAPI(
    title="KnowSphere API",
    description="文档上传 + 摄取触发 + 对话（嵌入式 LangGraph）",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_router)
app.include_router(attachments_router)
app.include_router(kb_router)
app.include_router(models_router)
app.include_router(agents_router)
app.include_router(evaluation_router)
app.include_router(documents_router)
app.include_router(chunker_router)

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/runtime-config")
def runtime_config() -> dict:
    """前端输入框联网/图谱开关所需的服务端能力。"""
    return {
        "web_search_available": bool(settings.web_search_enabled),
        "graph_available": bool(settings.neo4j_enable),
    }
