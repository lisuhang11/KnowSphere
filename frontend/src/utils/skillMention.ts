/** 技能 @ 提及：芯片状态与文本解析（对齐 WeKnora，不把 @name 留在正文）。 */

export const SKILL_ICON = 'system-code'

const MENTION_RE = /(^|[\s])@([A-Za-z0-9_-]+)/g
const MUST_USE_OPEN = '<must_use>'
const MUST_USE_CLOSE = '</must_use>'
const MUST_USE_NAME_RE = /read_skill\(skill_name="([^"]+)"\)/g

export function extractPinnedSkillNames(text: string, boundNames?: string[]): string[] {
  const bound = boundNames?.length ? new Set(boundNames) : null
  const found: string[] = []
  const seen = new Set<string>()
  const src = text || ''
  let match: RegExpExecArray | null
  MENTION_RE.lastIndex = 0
  while ((match = MENTION_RE.exec(src))) {
    const name = match[2]
    if (!name || seen.has(name)) continue
    if (bound && !bound.has(name)) continue
    seen.add(name)
    found.push(name)
  }
  return found
}

export function mentionQueryAt(text: string, cursor: number): { start: number; query: string } | null {
  const head = (text || '').slice(0, Math.max(0, cursor))
  const m = head.match(/(^|[\s])@([A-Za-z0-9_-]*)$/)
  if (!m) return null
  const query = m[2] || ''
  const start = head.length - query.length - 1
  return { start, query }
}

export function stripMustUseBlock(text: string): string {
  const raw = text || ''
  const start = raw.indexOf(MUST_USE_OPEN)
  if (start < 0) return raw
  const end = raw.indexOf(MUST_USE_CLOSE, start)
  if (end < 0) return raw
  return `${raw.slice(0, start)}${raw.slice(end + MUST_USE_CLOSE.length)}`.trim()
}

export function parseMustUseSkillNames(text: string): string[] {
  const start = (text || '').indexOf(MUST_USE_OPEN)
  if (start < 0) return []
  const end = (text || '').indexOf(MUST_USE_CLOSE, start)
  const block = end < 0 ? text.slice(start) : text.slice(start, end)
  const found: string[] = []
  const seen = new Set<string>()
  MUST_USE_NAME_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = MUST_USE_NAME_RE.exec(block))) {
    const name = (match[1] || '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    found.push(name)
  }
  return found
}

export function uniqueSkillNames(names: string[], boundNames?: string[]): string[] {
  const bound = boundNames?.length ? new Set(boundNames) : null
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of names) {
    const name = (raw || '').trim()
    if (!name || seen.has(name)) continue
    if (bound && !bound.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}
