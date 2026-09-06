import { CHAT_AUDIO_ACCEPT } from '@/utils/audio'

export const CHAT_IMAGE_ACCEPT = 'image/jpeg,image/png,image/gif,image/webp'
export const CHAT_DOCUMENT_ACCEPT = '.pdf,.docx,.doc,.pptx,.xlsx,.md,.txt,.html'
export const CHAT_ATTACHMENT_ACCEPT = `${CHAT_IMAGE_ACCEPT},${CHAT_DOCUMENT_ACCEPT}`

export function chatAttachmentAccept(opts: { vlmReady: boolean; audioReady: boolean }): string {
  const parts = [CHAT_DOCUMENT_ACCEPT]
  if (opts.vlmReady) parts.push(CHAT_IMAGE_ACCEPT)
  if (opts.audioReady) parts.push(CHAT_AUDIO_ACCEPT)
  return parts.join(',')
}
export const CHAT_IMAGE_MAX_COUNT = 5
export const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
export const CHAT_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
export const NO_VLM_IMAGE_UPLOAD_HINT =
  '未配置视觉理解（VLLM）模型，无法上传图片。请先在模型管理中添加。'

const IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff']

export function hasUsableVlm(models: { type: string; status?: string }[]): boolean {
  return models.some((m) => m.type === 'VLLM' && m.status !== 'disabled')
}

export function isChatImageFile(file: File): boolean {
  if (file.type.startsWith('image/')) return true
  const name = file.name.toLowerCase()
  return IMAGE_EXTS.some((ext) => name.endsWith(ext))
}

export interface PendingChatImage {
  id: string
  file: File
  previewUrl: string
}

export interface PendingChatAttachment {
  id: string
  file: File
  previewUrl: string
  attachmentId?: string
  status?: 'uploading' | 'uploaded' | 'processing' | 'ready' | 'failed'
  fileName: string
  errorMessage?: string
}

export interface ChatImageMeta {
  url: string
  caption?: string
}

export function validateChatImageFile(file: File): string | null {
  if (!file.type.startsWith('image/')) return '仅支持图片文件'
  if (file.size > CHAT_IMAGE_MAX_BYTES) return '单张图片不能超过 10MB'
  return null
}

export function validateChatAttachmentFile(file: File): string | null {
  if (file.size > CHAT_ATTACHMENT_MAX_BYTES) return '单个附件不能超过 20MB'
  if (file.size <= 0) return '文件为空'
  return null
}
