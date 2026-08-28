import request from './request'

export type TemporaryAttachmentStatus = 'uploaded' | 'processing' | 'ready' | 'failed'

export interface TemporaryAttachment {
  id: string
  session_id: string
  file_name: string
  file_type: string
  file_size: number
  mime_type?: string
  status: TemporaryAttachmentStatus
  image_description?: string
  error_message?: string
  expires_at?: string
}

interface AttachmentResponse {
  success: boolean
  data: TemporaryAttachment
}

interface AttachmentListResponse {
  success: boolean
  data: TemporaryAttachment[]
}

export async function uploadTemporaryAttachment(
  sessionId: string,
  file: File,
): Promise<AttachmentResponse> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await request.post<AttachmentResponse>(
    `/sessions/${sessionId}/attachments`,
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

export async function getTemporaryAttachment(
  sessionId: string,
  attachmentId: string,
): Promise<AttachmentResponse> {
  const { data } = await request.get<AttachmentResponse>(
    `/sessions/${sessionId}/attachments/${attachmentId}`,
  )
  return data
}

export async function deleteTemporaryAttachment(
  sessionId: string,
  attachmentId: string,
): Promise<void> {
  await request.delete(`/sessions/${sessionId}/attachments/${attachmentId}`)
}

export async function listTemporaryAttachments(
  sessionId: string,
): Promise<AttachmentListResponse> {
  const { data } = await request.get<AttachmentListResponse>(`/sessions/${sessionId}/attachments`)
  return data
}

export function attachmentPreviewUrl(sessionId: string, attachmentId: string): string {
  return `/api/sessions/${sessionId}/attachments/${attachmentId}/preview`
}

/** 轮询直到 ready/failed 或超时 */
export async function waitTemporaryAttachmentReady(
  sessionId: string,
  attachmentId: string,
  opts?: { intervalMs?: number; timeoutMs?: number },
): Promise<TemporaryAttachment> {
  const intervalMs = opts?.intervalMs ?? 800
  const timeoutMs = opts?.timeoutMs ?? 60000
  const started = Date.now()
  // eslint-disable-next-line no-constant-condition
  for (;;) {
    if (Date.now() - started >= timeoutMs) break
    const res = await getTemporaryAttachment(sessionId, attachmentId)
    const st = res.data.status
    if (st === 'ready' || st === 'failed') return res.data
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('附件解析超时，请稍后重试')
}
