/** 模型类型展示 */

import type { ModelType } from '@/api/models'

export type ModelFilter = 'all' | ModelType

export const MODEL_TYPE_ORDER: ModelType[] = [
  'KnowledgeQA',
  'Embedding',
  'Rerank',
  'VLLM',
  'ASR',
]

/** 主 Tab 展示顺序 */
export const MODEL_MAIN_TYPES: ModelType[] = [
  'KnowledgeQA',
  'Embedding',
  'Rerank',
  'VLLM',
]

export const TYPE_LABELS: Record<ModelType, string> = {
  KnowledgeQA: '问答',
  Embedding: '向量化',
  Rerank: '重排',
  VLLM: '视觉理解',
  ASR: '语音识别',
}

export const TYPE_DESCRIPTIONS: Record<ModelType, string> = {
  KnowledgeQA: '对话与 RAG 回答',
  Embedding: '文档向量化与检索',
  Rerank: '检索结果精排',
  VLLM: '聊天图片 / 附件 OCR 与多模态理解',
  ASR: '音频转写：知识库入库与对话附件语音识别',
}

export const TYPE_ICONS: Record<ModelType, string> = {
  KnowledgeQA: 'chat',
  Embedding: 'chart-bubble',
  Rerank: 'filter-sort',
  VLLM: 'image',
  ASR: 'sound',
}

export const TYPE_CARD_CLASS: Record<ModelType, string> = {
  KnowledgeQA: 'model-card--chat',
  Embedding: 'model-card--embedding',
  Rerank: 'model-card--rerank',
  VLLM: 'model-card--vllm',
  ASR: 'model-card--asr',
}

export function typeLabel(t: ModelType): string {
  return TYPE_LABELS[t] ?? t
}

export function typeDescription(t: ModelType): string {
  return TYPE_DESCRIPTIONS[t] ?? ''
}

export function sourceLabel(source: string): string {
  if (source === 'local') return '本地'
  if (source === 'remote') return '远程'
  return source
}

export function providerChipLabel(
  m: { source: string; provider?: string; provider_name?: string },
  providers: { id?: string; source?: string; name: string }[],
): string {
  if (m.source === 'local') return '本地 · Ollama'
  const pid = m.provider || ''
  const p = providers.find((x) => x.id === pid || x.source === pid)
  const vendor = m.provider_name || p?.name || pid || '远程'
  return `远程 · ${vendor}`
}

export function modelSummary(m: { parameters?: Record<string, unknown> }): string {
  const p = m.parameters ?? {}
  const parts: string[] = []
  if (p.model) parts.push(String(p.model))
  if (p.base_url) parts.push(String(p.base_url))
  if (p.supports_vision === true) parts.push('支持视觉')
  return parts.join(' · ') || '—'
}
