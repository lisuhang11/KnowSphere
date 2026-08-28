import type { Citation } from '@/api/sessions'

/** 底部来源列表：按 document_id 去重，保留 cite index 最小的一条。 */
export function uniqueCitationSources(citations: Citation[] | undefined): Citation[] {
  if (!citations?.length) return []
  const seen = new Set<string>()
  const sorted = [...citations].sort((a, b) => a.index - b.index)
  const out: Citation[] = []
  for (const c of sorted) {
    const key = c.document_id || c.file_name
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(c)
  }
  return out
}

/** 合并多次 doc_retrieval 的 citation_meta（后续批次 cite index 续编）。 */
export function mergeCitationMeta(
  existing: Citation[] | undefined,
  incoming: Citation[],
): Citation[] {
  if (!incoming.length) return existing ?? []
  if (!existing?.length) return incoming
  const offset = existing.length
  return [
    ...existing,
    ...incoming.map((c, i) => ({ ...c, index: offset + i + 1 })),
  ]
}
