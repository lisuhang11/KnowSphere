# KnowSphere API 命名规范

统一本项目的 **路径、HTTP 方法、处理函数名** 口径。  
**本文定义规范与实现对照**；迁移期可保留旧路径别名，避免前端一次性全改。

---

## 1. 总则

| 规则 | 说明 |
|------|------|
| **资源复数 + kebab-case** | `/knowledge-bases`、`/sessions`，不用 `/knowledgeBase` |
| **路径参数语义** | 会话用 `{session_id}` 或 `{id}`；知识库 `{id}`；文档 `{document_id}`；模型 `{id}` |
| **更新用 PUT** | 全量/字段级更新统一 `PUT /resource/{id}`；`PATCH` 视为历史遗留，新接口不用 |
| **列表用 GET** | `GET /resource` 列出；需要复杂筛选时用 query，避免 `POST .../search`（LangGraph 层除外） |
| **子资源嵌套** | 文档归属知识库：`/knowledge-bases/{id}/documents/...`；会话附件：`/sessions/{session_id}/attachments/...` |
| **Handler 命名** | 动词 + 资源：`CreateSession`、`ListKnowledgeBases`；Python 实现可用 `create_session` |
| **调试/预览独立前缀** | 分块预览 → `/chunker/preview`；模型连通性 → `/models/{id}/debug` |

**术语**

| 语义 | 对外用语 | 说明 |
|------|----------|------|
| 会话 | `session` | 路径 `/sessions`；LangGraph checkpoint 键与 `id` 相同 |
| 知识库 | `knowledge-base` | 路径 `/knowledge-bases` |
| 文档 | `document` | 归属知识库，路径 `/documents/{id}` 或嵌套前缀 |

---

## 2. 会话 `/sessions`（对话）

对话由嵌入式 LangGraph 提供，路径为 **`/sessions`**。

### 2.1 规范路由

| 方法 | 路径 | Handler | 说明 |
|------|------|---------|------|
| POST | `/sessions` | `CreateSession` | 创建会话 |
| GET | `/sessions` | `ListSessions` | 列出会话 |
| GET | `/sessions/{id}` | `GetSession` | 会话详情（标题、kb 范围等 metadata） |
| PUT | `/sessions/{id}` | `UpdateSession` | 更新标题、知识库范围等 |
| DELETE | `/sessions/{id}` | `DeleteSession` | 删除会话及 checkpoint |
| DELETE | `/sessions/batch` | `BatchDeleteSessions` | 批量删除 |
| DELETE | `/sessions/{id}/messages` | `ClearSessionMessages` | 清空消息 |
| POST | `/sessions/{session_id}/generate_title` | `GenerateTitle` | LLM 生成标题 |
| POST | `/sessions/{session_id}/stop` | `StopSession` | 停止生成 |
| POST | `/sessions/{session_id}/pin` | `PinSession` | 置顶 |
| DELETE | `/sessions/{id}/pin` | `UnpinSession` | 取消置顶 |
| POST | `/sessions/{session_id}/attachments` | `UploadTemporaryDocument` | 上传临时附件 |
| GET | `/sessions/{id}/attachments` | `ListTemporaryDocuments` | 列出临时附件 |
| GET | `/sessions/{id}/attachments/{attachment_id}` | `GetTemporaryDocument` | 附件详情 |
| GET | `/sessions/{id}/attachments/{attachment_id}/preview` | `PreviewTemporaryDocument` | 附件预览 |
| DELETE | `/sessions/{id}/attachments/{attachment_id}` | `DeleteTemporaryDocument` | 删除附件 |

**流式对话**

| 方法 | 路径 | Handler | 说明 |
|------|------|---------|------|
| POST | `/sessions/{session_id}/runs/stream` | `StreamSessionRun` | SSE 流式问答 |
| GET | `/sessions/{id}/state` | `GetSessionState` | 消息历史（checkpoint） |

### 2.2 当前实现

| Handler | 路径 | 实现 |
|---------|------|------|
| `CreateSession` | `POST /sessions` | ✓ `api/sessions.py` |
| `ListSessions` | `GET /sessions` | ✓ |
| `GetSession` | `GET /sessions/{id}` | ✓ |
| `UpdateSession` | `PUT /sessions/{id}` | ✓ |
| `DeleteSession` | `DELETE /sessions/{id}` | ✓ |
| `ClearSessionMessages` | `DELETE /sessions/{id}/messages` | ✓ |
| `PinSession` / `UnpinSession` | `POST/DELETE /sessions/{id}/pin` | ✓ |
| `GetSessionState` | `GET /sessions/{id}/state` | ✓ |
| `StreamSessionRun` | `POST /sessions/{id}/runs/stream` | ✓ |
| 临时附件 | `/sessions/{id}/attachments/...` | ✓ `api/temporary_attachments.py` |

---

## 3. 知识库 `/knowledge-bases`

| 方法 | 路径 | Handler | 现状 |
|------|------|---------|------|
| POST | `/knowledge-bases` | `CreateKnowledgeBase` | ✓ |
| GET | `/knowledge-bases` | `ListKnowledgeBases` | ✓ |
| GET | `/knowledge-bases/{id}` | `GetKnowledgeBase` | ✓ |
| PUT | `/knowledge-bases/{id}` | `UpdateKnowledgeBase` | ⚠ 现为 `PATCH` |
| DELETE | `/knowledge-bases/{id}` | `DeleteKnowledgeBase` | ✓ |

---

## 4. 文档 `/documents`

当前文档路由在 `api/documents.py`。创建/列表挂在知识库下；单条文档操作仍用 `/documents/{id}`。

### 4.1 规范路由（目标形态）

| 方法 | 路径 | Handler | 说明 |
|------|------|---------|------|
| POST | `/knowledge-bases/{id}/documents` | `UploadDocument` | 上传并异步解析（202） |
| GET | `/knowledge-bases/{id}/documents` | `ListDocuments` | 库内文档列表 |
| GET | `/documents/{document_id}` | `GetDocument` | 文档元信息 |
| DELETE | `/documents/{document_id}` | `DeleteDocument` | 删除文档及分块 |
| GET | `/documents/{document_id}/status` | `GetDocumentStatus` | 解析状态轮询 |
| POST | `/documents/{document_id}/cancel` | `CancelDocument` | 取消解析 |
| POST | `/documents/{document_id}/reparse` | `ReparseDocument` | 重新解析 |
| GET | `/documents/{document_id}/chunks` | `ListDocumentChunks` | 分页分块 |
| GET | `/documents/{document_id}/preview` | `PreviewDocument` | 原文预览 |
| POST | `/documents/{document_id}/move` | `MoveDocument` | 迁移到其他知识库 |

### 4.2 当前实现对照

| Handler | 当前路径 | 备注 |
|---------|----------|------|
| `UploadDocument` | `POST /knowledge-bases/{id}/documents` | ✓ 旧路径 `POST /upload` 仍作别名 |
| `ListDocuments` | `GET /knowledge-bases/{id}/documents` | ✓ 旧路径 `GET /documents?kb_id=` 仍作别名 |
| `GetDocument` | `GET /documents/{id}/meta` | ✓ |
| `DeleteDocument` | `DELETE /documents/{id}` | ✓ |
| `GetDocumentStatus` | `GET /documents/{id}/status` | ✓ |
| `CancelDocument` | `POST /documents/{id}/cancel` | ✓ |
| `ReparseDocument` | `POST /documents/{id}/reparse` | ✓ |
| `ListDocumentChunks` | `GET /documents/{id}/chunks` | ✓ |
| `PreviewDocument` | `GET /documents/{id}/preview` | ✓ |
| `MoveDocument` | `POST /documents/{id}/move` | ✓ |

---

## 5. 分块器 `/chunker`

| 方法 | 路径 | Handler | 现状 |
|------|------|---------|------|
| POST | `/chunker/preview` | `PreviewChunking` | ⚠ 现为 `POST /preview-chunking` |

---

## 6. 模型 `/models`

| 方法 | 路径 | Handler | 现状 |
|------|------|---------|------|
| GET | `/models/providers` | `ListModelProviders` | ✓ |
| POST | `/models` | `CreateModel` | ✓ |
| GET | `/models` | `ListModels` | ✓ |
| GET | `/models/{id}` | `GetModel` | ✓ |
| PUT | `/models/{id}` | `UpdateModel` | ✓ |
| DELETE | `/models/{id}` | `DeleteModel` | ✓ |
| POST | `/models/{id}/debug` | `DebugModel` | ✓ |
| PUT | `/models/{id}/credentials` | `PutModelCredentials` | ✓ |
| DELETE | `/models/{id}/credentials/{field}` | `DeleteModelCredentialField` | ✓ |

连通性测试统一走 `POST /models/{id}/debug`，按模型类型执行 ping / embedding / rerank 等探测。

---

## 7. 评测 `/evaluation`

| 方法 | 路径 | Handler | 现状 |
|------|------|---------|------|
| POST | `/evaluation` | `Evaluation` | CLI：`python -m evals.run_eval` |
| GET | `/evaluation` | `GetEvaluationResult` | 未暴露 REST |

---

## 8. 系统

| 方法 | 路径 | Handler | 现状 |
|------|------|---------|------|
| GET | `/health` | `Health` | ✓ `api/main.py` |

---

## 9. 实现文件

| 域 | 模块 |
|----|------|
| 会话 | `api/sessions.py` |
| 知识库 | `api/knowledge_bases.py` |
| 文档 | `api/documents.py` |
| 分块 | `api/chunker.py` |
| 模型 | `api/models.py` |
| 评测 | `evals/`（CLI） |

前端客户端：`frontend/src/api/*.ts`

---

## 10. 待整改清单

| 优先级 | 现状 | 规范 |
|--------|------|------|
| P0 | `POST /preview-chunking` | `POST /chunker/preview` |
| P1 | `PATCH /knowledge-bases/{id}` | `PUT /knowledge-bases/{id}` |
| P2 | `GET /documents/{id}/meta` | `GET /documents/{id}` |
| P3 | 缺 `POST /sessions/{id}/stop` | 显式停止流式生成接口 |

旧路径在迁移期应 **双注册或反向代理别名**。
