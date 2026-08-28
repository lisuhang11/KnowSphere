/** 会话 Markdown 导出（对齐 WeKnora utils/sessionMarkdown.ts，适配 KnowSphere ChatMsg） */

import type { ChatMsg } from '@/composables/useSessionChat'

export interface SessionMarkdownLabels {
  sessionId: string
  exportedAt: string
  user: string
  assistant: string
  attachments: string
  references: string
}

function markdownListText(value: string): string {
  return value.replace(/\s+/g, ' ').replace(/([\\`*_[\]<>])/g, '\\$1').trim()
}

function cleanMessageContent(content?: string): string {
  return String(content || '')
    .replace(/[ \t]+\n/g, '\n')
    .trim()
}

export function buildSessionMarkdown(options: {
  sessionId: string
  title: string
  messages: ChatMsg[]
  labels: SessionMarkdownLabels
  exportedAt?: string
}): string {
  const { sessionId, labels } = options
  const title = options.title.replace(/\s+/g, ' ').trim() || sessionId
  const exportedAt = options.exportedAt || new Date().toISOString()
  const blocks = [
    `# ${title.replace(/^#+\s*/, '')}`,
    `> ${labels.sessionId}: ${sessionId}  `,
    `> ${labels.exportedAt}: ${exportedAt}`,
  ]

  for (const message of options.messages) {
    if (message.role !== 'user' && message.role !== 'assistant') continue
    const content = cleanMessageContent(message.content)
    const attachments = (message.attachments || [])
      .map((a) => a.file_name?.trim() || '')
      .filter(Boolean)
    const references = [...new Set(
      (message.sourceDocs || message.citations || [])
        .map((c) => c.file_name?.trim() || '')
        .filter(Boolean)
        .map((name) => `- ${markdownListText(name)}`),
    )]
    if (!content && attachments.length === 0 && references.length === 0) continue

    blocks.push(`## ${message.role === 'user' ? labels.user : labels.assistant}`)
    if (content) blocks.push(content)
    if (attachments.length > 0) {
      blocks.push(
        `### ${labels.attachments}`,
        ...attachments.map((name) => `- ${markdownListText(name)}`),
      )
    }
    if (references.length > 0) {
      blocks.push(`### ${labels.references}`, ...references)
    }
  }

  return `${blocks.join('\n\n')}\n`
}
