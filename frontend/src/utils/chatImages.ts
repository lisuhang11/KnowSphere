export const CHAT_IMAGE_ACCEPT = 'image/jpeg,image/png,image/gif,image/webp'
export const CHAT_ATTACHMENT_ACCEPT =
  'image/jpeg,image/png,image/gif,image/webp,.pdf,.docx,.doc,.pptx,.xlsx,.md,.txt,.html'
export const CHAT_IMAGE_MAX_COUNT = 5
export const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024
export const CHAT_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

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
