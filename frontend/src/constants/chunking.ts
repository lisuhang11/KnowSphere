/** 切块策略与父子分块默认值（对齐后端 config/settings.py） */

export type ChunkStrategy = 'auto' | 'heading' | 'heuristic' | 'recursive'

export const STRATEGY_OPTIONS = [
  { value: 'auto', label: 'auto（自适应）', desc: '扫描文档结构自动选择 heading / heuristic / recursive' },
  { value: 'heading', label: 'heading（按标题）', desc: '按 Markdown 标题层级切分，保留面包屑' },
  { value: 'heuristic', label: 'heuristic（启发式）', desc: '按章节编号 / 分页符 / 伪标题等结构边界切分' },
  { value: 'recursive', label: 'recursive（递归字符）', desc: '经典递归字符切分（兜底）' },
] as const

export const TIER_LABELS: Record<string, string> = {
  auto: 'auto（自适应）',
  heading: 'heading · 按标题（Tier1）',
  heuristic: 'heuristic · 启发式边界（Tier2）',
  legacy: 'recursive · 递归字符（Tier3 兜底）',
  recursive: 'recursive · 递归字符',
}

export const CHUNK_DEFAULTS = {
  chunkSize: 600,
  chunkOverlap: 90,
  enableParentChild: false,
  parentChunkSize: 4096,
  childChunkSize: 384,
} as const

export function strategyLabel(v: string): string {
  return STRATEGY_OPTIONS.find((s) => s.value === v)?.label ?? v
}

export function tierLabel(t: string): string {
  return TIER_LABELS[t] ?? t
}
