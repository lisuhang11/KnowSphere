import hljs from 'highlight.js'

export interface SkillFileNode {
  name: string
  path: string
  isDir: boolean
  children?: SkillFileNode[]
}

export interface SkillFileRow {
  name: string
  path: string
  isDir: boolean
  depth: number
}

export interface SkillFrontmatterField {
  key: string
  value: string
  code: boolean
}

const LANG_MAP: Record<string, string> = {
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  jsx: 'javascript',
  py: 'python',
  rb: 'ruby',
  sh: 'bash',
  bash: 'bash',
  yml: 'yaml',
  yaml: 'yaml',
  md: 'markdown',
  markdown: 'markdown',
  rs: 'rust',
  go: 'go',
  json: 'json',
  xml: 'xml',
  html: 'xml',
  css: 'css',
  sql: 'sql',
  toml: 'ini',
  ini: 'ini',
  conf: 'ini',
}

export function extOf(path: string): string {
  const base = path.split('/').pop() || path
  const idx = base.lastIndexOf('.')
  if (idx < 0) return ''
  return base.slice(idx + 1).toLowerCase()
}

export function isMarkdownPath(path: string): boolean {
  const ext = extOf(path)
  return ext === 'md' || ext === 'markdown'
}

export function skillFileIcon(name: string, isDir: boolean): string {
  if (isDir) return 'folder'
  const ext = extOf(name)
  if (ext === 'md' || ext === 'markdown') return 'file'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'ico', 'bmp'].includes(ext)) return 'image'
  if (['py', 'js', 'ts', 'go', 'sh', 'json', 'yml', 'yaml'].includes(ext)) return 'code'
  return 'file'
}

export function buildSkillFileTree(paths: string[]): SkillFileNode[] {
  const root: SkillFileNode = { name: '', path: '', isDir: true, children: [] }
  for (const raw of paths) {
    const parts = raw.split('/').filter(Boolean)
    let cur = root
    parts.forEach((part, i) => {
      const isLast = i === parts.length - 1
      const nodePath = parts.slice(0, i + 1).join('/')
      if (!cur.children) cur.children = []
      let next = cur.children.find((child) => child.name === part)
      if (!next) {
        next = {
          name: part,
          path: nodePath,
          isDir: !isLast,
          children: isLast ? undefined : [],
        }
        cur.children.push(next)
      } else if (!isLast) {
        next.isDir = true
        if (!next.children) next.children = []
      }
      cur = next
    })
  }
  const sortNodes = (list: SkillFileNode[]) => {
    list.sort((a, b) => {
      if (a.path === 'SKILL.md') return -1
      if (b.path === 'SKILL.md') return 1
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    list.forEach((node) => node.children && sortNodes(node.children))
  }
  sortNodes(root.children || [])
  return root.children || []
}

export function collectSkillDirs(list: SkillFileNode[], out: Set<string>) {
  for (const node of list) {
    if (!node.isDir) continue
    out.add(node.path)
    if (node.children) collectSkillDirs(node.children, out)
  }
}

export function flattenVisibleSkillRows(
  list: SkillFileNode[],
  depth: number,
  expanded: Set<string>,
  out: SkillFileRow[],
) {
  for (const node of list) {
    out.push({ name: node.name, path: node.path, isDir: node.isDir, depth })
    if (node.isDir && expanded.has(node.path) && node.children?.length) {
      flattenVisibleSkillRows(node.children, depth + 1, expanded, out)
    }
  }
}

function formatFrontmatterValue(raw: string): { value: string; code: boolean } {
  let value = raw.trim()
  if (
    (value.startsWith('"') && value.endsWith('"') && value.length >= 2) ||
    (value.startsWith("'") && value.endsWith("'") && value.length >= 2)
  ) {
    value = value.slice(1, -1)
  }
  if ((value.startsWith('{') && value.endsWith('}')) || (value.startsWith('[') && value.endsWith(']'))) {
    try {
      return { value: JSON.stringify(JSON.parse(value), null, 2), code: true }
    } catch {
      return { value, code: true }
    }
  }
  return { value, code: false }
}

function parseFrontmatterFields(raw: string): SkillFrontmatterField[] {
  const fields: SkillFrontmatterField[] = []
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const idx = trimmed.indexOf(':')
    if (idx <= 0) continue
    const key = trimmed.slice(0, idx).trim()
    if (!/^[A-Za-z0-9_-]+$/.test(key)) continue
    const formatted = formatFrontmatterValue(trimmed.slice(idx + 1))
    if (!formatted.value || formatted.value === '>' || formatted.value === '|') continue
    fields.push({ key, ...formatted })
  }
  return fields
}

export function splitMarkdownFrontmatter(text: string): {
  fields: SkillFrontmatterField[]
  body: string
} {
  const src = text.replace(/^\uFEFF/, '')
  const match = src.match(/^(?:[ \t]*\r?\n)*---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/)
  if (!match) return { fields: [], body: src }
  return {
    fields: parseFrontmatterFields(match[1]),
    body: src.slice(match[0].length),
  }
}

export function highlightSkillFile(text: string, path: string): string {
  const ext = extOf(path)
  const lang = LANG_MAP[ext] || ext
  if (lang && hljs.getLanguage(lang)) {
    try {
      return hljs.highlight(text, { language: lang }).value
    } catch {
      /* fall through */
    }
  }
  try {
    return hljs.highlightAuto(text).value
  } catch {
    return text.replace(/[&<>]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch] || ch))
  }
}
