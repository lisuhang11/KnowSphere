import type { Citation } from '@/api/sessions'

export type KnowledgeReferenceLike = {
  id?: string
  knowledge_id?: string
  knowledge_title?: string
  knowledge_filename?: string
  knowledge_base_id?: string
  chunk_index?: number
  content?: string
  url?: string
}

export type ReferenceHighlightTarget = {
  documentId?: string
  index?: number
}

export type ReferenceListItem = {
  key: string
  kind: 'document' | 'web'
  index: number
  title: string
  snippet?: string
  content?: string
  knowledgeId?: string
  knowledgeBaseId?: string
  url?: string
  host?: string
}

export type ReferenceDrawerSection = {
  id: 'documents'
  items: ReferenceListItem[]
}

const HTTP_URL_RE = /https?:\/\/[^\s<>"'）】\]]+/i

export function extractHttpUrl(value: string | undefined | null): string | undefined {
  const raw = String(value || '').trim()
  if (!raw) return undefined
  const candidate = raw.startsWith('//') ? `https:${raw}` : raw
  if (/^https?:\/\//i.test(candidate)) {
    try {
      const parsed = new URL(candidate.split(/\s/)[0])
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.href
    } catch {
      /* fall through and scan the string */
    }
  }
  const matched = raw.match(HTTP_URL_RE)
  if (!matched) return undefined
  const cleaned = matched[0].replace(/[.,;。，；]+$/, '')
  try {
    return new URL(cleaned).href
  } catch {
    return cleaned
  }
}

export function isHttpUrl(value: string | undefined | null): boolean {
  return Boolean(extractHttpUrl(value))
}

export function webHostLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** 用户手势下打开外链。新标签成功则拦截默认行为，避免双开；失败则交给 <a href>。 */
export function openExternalUrl(url: string | undefined | null, event?: MouseEvent): boolean {
  const href = extractHttpUrl(url)
  if (!href) return false
  if (event) {
    if (event.defaultPrevented) return false
    if (typeof event.button === 'number' && event.button !== 0) return false
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false
  }
  const opened = window.open(href, '_blank', 'noopener,noreferrer')
  if (!opened) return false
  event?.preventDefault()
  event?.stopPropagation()
  try {
    opened.opener = null
  } catch {
    /* ignore */
  }
  return true
}

export function formatReferenceSnippet(text: string | undefined): string {
  let value = String(text || '').replace(/\s+/g, ' ').trim()
  if (!value) return ''
  value = value.replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
  value = value.replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
  value = value.replace(/`([^`]+)`/g, '$1')
  value = value.replace(/\*\*([^*]+)\*\*/g, '$1')
  value = value.replace(/\*([^*]+)\*/g, '$1')
  return value.replace(/\s+/g, ' ').trim()
}

function resolveRefUrl(ref: KnowledgeReferenceLike): string | undefined {
  return (
    extractHttpUrl(ref.url) ||
    extractHttpUrl(ref.knowledge_id) ||
    extractHttpUrl(ref.id) ||
    extractHttpUrl(ref.content)
  )
}

export function citationsToReferences(citations: Citation[]): KnowledgeReferenceLike[] {
  return citations.map((c) => ({
    id: c.document_id,
    knowledge_id: c.document_id,
    knowledge_title: c.file_name,
    knowledge_filename: c.file_name,
    content: c.snippet,
    chunk_index: c.chunk_index,
    url: extractHttpUrl(c.url) || extractHttpUrl(c.document_id),
  }))
}

export function buildReferenceSections(references: KnowledgeReferenceLike[]): ReferenceDrawerSection[] {
  const groupMap = new Map<string, ReferenceListItem>()
  references.forEach((ref, index) => {
    const key = ref.knowledge_id || ref.id || `ref-${index}`
    if (!groupMap.has(key)) {
      const url = resolveRefUrl(ref)
      groupMap.set(key, {
        key,
        kind: url ? 'web' : 'document',
        index: groupMap.size + 1,
        title: ref.knowledge_title || ref.knowledge_filename || `来源 ${groupMap.size + 1}`,
        snippet: formatReferenceSnippet(ref.content),
        content: ref.content,
        knowledgeId: ref.knowledge_id || ref.id,
        knowledgeBaseId: ref.knowledge_base_id,
        url,
        host: url ? webHostLabel(url) : undefined,
      })
    }
  })
  const items = Array.from(groupMap.values())
  if (!items.length) return []
  return [{ id: 'documents', items }]
}

export function resolveReferenceHighlightKey(
  references: KnowledgeReferenceLike[],
  target: ReferenceHighlightTarget | null | undefined,
): string | null {
  if (!target) return null
  if (target.index != null) {
    const sections = buildReferenceSections(references)
    const item = sections[0]?.items[target.index - 1]
    if (item) return item.key
  }
  if (target.documentId) {
    const found = references.find((r) => r.id === target.documentId || r.knowledge_id === target.documentId)
    if (found) return found.knowledge_id || found.id || null
  }
  return null
}
