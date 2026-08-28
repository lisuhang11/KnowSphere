/** 文件扩展名与大小格式化（对齐 WeKnora usermsg.vue） */

export function getFileExt(fileName: string): string {
  return (fileName || '').split('.').pop()?.toUpperCase() || 'FILE'
}

export function formatFileSize(bytes?: number): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
