/** 将 LangGraph 消息 content（string 或 block 数组）提取为纯文本 */
export function extractText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((b) => {
        if (typeof b === 'string') return b
        if (b && typeof b === 'object') {
          const block = b as Record<string, unknown>
          if (block.type === 'text' && typeof block.text === 'string') return block.text
          if (block.type === 'text' && typeof block.content === 'string') return block.content
        }
        return ''
      })
      .join('')
  }
  return ''
}

let seq = 0
export function uid(): string {
  seq += 1
  return `${Date.now().toString(36)}-${seq}`
}
