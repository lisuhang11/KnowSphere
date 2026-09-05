import type { EvalSample, EvalTask } from '@/api/evaluation'

export type EvalPhase = 'pending' | 'loading' | 'ingest' | 'eval' | 'agent' | 'ragas' | 'collect_done' | 'done'

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
    phase === 'collect_done' ||
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
    case 'collect_done':
      return '待离线打分'
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
    const done = Number(task.metric_summary?.ragas_finished ?? 0)
    const total = Number(task.metric_summary?.ragas_total ?? 0)
    if (total > 0 && done < total) return `RAGAS 打分中 第 ${done + 1}/${total} 题`
    return total > 0 ? `RAGAS 打分 ${done}/${total}` : 'RAGAS 打分中'
  }
  if (phase === 'agent' && task.suite === 'rag_quality') {
    const retryTotal = Number(task.metric_summary?.retry_total ?? 0)
    const retryDone = Number(task.metric_summary?.retry_finished ?? 0)
    if (retryTotal > 0) return `重试失败题 ${retryDone}/${retryTotal}`
    return `Agent ${task.finished}/${task.total || '?'}`
  }
  if (Number(task.metric_summary?.retry_total ?? 0) > 0 && task.status === 'running') {
    const retryTotal = Number(task.metric_summary?.retry_total ?? 0)
    const retryDone = Number(task.metric_summary?.retry_finished ?? 0)
    return `重试失败题 ${retryDone}/${retryTotal}`
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
  if (isRagasScoring(task)) {
    const done = Number(task.metric_summary?.ragas_finished ?? 0)
    const total = Number(task.metric_summary?.ragas_total ?? 0)
    if (!total) return 99
    return Math.min(99, Math.round((done / total) * 100))
  }
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
  const end =
    task.status === 'running' || !task.finished_at ? nowMs : new Date(task.finished_at).getTime()
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
  if (isRagasScoring(task)) {
    done = Number(task.metric_summary?.ragas_finished ?? task.finished ?? 0)
    total = Number(task.metric_summary?.ragas_total ?? task.total ?? 0)
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
  if (phase === 'collect_done') return '待 RAGAS 打分'
  if (phase === 'ragas') return 'RAGAS 打分中…'
  if (typeof summary.ragas_error === 'string' && summary.ragas_error) {
    return 'RAGAS 打分失败'
  }
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
    if (phase === 'collect_done') return '待 RAGAS 打分'
    return JSON.stringify(summary).slice(0, 80)
  }
  return groups
    .flatMap((g) => g.items.slice(0, 3).map((i) => `${i.label} ${formatMetricValue(i.value)}`))
    .join(' · ')
}

export type SampleVerdictKind =
  | 'correct'
  | 'wrong_answer'
  | 'false_abstain'
  | 'false_answer'
  | 'intent_wrong'
  | 'retrieval_miss'
  | 'run_error'
  | 'unscored'

export type SampleFilter =
  | 'issues'
  | 'all'
  | 'false_abstain'
  | 'wrong_answer'
  | 'false_answer'
  | 'intent_wrong'
  | 'retrieval_miss'
  | 'run_error'
  | 'unscored'
  | 'correct'

export interface SampleVerdict {
  kind: SampleVerdictKind
  ok: boolean
  label: string
  theme: 'success' | 'danger' | 'warning' | 'default'
  reason: string
}

export interface IssueStats {
  total: number
  ok: number
  wrong: number
  errors: number
  unscored: number
}

const VERDICT_RANK: Record<SampleVerdictKind, number> = {
  run_error: 0,
  false_abstain: 1,
  wrong_answer: 2,
  false_answer: 3,
  intent_wrong: 4,
  retrieval_miss: 5,
  unscored: 8,
  correct: 9,
}

function hasNumericScores(metrics: Record<string, unknown> | undefined): boolean {
  if (!metrics) return false
  return Object.values(metrics).some((v) => typeof v === 'number' && Number.isFinite(v))
}

function metricRecord(metrics: Record<string, unknown> | null | undefined, key: string) {
  const value = metrics?.[key]
  if (value && typeof value === 'object') return value as Record<string, unknown>
  return undefined
}

export function retrievalHit(row: EvalSample): boolean | null {
  const gt = row.retrieval_gt || []
  const ids = row.retrieval_ids || []
  if (!gt.length) return null
  return gt.some((id) => ids.includes(id))
}

function retrievalReason(row: EvalSample): string {
  const hit = retrievalHit(row)
  const gt = (row.retrieval_gt || []).join(', ')
  if (hit === true) return `检索已命中应召回段落${gt ? ` ${gt}` : ''}。`
  if (hit === false) return `检索未命中应召回段落${gt ? ` ${gt}` : ''}。`
  return ''
}

export function classifySample(row: EvalSample): SampleVerdict {
  if (row.error) {
    return {
      kind: 'run_error',
      ok: false,
      label: '运行出错',
      theme: 'danger',
      reason: row.error,
    }
  }

  const squad = metricRecord(row.metrics, 'squad')
  const intent = metricRecord(row.metrics, 'intent')
  const ragas = metricRecord(row.metrics, 'ragas')

  if (ragas && !squad && !intent) {
    if (hasNumericScores(ragas)) {
      const faith = ragas.faithfulness
      const rel = ragas.answer_relevancy
      const parts = [
        typeof faith === 'number' ? `忠实度 ${Number(faith).toFixed(3)}` : '',
        typeof rel === 'number' ? `相关性 ${Number(rel).toFixed(3)}` : '',
      ].filter(Boolean)
      return {
        kind: 'correct',
        ok: true,
        label: '已打分',
        theme: 'success',
        reason: parts.join(' · ') || 'RAGAS 分数已写出。',
      }
    }
    const ragasError = typeof row.details?.ragas_error === 'string' ? row.details.ragas_error.trim() : ''
    return {
      kind: 'unscored',
      ok: false,
      label: '未打分',
      theme: 'warning',
      reason: ragasError
        ? `RAGAS 打分失败：${ragasError}`
        : 'Agent 已作答，尚未写出 RAGAS 分数。可在结果页选择评分模型后离线打分。',
    }
  }

  if (intent && !squad) {
    return classifyIntent(row, intent)
  }

  if (squad) {
    const impossible = Number(squad.impossible || 0) >= 1
    const abstained = Number(squad.abstained || 0) >= 1
    const em = Number(squad.em || 0)
    const hitNote = retrievalReason(row)

    if (impossible) {
      if (em >= 1 || abstained) {
        return {
          kind: 'correct',
          ok: true,
          label: '正确拒答',
          theme: 'success',
          reason: '这是不可答题，系统拒绝作答，判为正确。',
        }
      }
      return {
        kind: 'false_answer',
        ok: false,
        label: '不该答却答了',
        theme: 'danger',
        reason: `这是不可答题，系统却给出了答案。${hitNote}`.trim(),
      }
    }

    if (abstained) {
      return {
        kind: 'false_abstain',
        ok: false,
        label: '该答却拒答',
        theme: 'warning',
        reason: `题目有标准答案，但系统输出了拒答。${hitNote}`.trim(),
      }
    }

    if (em >= 1) {
      return {
        kind: 'correct',
        ok: true,
        label: '答对',
        theme: 'success',
        reason: hitNote || '答案与正确答案一致。',
      }
    }

    const f1 = Number(squad.f1 || 0)
    if (f1 > 0) {
      return {
        kind: 'wrong_answer',
        ok: false,
        label: '部分匹配',
        theme: 'warning',
        reason: `尚未完全匹配（F1 ${f1.toFixed(3)}）。${hitNote}`.trim(),
      }
    }

    return {
      kind: 'wrong_answer',
      ok: false,
      label: '答案不对',
      theme: 'danger',
      reason: `系统答案与正确答案不一致。${hitNote}`.trim(),
    }
  }

  if (intent) {
    return classifyIntent(row, intent)
  }

  if (retrievalHit(row) === false) {
    return {
      kind: 'retrieval_miss',
      ok: false,
      label: '检索未中',
      theme: 'warning',
      reason: retrievalReason(row) || '应召回段落未出现在系统召回中。',
    }
  }

  return {
    kind: 'correct',
    ok: true,
    label: '已完成',
    theme: 'success',
    reason: retrievalHit(row) === true ? retrievalReason(row) : '本题没有可判定的对错标签。',
  }
}

function classifyIntent(row: EvalSample, intent: Record<string, unknown>): SampleVerdict {
  const ok = intent.correct === 1 || intent.correct === 1.0
  const gt = String(intent.intent_gt ?? '')
  const pred = String(intent.pred_intent ?? row.response ?? '')
  if (ok) {
    return {
      kind: 'correct',
      ok: true,
      label: '意图正确',
      theme: 'success',
      reason: gt && pred ? `${gt} → ${pred}` : '意图识别正确。',
    }
  }
  return {
    kind: 'intent_wrong',
    ok: false,
    label: '意图判错',
    theme: 'danger',
    reason: gt ? `应为 ${gt}，判成 ${pred || '（空）'}。` : '意图识别与标注不一致。',
  }
}

export function sampleFilterMatch(row: EvalSample, filter: SampleFilter): boolean {
  const verdict = classifySample(row)
  if (filter === 'all') return true
  if (filter === 'issues') return !verdict.ok && verdict.kind !== 'unscored'
  if (filter === 'correct') return verdict.ok
  if (filter === 'retrieval_miss') return retrievalHit(row) === false
  return verdict.kind === filter
}

export function sortSamplesByIssue(rows: EvalSample[]): EvalSample[] {
  return [...rows].sort((a, b) => {
    const rank = VERDICT_RANK[classifySample(a).kind] - VERDICT_RANK[classifySample(b).kind]
    if (rank !== 0) return rank
    return a.qid - b.qid
  })
}

export function formatGoldLabel(row: EvalSample): string {
  const squad = metricRecord(row.metrics, 'squad')
  const empty = !(row.reference || '').trim()
  if (empty && Number(squad?.impossible || 0) >= 1) return '（不可答题，无标准答案）'
  return empty ? '（空）' : String(row.reference)
}

export function formatPredLabel(row: EvalSample): string {
  if (row.error) return row.error
  const text = (row.response || '').trim()
  return text || '（空）'
}

export function estimateIssueStats(
  summary: Record<string, unknown> | null | undefined,
  fallbackTotal = 0,
): IssueStats {
  const empty = { total: fallbackTotal, ok: 0, wrong: 0, errors: 0, unscored: 0 }
  if (!summary) return empty
  const errors = Number(summary.error_count || 0)
  const sampleCount = Number(summary.sample_count || 0)
  const squad = summary.squad_metrics as Record<string, number> | undefined
  if (squad && (typeof squad.has_ans_count === 'number' || typeof squad.no_ans_count === 'number')) {
    const hasAns = Number(squad.has_ans_count || 0)
    const noAns = Number(squad.no_ans_count || 0)
    const wrongHas = Math.round(hasAns * (1 - Number(squad.has_ans_em || 0)))
    const wrongNo = Math.round(noAns * (1 - Number(squad.no_ans_acc || 0)))
    const wrong = Math.max(0, wrongHas + wrongNo)
    const scored = hasAns + noAns
    return {
      total: scored + errors || fallbackTotal,
      ok: Math.max(0, scored - wrong),
      wrong,
      errors,
      unscored: 0,
    }
  }
  const intent = summary.intent_metrics as Record<string, number> | undefined
  if (intent && typeof intent.accuracy === 'number' && sampleCount) {
    const ok = Math.round(sampleCount * intent.accuracy)
    return {
      total: sampleCount + errors || fallbackTotal,
      ok,
      wrong: Math.max(0, sampleCount - ok),
      errors,
      unscored: 0,
    }
  }
  const ragas = summary.ragas_metrics as Record<string, number> | undefined
  const ragasScored = ragas && typeof ragas.faithfulness === 'number'
  const waitingRagas =
    summary.phase === 'collect_done' ||
    summary.phase === 'ragas' ||
    Boolean(summary.ragas_error) ||
    Boolean(ragas)
  if (!ragasScored && waitingRagas) {
    return {
      total: sampleCount + errors || fallbackTotal,
      ok: 0,
      wrong: 0,
      errors,
      unscored: sampleCount,
    }
  }
  return {
    total: sampleCount + errors || fallbackTotal,
    ok: sampleCount,
    wrong: 0,
    errors,
    unscored: 0,
  }
}

export function taskIssueStats(task: EvalTask, samples?: EvalSample[]): IssueStats {
  if (samples?.length) {
    let ok = 0
    let wrong = 0
    let errors = 0
    let unscored = 0
    for (const row of samples) {
      const verdict = classifySample(row)
      if (verdict.kind === 'run_error') errors += 1
      else if (verdict.kind === 'unscored') unscored += 1
      else if (verdict.ok) ok += 1
      else wrong += 1
    }
    return { total: samples.length, ok, wrong, errors, unscored }
  }
  return estimateIssueStats(task.metric_summary, task.finished || task.total || 0)
}
