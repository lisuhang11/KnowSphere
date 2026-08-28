import request from './request'
import type { ChunkStrategy } from '@/constants/chunking'

export type { ChunkStrategy }

/** 知识库（多知识库管理，单用户模型，每个库可独立配置分块与 embedding） */
export interface KnowledgeBase {
  id: number
  name: string
  description: string
  chunk_size: number
  chunk_overlap: number
  embedding_model_id: string
  embedding_dim: number
  chunk_strategy: ChunkStrategy
  summary_model_id?: string | null
  enable_parent_child: boolean
  parent_chunk_size: number
  child_chunk_size: number
  created_at: string | null
  updated_at: string | null
  document_count?: number
  chunk_count?: number
}

export interface KBCreatePayload {
  name: string
  description?: string
  chunk_size?: number
  chunk_overlap?: number
  embedding_model_id?: string
  summary_model_id?: string | null
  chunk_strategy?: ChunkStrategy
  enable_parent_child?: boolean
  parent_chunk_size?: number
  child_chunk_size?: number
}

export interface KBUpdatePayload {
  name?: string
  description?: string
  chunk_size?: number
  chunk_overlap?: number
  chunk_strategy?: ChunkStrategy
  summary_model_id?: string | null
  enable_parent_child?: boolean
  parent_chunk_size?: number
  child_chunk_size?: number
}

export async function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  const resp = await request.get<KnowledgeBase[]>('/knowledge-bases')
  return resp.data
}

export async function getKnowledgeBase(kbId: number): Promise<KnowledgeBase> {
  const resp = await request.get<KnowledgeBase>(`/knowledge-bases/${kbId}`)
  return resp.data
}

export async function createKnowledgeBase(payload: KBCreatePayload): Promise<KnowledgeBase> {
  const resp = await request.post<KnowledgeBase>('/knowledge-bases', payload)
  return resp.data
}

export async function updateKnowledgeBase(
  kbId: number,
  payload: KBUpdatePayload,
): Promise<KnowledgeBase> {
  const resp = await request.patch<KnowledgeBase>(`/knowledge-bases/${kbId}`, payload)
  return resp.data
}

export async function deleteKnowledgeBase(
  kbId: number,
): Promise<{ id: number; name: string; deleted_documents: number; deleted_chunks: number }> {
  const resp = await request.delete<{
    id: number
    name: string
    deleted_documents: number
    deleted_chunks: number
  }>(`/knowledge-bases/${kbId}`)
  return resp.data
}

export async function moveDocument(
  documentId: string,
  kbId: number,
): Promise<{ document_id: string; kb_id: number; moved_chunks: number }> {
  const resp = await request.post<{ document_id: string; kb_id: number; moved_chunks: number }>(
    `/documents/${documentId}/move`,
    { kb_id: kbId },
  )
  return resp.data
}
