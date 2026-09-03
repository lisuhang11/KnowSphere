"""评测任务持久化（Postgres）。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from config.settings import settings
from evals.schemas import EvalConfig, TaskStatus

def ensure_eval_tables() -> None:
    with psycopg.connect(settings.postgres_dsn) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ks_evaluation_tasks (
                id              TEXT PRIMARY KEY,
                owner           TEXT NOT NULL,
                dataset_id      TEXT NOT NULL,
                suite           TEXT NOT NULL,
                pipeline_profile TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                config_snapshot JSONB NOT NULL DEFAULT '{}',
                metric_summary  JSONB,
                total           INT NOT NULL DEFAULT 0,
                finished        INT NOT NULL DEFAULT 0,
                err_msg         TEXT,
                eval_kb_id      BIGINT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                started_at      TIMESTAMPTZ,
                finished_at     TIMESTAMPTZ
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ks_evaluation_samples (
                id              BIGSERIAL PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES ks_evaluation_tasks(id) ON DELETE CASCADE,
                qid             INT NOT NULL,
                question        TEXT NOT NULL,
                reference       TEXT,
                response        TEXT,
                retrieval_ids   JSONB,
                retrieval_gt    JSONB,
                metrics         JSONB,
                latency_ms      INT,
                error           TEXT,
                UNIQUE (task_id, qid)
            )
            """
        )
        conn.commit()

def new_task_id(dataset_id: str, owner: str) -> str:
    return f"evaluation-{owner}-{dataset_id}-{uuid.uuid4().hex[:8]}"

class EvalStore():
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.postgres_dsn

    def create_task(self, config: EvalConfig) -> dict[str, Any]:
        ensure_eval_tables()
        task_id = new_task_id(config.dataset_id, config.owner)
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """
                INSERT INTO ks_evaluation_tasks
                    (id, owner, dataset_id, suite, pipeline_profile, status, config_snapshot)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id, owner, dataset_id, suite, pipeline_profile, status,
                          config_snapshot, metric_summary, total, finished, err_msg,
                          eval_kb_id, created_at, started_at, finished_at
                """,
                (
                    task_id,
                    config.owner,
                    config.dataset_id,
                    config.suite,
                    config.pipeline_profile,
                    Jsonb(config.snapshot()),
                ),
            ).fetchone()
            conn.commit()
        return _row_to_task(row)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        ensure_eval_tables()
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                """
                SELECT id, owner, dataset_id, suite, pipeline_profile, status,
                       config_snapshot, metric_summary, total, finished, err_msg,
                       eval_kb_id, created_at, started_at, finished_at
                FROM ks_evaluation_tasks WHERE id = %s
                """,
                (task_id,),
            ).fetchone()
        return _row_to_task(row) if row else None

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        metric_summary: dict | None = None,
        total: int | None = None,
        finished: int | None = None,
        err_msg: str | None = None,
        eval_kb_id: int | None = None,
        started: bool = False,
        finished_at: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = %s")
            params.append(status)
        if metric_summary is not None:
            sets.append("metric_summary = %s")
            params.append(Jsonb(metric_summary))
        if total is not None:
            sets.append("total = %s")
            params.append(total)
        if finished is not None:
            sets.append("finished = %s")
            params.append(finished)
        if err_msg is not None:
            sets.append("err_msg = %s")
            params.append(err_msg)
        if eval_kb_id is not None:
            sets.append("eval_kb_id = %s")
            params.append(eval_kb_id)
        if started:
            sets.append("started_at = %s")
            params.append(datetime.now(timezone.utc))
        if finished_at:
            sets.append("finished_at = %s")
            params.append(datetime.now(timezone.utc))
        if not sets:
            return
        params.append(task_id)
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                f"UPDATE ks_evaluation_tasks SET {', '.join(sets)} WHERE id = %s",
                params,
            )
            conn.commit()

    def upsert_sample(self, task_id: str, row: dict[str, Any]) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO ks_evaluation_samples
                    (task_id, qid, question, reference, response,
                     retrieval_ids, retrieval_gt, metrics, latency_ms, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (task_id, qid) DO UPDATE SET
                    question = EXCLUDED.question,
                    reference = EXCLUDED.reference,
                    response = EXCLUDED.response,
                    retrieval_ids = EXCLUDED.retrieval_ids,
                    retrieval_gt = EXCLUDED.retrieval_gt,
                    metrics = EXCLUDED.metrics,
                    latency_ms = EXCLUDED.latency_ms,
                    error = EXCLUDED.error
                """,
                (
                    task_id,
                    row["qid"],
                    row["question"],
                    row.get("reference"),
                    row.get("response"),
                    Jsonb(row.get("retrieval_ids") or []),
                    Jsonb(row.get("retrieval_gt") or []),
                    Jsonb(row.get("metrics") or {}),
                    row.get("latency_ms"),
                    row.get("error"),
                ),
            )
            conn.commit()

    def list_samples(self, task_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT qid, question, reference, response, retrieval_ids, retrieval_gt,
                       metrics, latency_ms, error
                FROM ks_evaluation_samples
                WHERE task_id = %s
                ORDER BY qid
                LIMIT %s OFFSET %s
                """,
                (task_id, limit, offset),
            ).fetchall()
        return [
            {
                "qid": r[0],
                "question": r[1],
                "reference": r[2],
                "response": r[3],
                "retrieval_ids": r[4],
                "retrieval_gt": r[5],
                "metrics": r[6],
                "latency_ms": r[7],
                "error": r[8],
            }
            for r in rows
        ]

    def count_samples(self, task_id: str) -> int:
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ks_evaluation_samples WHERE task_id = %s",
                (task_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def list_tasks(
        self,
        *,
        owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        ensure_eval_tables()
        owner = owner or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            rows = conn.execute(
                """
                SELECT id, owner, dataset_id, suite, pipeline_profile, status,
                       config_snapshot, metric_summary, total, finished, err_msg,
                       eval_kb_id, created_at, started_at, finished_at
                FROM ks_evaluation_tasks
                WHERE owner = %s
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (owner, limit, offset),
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def count_tasks(self, owner: str | None = None) -> int:
        owner = owner or settings.default_owner
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM ks_evaluation_tasks WHERE owner = %s",
                (owner,),
            ).fetchone()
        return int(row[0]) if row else 0

    def delete_task(self, task_id: str) -> bool:
        ensure_eval_tables()
        with psycopg.connect(self.dsn) as conn:
            cur = conn.execute("DELETE FROM ks_evaluation_tasks WHERE id = %s", (task_id,))
            conn.commit()
            return cur.rowcount > 0


def _row_to_task(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "owner": row[1],
        "dataset_id": row[2],
        "suite": row[3],
        "pipeline_profile": row[4],
        "status": row[5],
        "config_snapshot": row[6] if isinstance(row[6], dict) else json.loads(row[6] or "{}"),
        "metric_summary": row[7],
        "total": row[8],
        "finished": row[9],
        "err_msg": row[10],
        "eval_kb_id": row[11],
        "created_at": row[12].isoformat() if row[12] else None,
        "started_at": row[13].isoformat() if row[13] else None,
        "finished_at": row[14].isoformat() if row[14] else None,
    }
