"""Postgres + pgvector 表结构初始化。"""

from __future__ import annotations

import psycopg

from config.settings import settings

def init_schema(dsn: str) -> None:
    """创建扩展、表与索引（幂等，可重复执行）。"""
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                id          BIGSERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                chunk_size  INT  NOT NULL DEFAULT 600,
                chunk_overlap INT NOT NULL DEFAULT 90,
                embedding_model_id TEXT NOT NULL DEFAULT 'BAAI/bge-m3',
                embedding_dim INT  NOT NULL DEFAULT 1024,
                chunk_strategy TEXT NOT NULL DEFAULT 'auto',
                owner       TEXT NOT NULL DEFAULT 'default',
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # 存量库：知识库配置列后加，幂等补列
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS chunk_size INT NOT NULL DEFAULT 600"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS chunk_overlap INT NOT NULL DEFAULT 90"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS embedding_model_id TEXT NOT NULL DEFAULT 'BAAI/bge-m3'"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS embedding_dim INT NOT NULL DEFAULT 1024"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS chunk_strategy TEXT NOT NULL DEFAULT 'auto'"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS summary_model_id TEXT"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS enable_parent_child "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS parent_chunk_size "
            "INT NOT NULL DEFAULT 4096"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS child_chunk_size "
            "INT NOT NULL DEFAULT 384"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS graph_enabled "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS asr_enabled "
            "BOOLEAN NOT NULL DEFAULT FALSE"
        )
        conn.execute(
            "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS asr_model_id "
            "TEXT NOT NULL DEFAULT ''"
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id          BIGSERIAL PRIMARY KEY,
                document_id TEXT NOT NULL,
                file_name   TEXT NOT NULL,
                chunk_index INT  NOT NULL,
                content     TEXT NOT NULL,
                metadata    JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedding   VECTOR({settings.embedding_dim}),
                owner       TEXT NOT NULL DEFAULT 'default',
                knowledge_base_id BIGINT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # 存量库：chunks 表先于知识库功能存在，幂等补列
        conn.execute(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS knowledge_base_id BIGINT"
        )
        conn.execute(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS chunk_type "
            "TEXT NOT NULL DEFAULT 'text'"
        )
        conn.execute(
            "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS parent_chunk_id BIGINT"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw "
            "ON chunks USING hnsw (embedding vector_cosine_ops)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chunks_content_trgm "
            "ON chunks USING gin (content gin_trgm_ops)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS chunks_owner ON chunks (owner)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS chunks_kb ON chunks (knowledge_base_id)"
        )
        # 文档级处理配置：process_config 仅存用户显式指定的字段（omitempty 语义，
        # 与  KnowledgeProcessOverrides 对齐）；applied_strategy 记录实际
        # 生效的切分 tier（heading/heuristic/legacy），供列表/详情展示徽标。
        # image_refs：解析出的内嵌图片元数据（含 MinIO storage_key），供前端
        # 经 /documents/{id}/images/... 鉴权代理读取（Q5/Q12）。
        # status 解析状态机（对齐  parse_status）：pending → processing →
        # completed / failed，另加 cancelled（用户取消，保留已写数据可 reparse）。
        # stage 为当前处理阶段名（上传进度/列表诊断）；完整摄取与对话链路见 Langfuse traces。
        # stored_name：MinIO storage_key（{owner}/{kb_id}/{document_id}/{file_name}）
        # 历史本地文件为 {document_id}_{file_name}，无 `/`；存量数据无此列时回退 file_name。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id     TEXT PRIMARY KEY,
                file_name       TEXT NOT NULL,
                knowledge_base_id BIGINT,
                owner           TEXT NOT NULL DEFAULT 'default',
                process_config  JSONB NOT NULL DEFAULT '{}'::jsonb,
                applied_strategy TEXT,
                image_refs      JSONB NOT NULL DEFAULT '[]'::jsonb,
                status          TEXT NOT NULL DEFAULT 'pending',
                error_message   TEXT,
                stage           TEXT,
                task_id         TEXT,
                stored_name     TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS documents_owner ON documents (owner)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS documents_kb ON documents (knowledge_base_id)"
        )
        # 存量库：解析状态列后加，幂等补列
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"
        )
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message TEXT"
        )
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS stage TEXT"
        )
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS task_id TEXT"
        )
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS stored_name TEXT"
        )
        conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS image_refs JSONB NOT NULL DEFAULT '[]'::jsonb"
        )
        # 状态索引须在补列之后创建（存量库首次启动时 status 尚不存在）
        conn.execute("CREATE INDEX IF NOT EXISTS documents_status ON documents (status)")
        # 存量迁移：只有 chunks 没有 documents 行的文档（历史数据），补齐行并标
        # 为 completed——异步化后列表以 documents 为主表，不补行文档会"消失"。
        conn.execute(
            """
            INSERT INTO documents
                (document_id, file_name, knowledge_base_id, owner, applied_strategy,
                 status, created_at, updated_at)
            SELECT ch.document_id, MIN(ch.file_name), MIN(ch.knowledge_base_id),
                   ch.owner, NULL, 'completed',
                   MIN(ch.created_at), MAX(ch.created_at)
            FROM chunks ch
            WHERE NOT EXISTS (
                SELECT 1 FROM documents d
                WHERE d.document_id = ch.document_id AND d.owner = ch.owner
            )
            GROUP BY ch.document_id, ch.owner
            ON CONFLICT (document_id) DO NOTHING
            """
        )
        # 存量迁移 2：迁移前已存在的 documents 行被 ALTER 默认成 pending，
        # 但分块其实早已摄入完成——把它们提升为 completed（幂等，仅命中
        # pending 且无 task_id 的旧行；新异步行 pending 时尚未写分块，不受影响；
        # cancelled/failed 不在此列）。
        conn.execute(
            """
            UPDATE documents d
            SET status = 'completed', updated_at = now()
            WHERE d.status = 'pending'
              AND d.task_id IS NULL
              AND d.error_message IS NULL
              AND EXISTS (
                  SELECT 1 FROM chunks c
                  WHERE c.document_id = d.document_id AND c.owner = d.owner
              )
            """
        )
        # 长期记忆：跨会话画像 / 兴趣 / 常查资料（对齐 WeKnora memory_items + memory_doc_affinity）
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                id                TEXT PRIMARY KEY,
                owner             TEXT NOT NULL,
                kind              TEXT NOT NULL,
                content           TEXT NOT NULL,
                normalized_key    TEXT NOT NULL DEFAULT '',
                origin            TEXT NOT NULL DEFAULT 'explicit',
                status            TEXT NOT NULL DEFAULT 'active',
                importance        INT  NOT NULL DEFAULT 3,
                source_session_id TEXT NOT NULL DEFAULT '',
                created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS memory_items_owner_status "
            "ON memory_items (owner, status, importance DESC)"
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS memory_items_owner_kind_key
            ON memory_items (owner, kind, normalized_key)
            WHERE status = 'active' AND normalized_key <> ''
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_doc_affinity (
                owner       TEXT NOT NULL,
                document_id TEXT NOT NULL,
                title       TEXT NOT NULL DEFAULT '',
                hits        INT  NOT NULL DEFAULT 0,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (owner, document_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS memory_doc_affinity_owner_hits "
            "ON memory_doc_affinity (owner, hits DESC)"
        )
        conn.commit()
