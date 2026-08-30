/** 会话 API（经 vite 代理 /api -> 后端） */

import request from './request'

export interface Session {
  id: string
  /** LangGraph checkpoint 键，与 id 相同 */
  thread_id?: string
  title?: string
  kb_ids?: number[]
  is_pinned?: boolean
  pinned_at?: string | null
  created_at?: string
  updated_at?: string
  metadata?: Record<string, unknown>
}

export interface LangMessage {
  type?: string
  role?: string
  content: unknown
  name?: string
  additional_kwargs?: Record<string, unknown>
}

export interface SessionState {
  values?: { messages?: LangMessage[] }
}

/** 引用元数据（citation_meta 帧） */
export interface Citation {
  index: number
  document_id: string
  file_name: string
  chunk_index: number
  score?: number
  snippet?: string
}

export type StreamHandler = (event: string, data: Record<string, unknown>) => void

function sessionId(s: Session): string {
  return s.id || s.thread_id || ''
}

export { streamSessionRun } from '@/api/chatStream'

export async function createSession(title?: string, kbIds?: number[]): Promise<Session> {
  const body: Record<string, unknown> = {}
  if (title) body.title = title
  // 显式传入时始终带上（含 []），与流式对话语义一致
  if (kbIds !== undefined) body.kb_ids = kbIds
  const resp = await request.post<Session>('/sessions', body)
  return resp.data
}

export async function listSessions(limit = 100): Promise<Session[]> {
  const resp = await request.get<Session[]>('/sessions', { params: { limit } })
  return resp.data
}

export async function getSession(sessionId: string): Promise<Session> {
  const resp = await request.get<Session>(`/sessions/${sessionId}`)
  return resp.data
}

export async function updateSession(
  sessionId: string,
  payload: { title?: string; kb_ids?: number[] },
): Promise<Session> {
  const resp = await request.put<Session>(`/sessions/${sessionId}`, payload)
  return resp.data
}

export async function deleteSession(sessionId: string): Promise<void> {
  await request.delete(`/sessions/${sessionId}`)
}

export async function clearSessionMessages(sessionId: string): Promise<void> {
  await request.delete(`/sessions/${sessionId}/messages`)
}

export async function pinSession(sessionId: string): Promise<Session> {
  const resp = await request.post<{ session: Session }>(`/sessions/${sessionId}/pin`)
  return resp.data.session
}

export async function unpinSession(sessionId: string): Promise<Session> {
  const resp = await request.delete<{ session: Session }>(`/sessions/${sessionId}/pin`)
  return resp.data.session
}

export async function getSessionState(sessionId: string): Promise<SessionState> {
  const resp = await request.get<SessionState>(`/sessions/${sessionId}/state`)
  return resp.data
}

export { sessionId }
