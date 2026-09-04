import type { EvalTask } from '@/api/evaluation'

export type EvalPhase = 'pending' | 'loading' | 'ingest' | 'eval' | 'agent' | 'ragas' | 'done'

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

const SQUAD_LABELS: Record<string, string> = {
  em: 'Overall EM',
  f1: 'Overall F1',
  span_hit: 'Span Hit',
  has_ans_em: 'HasAns EM',
  has_ans_f1: 'HasAns F1',
  no_ans_acc: 'NoAns Acc',
  abstain_rate: '拒答率',
}

const METRIC_ORDER: Record<string, string[]> = {
  retrieval: ['precision', 'recall', 'mrr', 'ndcg3', 'ndcg10', 'map'],
  generation: ['rouge1', 'rouge2', 'rougel', 'bleu1', 'bleu2', 'bleu4'],
  ragas: ['faithfulness', 'answer_relevancy', 'context_precision', 'context_recall'],
  intent: ['accuracy', 'routing_accuracy', 'macro_f1'],
  squad: ['em', 'f1', 'span_hit', 'has_ans_em', 'has_ans_f1', 'no_ans_acc', 'abstain_rate'],
}

function metricItems(
  metrics: Record<string, number> | undefined,
  labels: Record<string, string>,
  order: string[],
): MetricCardItem[] {
  if (!metrics) return []
  const keys = order.filter((k) => typeof metrics[k] === 'number')
  const extras = Object.keys(metrics).filter(
    (k) => !order.includes(k) && typeof metrics[k] === 'number' && !k.endsWith('_count'),
  )
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
  if (task.status === 'pending') return 'pending'
  if (task.status === 'cancelled') return null
  const phase = task.metric_summary?.phase
  if (
    phase === 'loading' ||
    phase === 'ingest' ||
    phase === 'eval' ||
    phase === 'agent' ||
    phase === 'ragas' ||
    phase === 'done'
  ) {
    return phase
  }
  if (task.status === 'running' && task.suite === 'rag_quality') return 'agent'
  if (task.status === 'running' && task.total > 0) return 'eval'
  if (task.status === 'running') return 'loading'
  if (task.status === 'success') return 'done'
  return null
}

export function phaseLabel(phase: EvalPhase | null): string {
  switch (phase) {
    case 'pending':
      return '排队中'
    case 'loading':
      return '加载数据集'
    case 'ingest':
      return '灌库中'
    case 'eval':
      return '跑题中'
    case 'agent':
      return 'Agent 跑题'
    case 'ragas':
      return 'RAGAS 打分'
    case 'done':
      return '已完成'
    default:
      return ''
  }
}

export function isRagasScoring(task: EvalTask): boolean {
  return task.status === 'running' && taskPhase(task) === 'ragas'
}

export function isActiveTask(task: EvalTask): boolean {
  return task.status === 'pending' || task.status === 'running'
}

function summaryHasMetrics(summary: Record<string, unknown> | null | undefined): boolean {
  if (!summary) return false
  if (summary.result_ready) return true
  return Boolean(
    summary.squad_metrics ||
      summary.ragas_metrics ||
      summary.retrieval_metrics ||
      summary.generation_metrics ||
      summary.intent_metrics,
  )
}

export function hasEvalResult(task: EvalTask): boolean {
  if (task.status === 'success' || task.status === 'failed') return true
  if (task.status === 'cancelled') {
    const sampleCount = Number(task.metric_summary?.sample_count ?? task.finished ?? 0)
    return sampleCount > 0 || summaryHasMetrics(task.metric_summary)
  }
  return summaryHasMetrics(task.metric_summary)
}

export function isQueueTask(task: EvalTask): boolean {
  return isActiveTask(task)
}

export function progressLabel(task: EvalTask): string {
  const phase = taskPhase(task)
  if (task.status === 'cancelled') return '已取消'
  if (phase === 'pending') return '等待 worker…'
  if (phase === 'loading') return '加载数据集…'
  if (phase === 'ingest') {
    const done = Number(task.metric_summary?.ingest_finished ?? task.finished ?? 0)
    const total = Number(task.metric_summary?.ingest_total ?? task.total ?? 0)
    return total > 0 ? `灌库 ${done}/${total} 段` : '灌库中…'
  }
  if (isRagasScoring(task)) {
    const n = task.metric_summary?.ragas_total ?? task.total
    return n ? `RAGAS 打分中（${n} 题）` : 'RAGAS 打分中'
  }
  if (phase === 'agent' && task.suite === 'rag_quality') {
    return `Agent ${task.finished}/${task.total || '?'}`
  }
  if (task.total > 0) return `${task.finished}/${task.total} 题`
  if (task.status === 'running') return '准备中…'
  return '—'
}

export function progressPercentage(task: EvalTask): number {
  const phase = taskPhase(task)
  if (phase === 'pending' || phase === 'loading') return 0
  if (phase === 'ingest') {
    const done = Number(task.metric_summary?.ingest_finished ?? task.finished ?? 0)
    const total = Number(task.metric_summary?.ingest_total ?? task.total ?? 0)
    if (!total) return 0
    return Math.min(99, Math.round((done / total) * 100))
  }
  if (isRagasScoring(task)) return 100
  if (!task.total) return 0
  return Math.min(100, Math.round((task.finished / task.total) * 100))
}

export function progressStatus(
  task: EvalTask,
): 'success' | 'error' | 'warning' | 'active' | undefined {
  if (task.status === 'success') return 'success'
  if (task.status === 'failed') return 'error'
  if (task.status === 'cancelled') return 'warning'
  if (task.status === 'running' || task.status === 'pending') return 'active'
  return undefined
}

export function elapsedLabel(task: EvalTask, nowMs = Date.now()): string {
  if (!task.started_at) {
    if (task.status === 'pending') return '排队中'
    return '—'
  }
  const end = task.finished_at ? new Date(task.finished_at).getTime() : nowMs
  const ms = end - new Date(task.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const rem = sec % 60
  if (min < 60) return `${min}m ${rem}s`
  return `${Math.floor(min / 60)}h ${min % 60}m`
}

export function etaLabel(task: EvalTask, nowMs = Date.now()): string | null {
  if (task.status !== 'running') return null
  const phase = taskPhase(task)
  let done = task.finished
  let total = task.total
  if (phase === 'ingest') {
    done = Number(task.metric_summary?.ingest_finished ?? 0)
    total = Number(task.metric_summary?.ingest_total ?? 0)
  }
  if (!task.started_at || !total || done <= 0 || done >= total) return null
  const elapsed = nowMs - new Date(task.started_at).getTime()
  if (elapsed < 2000) return null
  const remaining = Math.round(((elapsed / done) * (total - done)) / 1000)
  if (remaining < 5) return '<5s'
  if (remaining < 60) return `约 ${remaining}s`
  return `约 ${Math.floor(remaining / 60)}m ${remaining % 60}s`
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

  const sm = summary.squad_metrics as Record<string, number> | undefined
  const squadItems = metricItems(sm, SQUAD_LABELS, METRIC_ORDER.squad)
  if (squadItems.length) {
    groups.push({ id: 'squad', title: 'SQuAD EM/F1', items: squadItems })
  }

  return groups
}

export function hasMetricCards(summary: Record<string, unknown> | null | undefined): boolean {
  return buildMetricCardGroups(summary).some((g) => g.items.length > 0)
}

export function highlightMetric(summary: Record<string, unknown> | null | undefined): string {
  if (!summary) return '—'
  const phase = summary.phase
  if (phase === 'loading') return '加载数据集…'
  if (phase === 'ingest') {
    const done = summary.ingest_finished
    const total = summary.ingest_total
    if (typeof done === 'number' && typeof total === 'number' && total > 0) {
      return `灌库 ${done}/${total}`
    }
    return '灌库中…'
  }
  if (phase === 'ragas') return 'RAGAS 打分中…'
  if (phase === 'agent' && !summary.ragas_metrics && !summary.squad_metrics) {
    return 'Agent 跑题中…'
  }
  const squad = summary.squad_metrics as Record<string, number> | undefined
  if (squad && typeof squad.f1 === 'number') {
    const parts = [`F1 ${formatMetricValue(squad.f1)}`]
    if (typeof squad.no_ans_acc === 'number') parts.push(`NoAns ${formatMetricValue(squad.no_ans_acc)}`)
    return parts.join(' · ')
  }
  const ragas = summary.ragas_metrics as Record<string, number> | undefined
  if (ragas && typeof ragas.faithfulness === 'number') {
    return `忠实度 ${formatMetricValue(ragas.faithfulness)}`
  }
  const intent = summary.intent_metrics as Record<string, number> | undefined
  if (intent && typeof intent.accuracy === 'number') {
    return `准确率 ${formatMetricValue(intent.accuracy)}`
  }
  const ret = summary.retrieval_metrics as Record<string, number> | undefined
  if (ret && typeof ret.recall === 'number') {
    return `召回 ${formatMetricValue(ret.recall)}`
  }
  return formatMetricsSummary(summary)
}

export function taskDurationLabel(task: EvalTask): string {
  if (!task.started_at || !task.finished_at) {
    if (task.status === 'running' || task.status === 'pending') return elapsedLabel(task)
    return '—'
  }
  const ms = new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()
  if (!Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${ms}ms`
  const sec = Math.round(ms / 1000)
  if (sec < 60) return `${sec}s`
  return `${Math.floor(sec / 60)}m ${sec % 60}s`
}

export function primaryBarItems(summary: Record<string, unknown> | null | undefined): MetricCardItem[] {
  return buildMetricCardGroups(summary).flatMap((g) => g.items)
}

export function squadCountPair(summary: Record<string, unknown> | null | undefined): {
  hasAns: number
  noAns: number
} | null {
  const squad = summary?.squad_metrics as Record<string, number> | undefined
  if (!squad) return null
  const hasAns = squad.has_ans_count
  const noAns = squad.no_ans_count
  if (typeof hasAns !== 'number' && typeof noAns !== 'number') return null
  return { hasAns: hasAns || 0, noAns: noAns || 0 }
}

export function formatMetricsSummary(summary: Record<string, unknown> | null | undefined): string {
  const groups = buildMetricCardGroups(summary)
  if (!groups.length) {
    if (!summary) return '—'
    const phase = summary.phase
    if (phase === 'loading') return '加载数据集…'
    if (phase === 'ingest') return '灌库中…'
    if (phase === 'eval') return '跑题中…'
    if (phase === 'agent') return 'Agent 跑题中…'
    if (phase === 'ragas') return 'RAGAS 打分中…'
    return JSON.stringify(summary).slice(0, 80)
  }
  return groups
    .flatMap((g) => g.items.slice(0, 3).map((i) => `${i.label} ${formatMetricValue(i.value)}`))
    .join(' · ')
}
