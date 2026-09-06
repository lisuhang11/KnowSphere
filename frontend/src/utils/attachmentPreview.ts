/** 附件 / 文档原文预览类型判定（对齐 WeKnora filePreview） */

export type AttachmentPreviewKind =
  | 'pdf'
  | 'docx'
  | 'pptx'
  | 'excel'
  | 'image'
  | 'markdown'
  | 'html'
  | 'text'
  | 'audio'
  | 'unsupported'

const IMAGE_EXTS = new Set([
  'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'svgz', 'ico', 'avif', 'heic', 'heif',
])

const TEXT_EXTS = new Set([
  'txt', 'text', 'log', 'json', 'jsonl', 'xml', 'yaml', 'yml',
  'js', 'mjs', 'ts', 'tsx', 'jsx', 'py', 'java', 'go', 'rs', 'rb', 'php', 'cs', 'cpp', 'c', 'h',
  'sh', 'bash', 'sql', 'mdx',
])

const EXCEL_EXTS = new Set(['xlsx', 'xls', 'csv', 'tsv', 'tab'])
const AUDIO_EXTS = new Set(['mp3', 'wav', 'm4a', 'flac', 'ogg', 'aac'])

export type ChatAttachmentLike = {
  id?: string
  file_name?: string
  file_type?: string
}

function extFromName(fileName?: string, fileType?: string): string {
  const raw = (fileType || fileName || '').trim().toLowerCase()
  if (!raw) return ''
  if (raw.includes('.')) return raw.split('.').pop() || ''
  return raw.replace(/^\./, '')
}

export function resolveAttachmentFileType(fileName?: string, fileType?: string): string {
  return extFromName(fileName, fileType)
}

export function resolveAttachmentPreviewKind(fileName?: string, fileType?: string): AttachmentPreviewKind {
  const ext = extFromName(fileName, fileType)
  if (!ext) return 'unsupported'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx' || ext === 'doc') return 'docx'
  if (ext === 'pptx' || ext === 'ppt') return 'pptx'
  if (EXCEL_EXTS.has(ext)) return 'excel'
  if (ext === 'md' || ext === 'markdown') return 'markdown'
  if (ext === 'html' || ext === 'htm' || ext === 'xhtml') return 'html'
  if (IMAGE_EXTS.has(ext)) return 'image'
  if (AUDIO_EXTS.has(ext)) return 'audio'
  if (TEXT_EXTS.has(ext)) return 'text'
  return 'unsupported'
}

export function isPreviewableAttachment(attachment: ChatAttachmentLike | null | undefined): boolean {
  if (!attachment?.id) return false
  return resolveAttachmentPreviewKind(attachment.file_name, attachment.file_type) !== 'unsupported'
}
