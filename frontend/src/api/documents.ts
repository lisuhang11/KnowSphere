import request from './request'

/** 文档解析状态（状态机：pending→processing→completed/failed，另加 cancelled） */
export type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled'

export interface DocumentInfo {
  document_id: string
  file_name: string
  chunk_count: number
  updated_at: string | null
  /** 解析状态（异步上传后由后端驱动演进） */
  status?: DocumentStatus
  /** 失败/入队失败原因 */
  error_message?: string | null
  /** 当前处理阶段（诊断用） */
  stage?: string | null
  /** 文档级处理配置（只含显式指定字段，omitempty 语义，空对象=跟随库默认） */
  process_config?: ChunkingProcessConfig | null
  /** 实际生效的切分 tier（heading/heuristic/legacy），存量数据可能为 null */
  applied_strategy?: string | null
  /** 文档所属知识库（getDocumentMeta 返回） */
  knowledge_base_id?: number | null
  /** 解析出的内嵌图片元数据（含 MinIO storage_key），列表/详情据此展示图片预览 */
  image_refs?: ImageRefInfo[] | null
}

/** 文档内嵌图片引用（对齐后端 ImageRef：markdown 占位 ![alt](images/xxx.jpg)） */
export interface ImageRefInfo {
  filename: string
  /** 原图相对引用（docx 等内部路径） */
  original_ref?: string | null
  mime_type: string
  /** MinIO 对象 key（经后端鉴权代理读取，不直接暴露地址） */
  storage_key?: string | null
}

/** 文档内嵌图片代理 URL：后端从 MinIO 拉取并做归属校验（filename 必须在 image_refs 内） */
export function documentImageUrl(documentId: string, filename: string): string {
  return `/api/documents/${documentId}/images/${encodeURIComponent(filename)}`
}

/** 轮询接口 GET /documents/{id}/status 的返回结构 */
export interface DocumentStatusInfo {
  document_id: string
  status: DocumentStatus
  error_message: string | null
  stage: string | null
  updated_at: string | null
}

/** 在途状态（需要轮询）：pending / processing */
export function isStatusInFlight(status?: DocumentStatus | null): boolean {
  return status === 'pending' || status === 'processing'
}

/** 文档级切块配置 */
export interface ChunkingProcessConfig {
  chunking_config?: {
    strategy?: string
    chunk_size?: number
    chunk_overlap?: number
    enable_parent_child?: boolean
    parent_chunk_size?: number
    child_chunk_size?: number
  }
}

/** 列出某知识库内的文档 */
export async function listDocuments(kbId: number): Promise<DocumentInfo[]> {
  const resp = await request.get<DocumentInfo[]>('/documents', { params: { kb_id: kbId } })
  return resp.data
}

/** 按 document_id 取文档元信息（不依赖 kb_id） */
export async function getDocumentMeta(documentId: string): Promise<DocumentInfo> {
  const resp = await request.get<DocumentInfo>(`/documents/${documentId}/meta`)
  return resp.data
}

/** 上传文档到指定知识库（异步）：立即返回 202，文档状态由后端驱动，前端轮询 status */
export async function uploadDocument(
  file: File,
  kbId: number,
  processConfig?: ChunkingProcessConfig | null,
  onProgress?: (percent: number) => void,
): Promise<{ document_id: string; file_name: string; kb_id: number; status: DocumentStatus; task_id: string | null }> {
  const form = new FormData()
  form.append('file', file)
  form.append('kb_id', String(kbId))
  if (processConfig) form.append('process_config', JSON.stringify(processConfig))
  const resp = await request.post<{
    document_id: string
    file_name: string
    kb_id: number
    status: DocumentStatus
    task_id: string | null
  }>('/upload', form, {
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
    },
  })
  return resp.data
}

/** 轮询文档解析状态（过滤在途项后批量查询） */
export async function getDocumentStatus(documentId: string): Promise<DocumentStatusInfo> {
  const resp = await request.get<DocumentStatusInfo>(`/documents/${documentId}/status`)
  return resp.data
}

/** 取消解析：pending/processing → cancelled（保留已写数据，可重新解析） */
export async function cancelDocument(
  documentId: string,
): Promise<{ document_id: string; status: DocumentStatus }> {
  const resp = await request.post<{ document_id: string; status: DocumentStatus }>(
    `/documents/${documentId}/cancel`,
  )
  return resp.data
}

/** 重新解析（异步）：任意终态 → processing，立即返回 202 */
export async function reparseDocument(
  documentId: string,
  processConfig?: ChunkingProcessConfig | null,
): Promise<{ document_id: string; status: DocumentStatus; task_id: string }> {
  const resp = await request.post<{ document_id: string; status: DocumentStatus; task_id: string }>(
    `/documents/${documentId}/reparse`,
    processConfig ? { process_config: processConfig } : null,
  )
  return resp.data
}

export async function deleteDocument(
  documentId: string,
): Promise<{ document_id: string; deleted_chunks: number }> {
  const resp = await request.delete<{ document_id: string; deleted_chunks: number }>(
    `/documents/${documentId}`,
  )
  return resp.data
}

/* ---------- 文档详情：切块列表 / 原文预览 / 切块预览 ---------- */

export interface ChunkInfo {
  id: number
  chunk_index: number
  content: string
  metadata: Record<string, unknown>
  chunk_type?: string
  parent_chunk_id?: number | null
  char_count: number
  token_count: number
  created_at: string | null
}

export interface ChunkListResult {
  total: number
  page: number
  page_size: number
  chunks: ChunkInfo[]
}

export async function listChunks(
  documentId: string,
  page = 1,
  pageSize = 20,
  includeParentText = false,
): Promise<ChunkListResult> {
  const resp = await request.get<ChunkListResult>(`/documents/${documentId}/chunks`, {
    params: { page, page_size: pageSize, include_parent_text: includeParentText },
  })
  return resp.data
}

export async function getChunkById(chunkId: number): Promise<ChunkInfo> {
  const resp = await request.get<ChunkInfo>(`/chunks/${chunkId}`)
  return resp.data
}

export interface DocumentPreview {
  document_id: string
  file_name: string
  content: string
  truncated: boolean
}

export function documentFileUrl(documentId: string): string {
  return `/api/documents/${documentId}/file`
}

export async function previewDocument(documentId: string): Promise<DocumentPreview> {
  const resp = await request.get<DocumentPreview>(`/documents/${documentId}/preview`)
  return resp.data
}

export interface PreviewChunk {
  seq: number
  content: string
  context_header?: string | null
  char_count: number
  token_count: number
  parent_index?: number
}

/** 文档画像（与后端 chunkers.profiler.DocumentProfile.to_dict 对应） */
export interface ChunkingProfile {
  char_count: number
  line_count: number
  avg_line_len: number
  line_len_stddev: number
  md_heading_total: number
  md_heading_counts: Record<string, number>
  heading_density: number
  dominant_heading_level: number
  numbered_section_count: number
  all_caps_short_line_count: number
  form_feed_count: number
  visual_sep_count: number
  chapter_marker_count: number
  repeated_footer_count: number
  heuristic_marker_total: number
  has_tables: boolean
  has_code: boolean
  detected_langs: string[]
}

/** 单级切块预览结果 */
export interface FlatChunkingPreviewResult {
  enable_parent_child: false
  chunk_size: number
  chunk_overlap: number
  chunk_count: number
  strategy: string
  selected_tier: string
  tier_chain: string[]
  rejected: { tier: string; reason: string }[]
  profile: ChunkingProfile
  stats: {
    chunk_count: number
    avg_chars: number
    stddev_chars?: number
    min_chars: number
    max_chars: number
  }
  chunks: PreviewChunk[]
}

/** 父子分块预览结果 */
export interface ParentChildChunkingPreviewResult {
  enable_parent_child: true
  parent_chunk_size: number
  child_chunk_size: number
  chunk_overlap: number
  chunk_count: number
  parent_count: number
  strategy: string
  selected_tier: string
  tier_chain: string[]
  rejected: { tier: string; reason: string }[]
  profile: ChunkingProfile
  stats: {
    parent_count?: number
    child_count?: number
    chunk_count: number
    avg_chars?: number
    min_chars?: number
    max_chars?: number
  }
  chunks: PreviewChunk[]
}

export type ChunkingPreviewResult = FlatChunkingPreviewResult | ParentChildChunkingPreviewResult

export interface PreviewChunkingOptions {
  strategy?: string
  chunkSize?: number
  chunkOverlap?: number
  kbId?: number
  enableParentChild?: boolean
  parentChunkSize?: number
  childChunkSize?: number
}

export async function previewChunking(
  text: string,
  options?: PreviewChunkingOptions,
): Promise<ChunkingPreviewResult> {
  const resp = await request.post<ChunkingPreviewResult>('/preview-chunking', {
    text,
    strategy: options?.strategy ?? 'auto',
    kb_id: options?.kbId ?? null,
    chunk_size: options?.chunkSize ?? null,
    chunk_overlap: options?.chunkOverlap ?? null,
    enable_parent_child: options?.enableParentChild ?? null,
    parent_chunk_size: options?.parentChunkSize ?? null,
    child_chunk_size: options?.childChunkSize ?? null,
  })
  return resp.data
}
