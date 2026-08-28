import type { EvalTask } from '@/api/evaluation'

export type EvalPhase = 'agent' | 'ragas' | 'done'

export interface MetricCardItem {
  key: string
  label: string
  value: number
}

export interface MetricCardGroup {
  id: string
  title: string
  items: MetricCardItem[]
}

const RAGAS_LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  answer_relevancy: '答案相关性',
  context_precision: '上下文精确度',
  context_recall: '上下文召回',
}

const RETRIEVAL_LABELS: Record<string, string> = {
  precision: '精确率',
  recall: '召回率',
  mrr: 'MRR',
  ndcg3: 'nDCG@3',
  ndcg10: 'nDCG@10',
  map: 'MAP',
}

const GENERATION_LABELS: Record<string, string> = {
  rouge1: 'ROUGE-1',
  rouge2: 'ROUGE-2',
  rougel: 'ROUGE-L',
  bleu1: 'BLEU-1',
  bleu2: 'BLEU-2',
  bleu4: 'BLEU-4',
}

const INTENT_LABELS: Record<string, string> = {
  accuracy: '意图准确率',
  routing_accuracy: '路由准确率',
  macro_f1: 'Macro-F1',
}

const METRIC_ORDER: Record<string, string[]> = {
  retrieval: ['precision', 'recall', 'mrr', 'ndcg3', 'ndcg10', 'map'],
  generation: ['rouge1', 'rouge2', 'rougel', 'bleu1', 'bleu2', 'bleu4'],
  ragas: ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall'],
  intent: ['accuracy', 'routing_accuracy', 'macro_f1'],
}

function metricItems(
  metrics: Record<string, number> | undefined,
  labels: Record<string, string>,
  order: string[],
): MetricCardItem[] {
  if (!metrics) return []
  const keys = order.filter((k) => typeof metrics[k] === 'number')
  const extras = Object.keys(metrics).filter((k) => !order.includes(k) && typeof metrics[k] === 'number')
  return [...keys, ...extras].map((key) => ({
    key,
    label: labels[key] || key,
    value: metrics[key],
  }))
}

export function formatMetricValue(value: number): string {
  if (!Number.isFinite(value)) return '—'
  return value.toFixed(3)
}

export function metricScoreClass(value: number): string {
  if (!Number.isFinite(value)) return ''
  if (value >= 0.75) return 'score-high'
  if (value >= 0.45) return 'score-mid'
  return 'score-low'
}

export function taskPhase(task: EvalTask): EvalPhase | null {
  const phase = task.metric_summary?.phase
  if (phase === 'agent' || phase === 'ragas' || phase === 'done') return phase
  if (task.status === 'running' && task.suite === 'rag_quality') return 'agent'
  if (task.status === 'success') return 'done'
  return null
}

export function isRagasScoring(task: EvalTask): boolean {
  return task.status === 'running' && taskPhase(task) === 'ragas'
}

export function progressLabel(task: EvalTask): string {
  if (isRagasScoring(task)) {
    const n = task.metric_summary?.ragas_total ?? task.total
    return n ? `RAGAS 打分中（${n} 题）` : 'RAGAS 打分中'
  }
  if (taskPhase(task) === 'agent' && task.suite === 'rag_quality') {
    return `Agent ${task.finished}/${task.total}`
  }
  if (task.total > 0) return `${task.finished}/${task.total}`
  return '—'
}

export function progressPercentage(task: EvalTask): number {
  if (isRagasScoring(task)) return 100
  if (!task.total) return 0
  return Math.round((task.finished / task.total) * 100)
}

export function buildMetricCardGroups(summary: Record<string, unknown> | null | undefined): MetricCardGroup[] {
  if (!summary) return []

  const groups: MetricCardGroup[] = []
  const rm = summary.retrieval_metrics as Record<string, number> | undefined
  const gm = summary.generation_metrics as Record<string, number> | undefined
  const rag = summary.ragas_metrics as Record<string, number> | undefined

  const retrievalItems = metricItems(rm, RETRIEVAL_LABELS, METRIC_ORDER.retrieval)
  if (retrievalItems.length) {
    groups.push({ id: 'retrieval', title: '检索指标', items: retrievalItems })
  }

  const generationItems = metricItems(gm, GENERATION_LABELS, METRIC_ORDER.generation)
  if (generationItems.length) {
    groups.push({ id: 'generation', title: '生成指标', items: generationItems })
  }

  const ragasItems = metricItems(rag, RAGAS_LABELS, METRIC_ORDER.ragas)
  if (ragasItems.length) {
    groups.push({ id: 'ragas', title: 'RAGAS 质量', items: ragasItems })
  }

  const im = summary.intent_metrics as Record<string, number> | undefined
  const intentItems = metricItems(im, INTENT_LABELS, METRIC_ORDER.intent)
  if (intentItems.length) {
    groups.push({ id: 'intent', title: '意图识别', items: intentItems })
  }

  return groups
}

export function hasMetricCards(summary: Record<string, unknown> | null | undefined): boolean {
  return buildMetricCardGroups(summary).some((g) => g.items.length > 0)
}

export function formatMetricsSummary(summary: Record<string, unknown> | null | undefined): string {
  const groups = buildMetricCardGroups(summary)
  if (!groups.length) {
    if (!summary) return '—'
    const phase = summary.phase
    if (phase === 'agent') return 'Agent 跑题中…'
    if (phase === 'ragas') return 'RAGAS 打分中…'
    return JSON.stringify(summary).slice(0, 80)
  }
  return groups
    .flatMap((g) => g.items.slice(0, 3).map((i) => `${i.label} ${formatMetricValue(i.value)}`))
    .join(' · ')
}
