#!/usr/bin/env bash
# KnowSphere 一键启动：postgres + redis + minio + Celery worker + API + 前端
# 用法: bash .codebuddy/skills/knowsphere-dev/scripts/start_all.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

log() { echo -e "\033[1;34m[$1]\033[0m $2"; }

log "env" "检查 .env"
[ -f .env ] || { cp .env.example .env; log "env" "已从 .env.example 生成 .env，请填入 SILICONFLOW_API_KEY"; }

log "docker" "启动 postgres redis minio"
docker compose up -d postgres redis minio >/dev/null 2>&1
docker compose ps --format '{{.Name}} {{.Status}}' | grep -E "postgres|redis|minio"

log "db" "初始化表结构（幂等）"
uv run python -c "from utils.vector_store import ChunkStore; ChunkStore().init_schema()" >/dev/null 2>&1 && log "db" "schema ok" || { log "db" "schema 初始化失败"; exit 1; }

log "db" "摄入样例文档（需有效 key，失败可忽略）"
if [ -f data/sample/园区导览.md ]; then
  uv run python -m ingestion.ingest data/sample/园区导览.md >/dev/null 2>&1 \
    && log "db" "样例摄入 ok" || log "db" "样例摄入跳过（embedding key 无效或无网络）"
fi

log "worker" "启动 Celery worker + beat（文档异步解析，否则上传一直 pending）"
pgrep -f "celery -A api.celery_app" >/dev/null || {
  nohup uv run celery -A api.celery_app.celery worker -B --loglevel=info -Q documents \
    > /tmp/knowsphere-worker.log 2>&1 &
  echo "  pid $!"
}

log "api" "启动 FastAPI(内嵌 LangGraph) :8000"
pgrep -f "uvicorn api.main" >/dev/null || { nohup uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 > /tmp/knowsphere-api.log 2>&1 & echo "  pid $!"; }

log "front" "启动 Vite dev :5173"
pgrep -f "vite" >/dev/null || { (cd frontend && nohup npm run dev > /tmp/knowsphere-front.log 2>&1 &) ; echo "  pid $!"; }

log "wait" "等待服务就绪"
for i in $(seq 1 30); do
  ok=1
  curl -sf -o /dev/null http://localhost:8000/docs || ok=0
  curl -sf -o /dev/null http://localhost:5173/ || ok=0
  [ "$ok" = "1" ] && break
  sleep 2
done

log "done" "服务状态"
curl -s -o /dev/null -w "  API(含对话) http://localhost:8000  -> %{http_code}\n" http://localhost:8000/docs
curl -s -o /dev/null -w "  前端        http://localhost:5173  -> %{http_code}\n" http://localhost:5173/
log "done" "完成。日志: /tmp/knowsphere-api.log /tmp/knowsphere-worker.log /tmp/knowsphere-front.log"
