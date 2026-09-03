/**
 * SSE 流式对话（@microsoft/fetch-event-source）
 */

import { fetchEventSource } from '@microsoft/fetch-event-source'

export type StreamHandler = (event: string, data: Record<string, unknown>) => void

export interface StreamImagePayload {
  data: string
}

async function openSessionSse(
  url: string,
  onEvent: StreamHandler,
  init: {
    method: 'GET' | 'POST'
    body?: string
    signal?: AbortSignal
  },
): Promise<void> {
  await fetchEventSource(url, {
    method: init.method,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      Accept: 'text/event-stream',
    },
    body: init.body,
    signal: init.signal,
    openWhenHidden: true,

    async onopen(response) {
      if (!response.ok) {
        let detail = `HTTP ${response.status}`
        try {
          const data = await response.json()
          detail = String(data.detail || detail)
        } catch {
          try {
            detail = await response.text()
          } catch {
            /* ignore */
          }
        }
        const err = new Error(detail) as Error & { status?: number }
        err.status = response.status
        throw err
      }
    },

    onmessage(ev) {
      const raw = ev.data?.trim()
      if (!raw || raw === '[DONE]') return
      const event = ev.event || 'messages'
      try {
        onEvent(event, JSON.parse(raw) as Record<string, unknown>)
      } catch {
        /* 非 JSON 帧忽略 */
      }
    },

    onerror(err) {
      throw err instanceof Error ? err : new Error(String(err))
    },
  })
}

export async function streamSessionRun(
  sessionId: string,
  userText: string,
  onEvent: StreamHandler,
  signal?: AbortSignal,
  kbIds?: number[],
  chatModelId?: string | null,
  images?: StreamImagePayload[],
  attachmentIds?: string[],
  vlmModelId?: string | null,
  agentId?: string | null,
  webSearchEnabled?: boolean,
  skillNames?: string[],
): Promise<void> {
  const body: Record<string, unknown> = {
    message: userText,
    stream_mode: ['messages', 'custom'],
  }
  // 必须显式下发（含 []）：省略空数组时后端会回落到会话旧 kb_ids，清除选择会失效
  if (kbIds !== undefined) body.kb_ids = kbIds
  if (chatModelId?.trim()) body.chat_model_id = chatModelId.trim()
  if (vlmModelId?.trim()) body.vlm_model_id = vlmModelId.trim()
  if (agentId?.trim()) body.agent_id = agentId.trim()
  if (webSearchEnabled !== undefined) body.web_search_enabled = webSearchEnabled
  if (skillNames?.length) body.skill_names = skillNames
  if (attachmentIds?.length) body.attachment_ids = attachmentIds
  else if (images?.length) body.images = images

  await openSessionSse(`/api/sessions/${sessionId}/runs/stream`, onEvent, {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  })
}

export async function continueSessionRun(
  sessionId: string,
  onEvent: StreamHandler,
  signal?: AbortSignal,
): Promise<void> {
  await openSessionSse(`/api/sessions/${sessionId}/runs/continue`, onEvent, {
    method: 'GET',
    signal,
  })
}
