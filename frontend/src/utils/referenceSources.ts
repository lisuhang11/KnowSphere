import type { Citation } from '@/api/sessions'

export type KnowledgeReferenceLike = {
  id?: string
  knowledge_id?: string
  knowledge_title?: string
  knowledge_filename?: string
  knowledge_base_id?: string
  chunk_index?: number
  content?: string
}

export type ReferenceHighlightTarget = {
  documentId?: string
  index?: number
}

export type ReferenceListItem = {
  key: string
  kind: 'document'
  index: number
  title: string
  snippet?: string
  content?: string
  knowledgeId?: string
  knowledgeBaseId?: string
}

export type ReferenceDrawerSection = {
  id: 'documents'
  items: ReferenceListItem[]
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

export function citationsToReferences(citations: Citation[]): KnowledgeReferenceLike[] {
  return citations.map((c) => ({
    id: c.document_id,
    knowledge_id: c.document_id,
    knowledge_title: c.file_name,
    knowledge_filename: c.file_name,
    content: c.snippet,
    chunk_index: c.chunk_index,
  }))
}

export function buildReferenceSections(references: KnowledgeReferenceLike[]): ReferenceDrawerSection[] {
  const groupMap = new Map<string, ReferenceListItem>()
  references.forEach((ref, index) => {
    const key = ref.knowledge_id || ref.id || `ref-${index}`
    if (!groupMap.has(key)) {
      groupMap.set(key, {
        key,
        kind: 'document',
        index: groupMap.size + 1,
        title: ref.knowledge_title || ref.knowledge_filename || `来源 ${groupMap.size + 1}`,
        snippet: formatReferenceSnippet(ref.content),
        content: ref.content,
        knowledgeId: ref.knowledge_id || ref.id,
        knowledgeBaseId: ref.knowledge_base_id,
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
