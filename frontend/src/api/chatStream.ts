/**
 * SSE 流式对话（@microsoft/fetch-event-source）
 */

import { fetchEventSource } from '@microsoft/fetch-event-source'

export type StreamHandler = (event: string, data: Record<string, unknown>) => void

export interface StreamImagePayload {
  data: string
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
): Promise<void> {
  const body: Record<string, unknown> = {
    message: userText,
    stream_mode: ['messages', 'custom'],
  }
  if (kbIds?.length) body.kb_ids = kbIds
  if (chatModelId?.trim()) body.chat_model_id = chatModelId.trim()
  if (vlmModelId?.trim()) body.vlm_model_id = vlmModelId.trim()
  if (attachmentIds?.length) body.attachment_ids = attachmentIds
  else if (images?.length) body.images = images

  await fetchEventSource(`/api/sessions/${sessionId}/runs/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify(body),
    signal,
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
        throw new Error(detail)
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
