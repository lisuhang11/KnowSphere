<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import {
  cancelEvalTask,
  createEvalTask,
  produceEvalResults,
  startRagasScore,
  retryEvalFailed,
  deleteEvalDataset,
  downloadEvalDataset,
  deleteEvalTask,
  getEvalDataset,
  getEvalTask,
  listEvalDatasetContexts,
  listEvalDatasets,
  listEvalSamples,
  listEvalTasks,
  listSquadV2Articles,
  patchEvalDataset,
  syncSquadV2Dataset,
  uploadEvalDataset,
  type CreateEvalPayload,
  type EvalDatasetContextsPage,
  type EvalDatasetInfo,
  type EvalDatasetPreview,
  type EvalSample,
  type EvalTask,
  type SquadV2Article,
} from '@/api/evaluation'
import EvalMetricBars from '@/components/EvalMetricBars.vue'
import EvalMetricCards from '@/components/EvalMetricCards.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import { listModels, type ModelInfo } from '@/api/models'
import { selectInitialModelId, modelDisplayName } from '@/utils/modelDefaults'
import {
  classifySample,
  elapsedLabel,
  etaLabel,
  formatGoldLabel,
  formatMetricValue,
  formatPredLabel,
  highlightMetric,
  isActiveTask,
  hasEvalResult,
  isRagasScoring,
  phaseLabel,
  progressLabel,
  progressPercentage,
  progressStatus,
  retrievalHit,
  sampleFilterMatch,
  sortSamplesByIssue,
  taskDurationLabel,
  taskIssueStats,
  taskPhase,
  type SampleFilter,
  type SampleVerdict,
} from '@/utils/evalMetrics'

const loading = ref(false)
const activeTab = ref<'datasets' | 'tasks' | 'results'>('datasets')
const tasks = ref<EvalTask[]>([])
const datasets = ref<EvalDatasetInfo[]>([])

const createVisible = ref(false)
const uploadVisible = ref(false)
const detailVisible = ref(false)
const previewVisible = ref(false)
const editVisible = ref(false)
const detailTask = ref<EvalTask | null>(null)
const detailSamples = ref<EvalSample[]>([])
const sampleTotal = ref(0)
const preview = ref<EvalDatasetPreview | null>(null)
const previewLoading = ref(false)
const previewDatasetId = ref('')
const contextPage = ref<EvalDatasetContextsPage | null>(null)
const contextLoading = ref(false)
const contextLoadError = ref('')
const contextPageNum = ref(1)
const contextPageSize = ref(3)
const squadTitleFilter = ref<string | null>(null)
const squadArticles = ref<SquadV2Article[]>([])
const squadSyncing = ref(false)

const form = ref<CreateEvalPayload>({
  dataset_id: 'campus_demo',
  suite: 'rag_bench',
  pipeline_profile: 'rag_agent',
  sample_limit: undefined,
  workers: 2,
  ragas_model_id: undefined,
})
const evalModels = ref<ModelInfo[]>([])

const uploadOverwrite = ref(false)
const uploadExample = `{
  "title": "Normans",
  "paragraphs": [
    {
      "context": "The Normans were the people who in the 10th and 11th centuries...",
      "qas": [
        {
          "question": "In what country is Normandy located?",
          "id": "56ddde6b9a695914005b9628",
          "answers": [{ "text": "France", "answer_start": 159 }],
          "is_impossible": false
        }
      ]
    }
  ]
}`

const editForm = ref({ id: '', description: '', source: '' })
const sampleFilter = ref<SampleFilter>('issues')
const samplePage = ref(1)
const samplePageSize = ref(20)
const expandedQids = ref<(string | number)[]>([])

let pollTimer: ReturnType<typeof setInterval> | null = null
let clockTimer: ReturnType<typeof setInterval> | null = null
const nowMs = ref(Date.now())

const hasRunning = computed(() => tasks.value.some((t) => isActiveTask(t)))
const activeTasks = computed(() => tasks.value.filter((t) => isActiveTask(t)))
const resultTasks = computed(() => tasks.value.filter((t) => !isActiveTask(t)))
const cancellingIds = ref<Record<string, boolean>>({})
const producingIds = ref<Record<string, boolean>>({})
const scoringIds = ref<Record<string, boolean>>({})
const retryingIds = ref<Record<string, boolean>>({})
const scoreVisible = ref(false)
const scoreTarget = ref<EvalTask | null>(null)
const scoreModelId = ref<string | undefined>()

const statusTheme = (s: string, partial = false) => {
  if (s === 'success') return partial ? 'warning' : 'success'
  if (s === 'failed') return 'danger'
  if (s === 'cancelled') return 'default'
  if (s === 'running') return 'warning'
  if (s === 'pending') return 'default'
  return 'default'
}

const statusLabel = (s: string, partial = false) => {
  if (s === 'success' && partial) return '部分结果'
  const map: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }
  return map[s] || s
}

function hasRagasMetrics(t: EvalTask) {
  const rag = t.metric_summary?.ragas_metrics
  return Boolean(rag && typeof rag === 'object' && Object.keys(rag as object).length)
}

function canScoreRagas(t: EvalTask) {
  if (t.suite !== 'rag_quality') return false
  if (t.status === 'running' || t.status === 'pending') return false
  const n = Number(t.metric_summary?.sample_count ?? t.metric_summary?.ragas_pending ?? t.finished ?? 0)
  return n > 0
}

function isRetryableSample(task: EvalTask | null | undefined, row: EvalSample): boolean {
  if (row.error) return true
  if (task?.suite === 'rag_quality' && !(row.response || '').trim()) return true
  return false
}

function retryableCount(task: EvalTask, samples?: EvalSample[]): number {
  if (samples?.length) return samples.filter((row) => isRetryableSample(task, row)).length
  return Number(task.metric_summary?.error_count ?? 0)
}

function canRetryFailed(t: EvalTask, samples?: EvalSample[]) {
  if (t.status === 'running' || t.status === 'pending') return false
  return retryableCount(t, samples) > 0
}

function taskStatusLabel(t: EvalTask) {
  if (t.status === 'success' && t.suite === 'rag_quality' && !hasRagasMetrics(t)) return '待 RAGAS 打分'
  return statusLabel(t.status, isPartialResult(t))
}

function taskStatusTheme(t: EvalTask) {
  if (taskStatusLabel(t) === '待 RAGAS 打分') return 'warning'
  return statusTheme(t.status, isPartialResult(t))
}

function isPartialResult(t: EvalTask) {
  return Boolean(t.metric_summary?.partial)
}

function canProduceResult(t: EvalTask) {
  if (t.suite === 'rag_quality') return false
  return t.status === 'running' || t.status === 'cancelled' || (t.status === 'pending' && t.finished > 0)
}

function sampleContexts(row: EvalSample): string[] {
  const ctx = row.details?.retrieved_contexts
  return Array.isArray(ctx) ? ctx.filter((item) => typeof item === 'string' && item.trim()) : []
}

const suiteLabel = (s: string) => {
  if (s === 'rag_quality') return 'RAGAS 质量'
  if (s === 'intent_bench') return '意图识别'
  return 'rag_bench'
}

const createDatasetHint = computed(() => {
  if (form.value.suite === 'intent_bench') {
    return '意图评测仅调用 query_understand，无需灌库。请选择含 intent_gt 的数据集（如 intent_demo）。'
  }
  if (form.value.dataset_id.startsWith('squad')) {
    return 'SQuAD 2.0 使用 EM/F1 与不可答题拒答率。抽样按段落整抽：抽中一段正文则保留该段全部问答，不会从同一段里拆一半题；实际题数可能略多于或略少于填写值。'
  }
  if (form.value.suite === 'rag_quality') {
    if (form.value.dataset_id === 'hotpot') {
      return '先跑 RAG 收集 question / 检索片段 / 回答 / 标准答案（需网络加载 HotpotQA）。收集完成后再选评分模型做离线 RAGAS。建议抽样 10–50 题。'
    }
    return '先跑 RAG 收集 question / 检索片段 / 回答 / 标准答案。收集完成后再到结果页选择评分模型，离线打 RAGAS 分。'
  }
  return ''
})

const filteredDatasets = computed(() => {
  if (form.value.suite === 'intent_bench') {
    const intentOnly = datasets.value.filter((d) => d.kind === 'intent' || d.id.includes('intent'))
    return intentOnly.length ? intentOnly : datasets.value
  }
  return datasets.value.filter((d) => d.kind !== 'intent')
})

const detailSampleCount = computed(() => {
  if (sampleTotal.value) return sampleTotal.value
  const n = detailTask.value?.metric_summary?.sample_count
  return typeof n === 'number' ? n : detailSamples.value.length || null
})

const historyTasks = computed(() => {
  const ds = detailTask.value?.dataset_id
  if (!ds) return []
  return tasks.value.filter((t) => t.dataset_id === ds && hasEvalResult(t)).slice(0, 8)
})

function sampleVerdict(row: EvalSample): SampleVerdict {
  return classifySample(row)
}

function isIntentSample(row: EvalSample) {
  return Boolean(row.metrics?.intent)
}

function hasRetrieval(row: EvalSample) {
  return Boolean((row.retrieval_ids && row.retrieval_ids.length) || (row.retrieval_gt && row.retrieval_gt.length))
}

function issueStats(task: EvalTask) {
  return taskIssueStats(task)
}

function resultIssueCount(task: EvalTask) {
  const stats = issueStats(task)
  return stats.wrong + stats.errors
}

function resultIssueLine(task: EvalTask) {
  const stats = issueStats(task)
  const issues = stats.wrong + stats.errors
  const total = stats.total || task.finished || '?'
  if (issues > 0 && stats.unscored) return `出错 ${stats.errors} · 未打分 ${stats.unscored} / 共 ${total}`
  if (issues > 0) return `答错 ${issues} 题 · 答对 ${stats.ok} / 共 ${total}`
  if (stats.unscored) return `未打分 ${stats.unscored} / 共 ${total}`
  if (stats.total) return `全部答对 · ${stats.total} 题`
  return '暂无逐题统计'
}

const detailIssueStats = computed(() => {
  if (!detailTask.value) return { total: 0, ok: 0, wrong: 0, errors: 0, unscored: 0 }
  return taskIssueStats(detailTask.value, detailSamples.value)
})

const verdictChips = computed(() => {
  const rows = detailSamples.value
  const count = (filter: SampleFilter) => rows.filter((row) => sampleFilterMatch(row, filter)).length
  const chips: Array<{ value: SampleFilter; label: string; count: number; tone: string }> = [
    { value: 'issues', label: '答错', count: count('issues'), tone: 'danger' },
    { value: 'false_abstain', label: '该答却拒答', count: count('false_abstain'), tone: 'warning' },
    { value: 'wrong_answer', label: '答案不对', count: count('wrong_answer'), tone: 'danger' },
    { value: 'false_answer', label: '不该答却答了', count: count('false_answer'), tone: 'danger' },
    { value: 'intent_wrong', label: '意图判错', count: count('intent_wrong'), tone: 'danger' },
    { value: 'retrieval_miss', label: '检索未中', count: count('retrieval_miss'), tone: 'warning' },
    { value: 'run_error', label: '运行出错', count: count('run_error'), tone: 'danger' },
    { value: 'unscored', label: '未打分', count: count('unscored'), tone: 'warning' },
    { value: 'correct', label: '答对', count: count('correct'), tone: 'success' },
    { value: 'all', label: '全部', count: rows.length, tone: 'default' },
  ]
  return chips.filter((chip) => chip.value === 'issues' || chip.value === 'all' || chip.value === 'correct' || chip.count > 0)
})

const detailHeader = computed(() => {
  const task = detailTask.value
  if (!task) return '任务详情'
  const stats = detailIssueStats.value
  const issues = stats.wrong + stats.errors
  if (issues > 0 && stats.unscored) return `${task.dataset_id} · 出错 ${stats.errors} · 未打分 ${stats.unscored}`
  if (issues > 0) return `${task.dataset_id} · 答错 ${issues} 题`
  if (stats.unscored) return `${task.dataset_id} · 未打分 ${stats.unscored} 题`
  if (stats.total) return `${task.dataset_id} · 全部答对`
  return task.dataset_id || task.id
})

const filteredSamples = computed(() => {
  const rows = detailSamples.value.filter((row) => sampleFilterMatch(row, sampleFilter.value))
  return sortSamplesByIssue(rows)
})

function sampleRowClassName({ row }: { row: EvalSample }) {
  const verdict = sampleVerdict(row)
  if (verdict.ok || verdict.kind === 'unscored') return ''
  return 'sample-row-issue'
}

function setSampleFilter(filter: SampleFilter) {
  sampleFilter.value = filter
}

const sampleEmptyText = computed(() => {
  if (sampleFilter.value === 'unscored' && sampleTotal.value) {
    return '没有未打分的题目。可点「全部」查看逐题明细。'
  }
  if (sampleFilter.value === 'issues' && sampleTotal.value) {
    return '这批题里没有答错的。可点「全部」查看逐题明细。'
  }
  return '暂无逐题明细'
})

const sampleColumns = computed(() => {
  const intent = detailTask.value?.suite === 'intent_bench'
  return [
    { colKey: 'qid', title: '#', width: 48 },
    { colKey: 'verdict', title: '判定', width: 132 },
    { colKey: 'question', title: '问题', minWidth: 140, ellipsis: true },
    { colKey: 'gold', title: intent ? '标注意图' : '正确答案', minWidth: 140 },
    { colKey: 'pred', title: intent ? '识别结果' : '系统答案', minWidth: 180 },
    { colKey: 'latency', title: '耗时', width: 80 },
    { colKey: 'sampleMetrics', title: '指标', width: 160 },
  ]
})

const pagedSamples = computed(() => {
  const start = (samplePage.value - 1) * samplePageSize.value
  return filteredSamples.value.slice(start, start + samplePageSize.value)
})

function historyHighlight(row: EvalTask) {
  return highlightMetric(row.metric_summary)
}

function progressPct(t: EvalTask) {
  return progressPercentage(t)
}

function taskEta(t: EvalTask) {
  return etaLabel(t, nowMs.value)
}

function taskElapsed(t: EvalTask) {
  return elapsedLabel(t, nowMs.value)
}

function taskPhaseText(t: EvalTask) {
  return phaseLabel(taskPhase(t))
}

function openTaskDetail(t: EvalTask) {
  onRowClick({ row: t })
}

function evalKbId(t: EvalTask): number | null {
  const fromTask = t.eval_kb_id
  if (typeof fromTask === 'number' && fromTask > 0) return fromTask
  const fromSummary = t.metric_summary?.eval_kb_id
  if (typeof fromSummary === 'number' && fromSummary > 0) return fromSummary
  return null
}

function evalKbName(t: EvalTask): string {
  const name = t.metric_summary?.eval_kb_name
  return typeof name === 'string' ? name : ''
}

function snapshotModelLabel(task: EvalTask, key: 'chat_model_id' | 'embedding_model_id' | 'ragas_model_id') {
  const id = task.config_snapshot?.[key]
  if (typeof id !== 'string' || !id.trim()) return '系统默认'
  const rec = evalModels.value.find((m) => m.id === id)
  return rec ? modelDisplayName(rec) : id
}

function formatSampleMetrics(row: EvalSample): string {
  const metrics = row.metrics
  if (!metrics) return '—'
  const rag = metrics.ragas as Record<string, number> | undefined
  const ret = metrics.retrieval as Record<string, number> | undefined
  const squad = metrics.squad as Record<string, number> | undefined
  const intent = metrics.intent as Record<string, unknown> | undefined
  const parts: string[] = []
  if (intent) {
    const pred = intent.pred_intent ?? ''
    const gt = intent.intent_gt ?? ''
    const ok = intent.correct === 1 || intent.correct === 1.0
    const routeOk = intent.routing_correct === 1 || intent.routing_correct === 1.0
    parts.push(`${ok ? '✓' : '✗'} ${gt}→${pred}`)
    parts.push(`路由${routeOk ? '✓' : '✗'}`)
  }
  if (ret) parts.push(`P ${formatMetricValue(ret.precision)} R ${formatMetricValue(ret.recall)}`)
  if (squad) {
    parts.push(`EM ${formatMetricValue(squad.em)} F1 ${formatMetricValue(squad.f1)}`)
    if (typeof squad.span_hit === 'number') parts.push(`span ${formatMetricValue(squad.span_hit)}`)
  }
  const gen = metrics.generation as Record<string, number> | undefined
  if (gen) parts.push(`R1 ${formatMetricValue(gen.rouge1)}`)
  if (rag && Object.keys(rag).length) {
    parts.push(
      Object.entries(rag)
        .map(([k, v]) => `${k.slice(0, 4)} ${formatMetricValue(v)}`)
        .join(' '),
    )
  } else if (metrics.ragas && typeof metrics.ragas === 'object') {
    const err = row.details?.ragas_error
    parts.push(err ? `RAGAS 失败：${err}` : 'RAGAS 未打分')
  }
  return parts.join(' · ') || JSON.stringify(metrics).slice(0, 60)
}

watch(
  () => form.value.suite,
  (suite) => {
    if (suite === 'rag_quality' && form.value.dataset_id === 'campus_demo') {
      const hotpot = datasets.value.find((d) => d.id === 'hotpot')
      if (hotpot) form.value.dataset_id = 'hotpot'
    }
    if (suite === 'intent_bench') {
      const intentDs = datasets.value.find((d) => d.kind === 'intent' || d.id === 'intent_demo')
      if (intentDs) form.value.dataset_id = intentDs.id
      form.value.pipeline_profile = 'intent'
    } else if (form.value.pipeline_profile === 'intent') {
      form.value.pipeline_profile = 'rag_agent'
    }
    if (suite === 'rag_quality') {
      form.value.workers = 1
    }
  },
)

watch(sampleFilter, () => {
  samplePage.value = 1
})

function onSquadArticleFilter() {
  contextPageNum.value = 1
  void loadContextPage()
}

async function loadTasks(opts: { silent?: boolean } = {}) {
  if (!opts.silent) loading.value = true
  try {
    const data = await listEvalTasks()
    tasks.value = data.items
  } finally {
    if (!opts.silent) loading.value = false
  }
}

async function loadDatasets() {
  datasets.value = await listEvalDatasets()
  if (!datasets.value.find((d) => d.id === form.value.dataset_id) && datasets.value.length) {
    form.value.dataset_id = datasets.value[0].id
  }
}

async function loadEvalModels() {
  try {
    evalModels.value = await listModels()
    if (!form.value.ragas_model_id) {
      form.value.ragas_model_id = selectInitialModelId(evalModels.value, 'KnowledgeQA') || undefined
    }
  } catch {
    evalModels.value = []
  }
}

async function submitCreate() {
  try {
    const payload: CreateEvalPayload = {
      ...form.value,
      pipeline_profile: form.value.suite === 'intent_bench' ? 'intent' : 'rag_agent',
    }
    if (payload.suite === 'rag_quality' && !payload.sample_limit) {
      payload.sample_limit = 10
    }
    delete payload.ragas_model_id
    delete payload.chat_model_id
    delete payload.embedding_model_id
    await createEvalTask(payload)
    MessagePlugin.success('评测任务已创建，请确保 Celery worker 已启动')
    createVisible.value = false
    activeTab.value = 'tasks'
    await loadTasks()
    startPolling()
    startClock()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

function onUploadFile(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    uploadJson.value = String(reader.result || '')
  }
  reader.readAsText(file)
}

async function submitUpload() {
  try {
    const parsed = JSON.parse(uploadJson.value || uploadExample)
    if (uploadDescription.value.trim()) parsed.description = uploadDescription.value.trim()
    if (uploadSource.value.trim()) parsed.source = uploadSource.value.trim()
    parsed.overwrite = uploadOverwrite.value
    const saved = await uploadEvalDataset(parsed)
    MessagePlugin.success(`数据集 ${saved.id} 已保存`)
    uploadVisible.value = false
    uploadJson.value = ''
    uploadDescription.value = ''
    uploadSource.value = ''
    uploadOverwrite.value = false
    await loadDatasets()
    form.value.dataset_id = saved.id
    activeTab.value = 'datasets'
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

function useDataset(d: EvalDatasetInfo) {
  form.value.dataset_id = d.id
  if (d.kind === 'intent') {
    form.value.suite = 'intent_bench'
    form.value.pipeline_profile = 'intent'
  } else if (form.value.suite === 'intent_bench') {
    form.value.suite = 'rag_bench'
    form.value.pipeline_profile = 'rag_agent'
  }
  createVisible.value = true
}

const contextTotalPages = computed(() => {
  const total = contextPage.value?.total_contexts ?? 0
  const size = contextPageSize.value || 1
  return Math.max(1, Math.ceil(total / size))
})

function formatQaAnswer(qa: { answer?: string; answers?: string[]; is_impossible?: boolean }) {
  if (qa.is_impossible) return null
  const text = (qa.answer || '').trim() || (qa.answers || []).filter(Boolean).join(' / ')
  return text || '—'
}

async function loadContextPage() {
  if (!previewDatasetId.value) return
  contextLoading.value = true
  contextLoadError.value = ''
  try {
    const page = Number(contextPageNum.value) || 1
    const size = Number(contextPageSize.value) || 3
    const offset = (page - 1) * size
    contextPage.value = await listEvalDatasetContexts(
      previewDatasetId.value,
      offset,
      size,
      previewDatasetId.value === 'squad_v2' ? squadTitleFilter.value : null,
    )
    if (!contextPage.value?.contexts?.length) {
      contextLoadError.value = '当前页没有可展示的段落'
    }
  } catch (e) {
    contextPage.value = null
    contextLoadError.value = (e as Error).message || '加载段落失败'
    MessagePlugin.error(contextLoadError.value)
  } finally {
    contextLoading.value = false
  }
}

async function ensureSquadV2AndLoadArticles() {
  if (previewDatasetId.value !== 'squad_v2') return
  if (!preview.value?.item_count) {
    squadSyncing.value = true
    try {
      await syncSquadV2Dataset()
      preview.value = await getEvalDataset('squad_v2')
      await loadDatasets()
    } catch (e) {
      MessagePlugin.error((e as Error).message)
      throw e
    } finally {
      squadSyncing.value = false
    }
  }
  try {
    squadArticles.value = await listSquadV2Articles()
  } catch {
    squadArticles.value = []
  }
}

async function openPreview(d: EvalDatasetInfo) {
  previewVisible.value = true
  previewLoading.value = true
  previewDatasetId.value = d.id
  contextPageNum.value = 1
  squadTitleFilter.value = null
  contextPage.value = null
  contextLoadError.value = ''
  try {
    preview.value = await getEvalDataset(d.id)
    if (d.kind === 'intent' || d.id === 'hotpot') return
    if (d.id === 'squad_v2') {
      try {
        await ensureSquadV2AndLoadArticles()
      } catch {
        // 同步失败时仍尝试读取本地缓存
      }
    }
    await loadContextPage()
  } catch (e) {
    MessagePlugin.error((e as Error).message || '加载预览失败')
  } finally {
    previewLoading.value = false
  }
}

async function resyncSquadV2() {
  squadSyncing.value = true
  try {
    await syncSquadV2Dataset(true)
    preview.value = await getEvalDataset('squad_v2')
    squadArticles.value = await listSquadV2Articles()
    await loadDatasets()
    contextPageNum.value = 1
    await loadContextPage()
    MessagePlugin.success('SQuAD dev-v2.0 已同步')
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    squadSyncing.value = false
  }
}

function openEdit(d: EvalDatasetInfo) {
  editForm.value = {
    id: d.id,
    description: d.description || '',
    source: d.source || '',
  }
  editVisible.value = true
}

async function submitEdit() {
  try {
    await patchEvalDataset(editForm.value.id, {
      description: editForm.value.description,
      source: editForm.value.source,
    })
    MessagePlugin.success('已更新描述')
    editVisible.value = false
    await loadDatasets()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

const exportingId = ref<string | null>(null)

async function exportDataset(d: EvalDatasetInfo | { id: string }) {
  if (exportingId.value) return
  exportingId.value = d.id
  try {
    await downloadEvalDataset(d.id)
    MessagePlugin.success(`已导出 ${d.id}.json`)
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    exportingId.value = null
  }
}

function confirmDeleteDataset(d: EvalDatasetInfo) {
  let extra = '此操作不可恢复。'
  if (d.online) extra = '将从评测库中移除该在线集，之后不再出现在列表中。此操作不可恢复。'
  else if (d.builtin) extra = '这是随仓库附带的评测集，删除后需重新转换或上传才能再用。此操作不可恢复。'
  const dlg = DialogPlugin.confirm({
    header: '删除数据集',
    body: `确定删除 ${d.id}？${extra}`,
    onConfirm: async () => {
      try {
        await deleteEvalDataset(d.id)
        MessagePlugin.success('已删除')
        await loadDatasets()
      } catch (e) {
        MessagePlugin.error((e as Error).message)
      } finally {
        dlg.destroy()
      }
    },
  })
}

function confirmDeleteTask(row: EvalTask) {
  const dlg = DialogPlugin.confirm({
    header: '删除评测任务',
    body: `确定删除任务 ${row.id}？逐题明细会一并删除。`,
    onConfirm: async () => {
      try {
        await deleteEvalTask(row.id)
        MessagePlugin.success('已删除')
        if (detailTask.value?.id === row.id) detailVisible.value = false
        await loadTasks()
      } finally {
        dlg.destroy()
      }
    },
  })
}

function confirmCancelTask(row: EvalTask) {
  const running = row.status === 'running'
  const dlg = DialogPlugin.confirm({
    header: running ? '中断评测' : '取消任务',
    body: running
      ? `确定中断 ${row.dataset_id} 的评测？当前题目可能会跑完，之后不再继续；已完成的明细会保留。`
      : `确定取消排队中的任务 ${row.id}？任务不会开始执行。`,
    confirmBtn: running ? '中断' : '取消任务',
    onConfirm: async () => {
      cancellingIds.value = { ...cancellingIds.value, [row.id]: true }
      try {
        const updated = await cancelEvalTask(row.id)
        upsertTask(updated)
        if (detailTask.value?.id === row.id) detailTask.value = updated
        await loadTasks({ silent: true })
        upsertTask(updated)
        if (hasEvalResult(updated) || updated.status === 'success') {
          MessagePlugin.success('已中断，结果已写入评测结果')
          activeTab.value = 'results'
          await onRowClick({ row: updated })
        } else {
          MessagePlugin.success(running ? '已请求中断' : '已取消')
        }
      } catch (e) {
        MessagePlugin.error((e as Error).message)
      } finally {
        const next = { ...cancellingIds.value }
        delete next[row.id]
        cancellingIds.value = next
        dlg.destroy()
      }
    },
  })
}

function upsertTask(updated: EvalTask) {
  const idx = tasks.value.findIndex((t) => t.id === updated.id)
  if (idx >= 0) {
    const next = tasks.value.slice()
    next[idx] = updated
    tasks.value = next
  } else {
    tasks.value = [updated, ...tasks.value]
  }
}

async function produceResult(row: EvalTask) {
  producingIds.value = { ...producingIds.value, [row.id]: true }
  try {
    const updated = await produceEvalResults(row.id)
    upsertTask(updated)
    MessagePlugin.success(isPartialResult(updated) ? '已按已完成题目生成部分结果' : '已生成评测结果')
    if (detailTask.value?.id === row.id) detailTask.value = updated
    await loadTasks({ silent: true })
    upsertTask(updated)
    activeTab.value = 'results'
    await onRowClick({ row: updated })
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    const next = { ...producingIds.value }
    delete next[row.id]
    producingIds.value = next
  }
}

function openScoreDialog(row: EvalTask) {
  scoreTarget.value = row
  const fromSnap = row.config_snapshot?.ragas_model_id
  scoreModelId.value =
    (typeof fromSnap === 'string' && fromSnap) ||
    form.value.ragas_model_id ||
    selectInitialModelId(evalModels.value, 'KnowledgeQA') ||
    undefined
  scoreVisible.value = true
}

async function submitScore() {
  const row = scoreTarget.value
  if (!row) return
  if (!scoreModelId.value) {
    MessagePlugin.warning('请选择 RAGAS 评分模型')
    return
  }
  scoringIds.value = { ...scoringIds.value, [row.id]: true }
  try {
    const updated = await startRagasScore(row.id, scoreModelId.value)
    upsertTask(updated)
    scoreVisible.value = false
    MessagePlugin.success('已开始离线 RAGAS 打分，请确保 Celery worker 已启动')
    activeTab.value = 'tasks'
    await loadTasks({ silent: true })
    startPolling()
    startClock()
    if (detailTask.value?.id === row.id) detailTask.value = updated
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    const next = { ...scoringIds.value }
    delete next[row.id]
    scoringIds.value = next
  }
}

async function retryFailed(row: EvalTask, qids?: number[]) {
  retryingIds.value = { ...retryingIds.value, [row.id]: true }
  try {
    const updated = await retryEvalFailed(row.id, qids)
    upsertTask(updated)
    MessagePlugin.success(
      qids?.length === 1 ? '已开始重试本题，请确保 Celery worker 已启动' : '已开始重试失败题，请确保 Celery worker 已启动',
    )
    activeTab.value = 'tasks'
    await loadTasks({ silent: true })
    startPolling()
    startClock()
    if (detailTask.value?.id === row.id) detailTask.value = updated
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    const next = { ...retryingIds.value }
    delete next[row.id]
    retryingIds.value = next
  }
}

function confirmRetryFailed(row: EvalTask, qids?: number[]) {
  const count = qids?.length || retryableCount(row, detailTask.value?.id === row.id ? detailSamples.value : undefined)
  const dlg = DialogPlugin.confirm({
    header: qids?.length === 1 ? '重试本题' : '重试失败题',
    body: qids?.length === 1
      ? '将按原配置重新跑这一题，成功后覆盖原来的失败结果。'
      : `将按原配置重跑 ${count} 道失败题（运行出错${row.suite === 'rag_quality' ? '或空答' : ''}），成功后覆盖原来的失败结果。`,
    confirmBtn: { content: '开始重试', theme: 'primary' },
    onConfirm: () => {
      dlg.destroy()
      void retryFailed(row, qids)
    },
  })
}

function confirmProduceResult(row: EvalTask) {
  if (row.status === 'running' || row.status === 'pending') {
    const dlg = DialogPlugin.confirm({
      header: '产出结果',
      body: '将中断当前评测，并按已经跑完的题目汇总指标。尚未完成的题目不会纳入结果。',
      confirmBtn: '中断并产出',
      onConfirm: async () => {
        dlg.destroy()
        await produceResult(row)
      },
    })
    return
  }
  void produceResult(row)
}

async function onRowClick(ctx: { row: EvalTask }) {
  const id = ctx.row.id
  detailVisible.value = true
  sampleFilter.value =
    ctx.row.suite === 'rag_quality' && !hasRagasMetrics(ctx.row) ? 'unscored' : 'issues'
  samplePage.value = 1
  expandedQids.value = []
  detailTask.value = ctx.row
  const samples = await listEvalSamples(id, 500, 0)
  detailSamples.value = samples.items
  sampleTotal.value = samples.total
}

function onExpandChange(keys: (string | number)[]) {
  expandedQids.value = keys
}

function startPolling() {
  if (pollTimer) return
  const tick = async () => {
    await loadTasks({ silent: true })
    if (detailVisible.value && detailTask.value) {
      const id = detailTask.value.id
      detailTask.value = await getEvalTask(id)
      if (isActiveTask(detailTask.value) || detailSamples.value.length === 0) {
        const samples = await listEvalSamples(id, 500, 0)
        detailSamples.value = samples.items
        sampleTotal.value = samples.total
      } else if (detailTask.value.status === 'success' || detailTask.value.status === 'failed') {
        const samples = await listEvalSamples(id, 500, 0)
        detailSamples.value = samples.items
        sampleTotal.value = samples.total
      }
    }
    if (!hasRunning.value) stopPolling()
  }
  void tick()
  pollTimer = setInterval(() => {
    void tick()
  }, 1500)
}

function startClock() {
  if (clockTimer) return
  clockTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
}

function stopClock() {
  if (clockTimer) {
    clearInterval(clockTimer)
    clockTimer = null
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(hasRunning, (v) => {
  if (v) {
    startPolling()
    startClock()
  } else {
    stopClock()
  }
})

watch(activeTab, (tab) => {
  if (tab === 'results' || tab === 'tasks') {
    void loadTasks({ silent: true })
  }
})

onMounted(async () => {
  await Promise.all([loadTasks(), loadDatasets(), loadEvalModels()])
  startPolling()
  if (hasRunning.value) startClock()
})

onUnmounted(() => {
  stopPolling()
  stopClock()
})
</script>

<template>
  <div class="page">
    <header class="header">
      <div>
        <h1 class="title">效果评测</h1>
        <p class="subtitle">保存可复用的测试集，并可视化每次评测结果</p>
      </div>
      <div class="header-actions">
        <t-button variant="outline" @click="uploadVisible = true">上传数据集</t-button>
        <t-button theme="primary" @click="createVisible = true">新建评测</t-button>
      </div>
    </header>

    <t-tabs v-model="activeTab">
      <t-tab-panel value="datasets" label="数据集">
        <div class="dataset-grid">
          <article v-for="d in datasets" :key="d.id" class="dataset-card">
            <div class="card-head">
              <strong class="ds-id">{{ d.id }}</strong>
              <div class="card-tags">
                <t-tag v-if="d.kind === 'intent'" size="small" variant="light">意图</t-tag>
                <t-tag v-else size="small" variant="light" theme="primary">RAG</t-tag>
                <t-tag v-if="d.online" size="small" variant="light">在线</t-tag>
                <t-tag v-else-if="d.builtin" size="small" variant="light">内置</t-tag>
              </div>
            </div>
            <p class="ds-desc">{{ d.description || '暂无描述' }}</p>
            <div class="ds-meta">
              <span v-if="d.item_count">{{ d.item_count }} 题</span>
              <span v-if="d.passage_count">{{ d.passage_count }} 段</span>
              <span v-if="d.source">{{ d.source }}</span>
              <span v-if="d.created_at">{{ String(d.created_at).replace('T', ' ').slice(0, 16) }}</span>
            </div>
            <div class="card-actions">
              <t-button size="small" variant="outline" @click="openPreview(d)">预览</t-button>
              <t-button
                size="small"
                variant="outline"
                :loading="exportingId === d.id"
                @click="exportDataset(d)"
              >
                导出 JSON
              </t-button>
              <t-button size="small" theme="primary" variant="outline" @click="useDataset(d)">用此评测</t-button>
              <t-button v-if="!d.online" size="small" variant="text" @click="openEdit(d)">编辑描述</t-button>
              <t-button
                size="small"
                theme="danger"
                variant="outline"
                @click="confirmDeleteDataset(d)"
              >
                删除
              </t-button>
            </div>
          </article>
        </div>
        <t-empty v-if="!datasets.length" description="暂无数据集" />
      </t-tab-panel>

      <t-tab-panel value="tasks" :label="activeTasks.length ? `测评任务 (${activeTasks.length})` : '测评任务'">
        <t-alert theme="info" class="tip">
          进行中的评测显示在这里。rag_quality 收集完成后会出现在「评测结果」，再选评分模型做离线 RAGAS。
          <code>celery -A api.celery_app.celery worker -B --loglevel=info</code>
        </t-alert>
        <div v-if="activeTasks.length" class="live-panel">
          <div class="live-panel-head">
            <strong>进行中</strong>
            <span class="live-panel-meta">每 1.5s 自动刷新 · {{ activeTasks.length }} 个任务</span>
          </div>
          <article v-for="t in activeTasks" :key="t.id" class="live-card" @click="openTaskDetail(t)">
            <div class="live-card-top">
              <div class="live-card-title">
                <t-tag :theme="statusTheme(t.status, isPartialResult(t))" variant="light" size="small">
                  {{ statusLabel(t.status, isPartialResult(t)) }}
                </t-tag>
                <t-tag v-if="taskPhaseText(t)" size="small" variant="outline">{{ taskPhaseText(t) }}</t-tag>
                <strong>{{ t.dataset_id }}</strong>
                <span class="muted">{{ suiteLabel(t.suite) }} · {{ t.pipeline_profile }}</span>
              </div>
              <div class="live-card-time">
                <span>已用 {{ taskElapsed(t) }}</span>
                <span v-if="taskEta(t)">剩余 {{ taskEta(t) }}</span>
              </div>
            </div>
            <t-progress
              :percentage="progressPct(t)"
              :label="progressLabel(t)"
              :status="progressStatus(t)"
              :class="{ 'ragas-progress': isRagasScoring(t) }"
            />
            <p class="live-card-metrics">{{ highlightMetric(t.metric_summary) }}</p>
            <p v-if="evalKbId(t)" class="live-card-kb">
              临时知识库
              <router-link :to="`/knowledge-bases/${evalKbId(t)}`" @click.stop>
                {{ evalKbName(t) || `#${evalKbId(t)}` }}
              </router-link>
              <span class="muted"> · 评测结束后自动删除</span>
            </p>
            <div class="live-card-actions" @click.stop>
              <t-button size="small" variant="outline" @click="openTaskDetail(t)">查看明细</t-button>
              <t-button
                size="small"
                theme="primary"
                :loading="Boolean(producingIds[t.id])"
                @click="confirmProduceResult(t)"
              >
                产出结果
              </t-button>
              <t-button
                size="small"
                theme="danger"
                variant="outline"
                :loading="Boolean(cancellingIds[t.id])"
                @click="confirmCancelTask(t)"
              >
                {{ t.status === 'running' ? '中断' : '取消' }}
              </t-button>
            </div>
          </article>
        </div>
        <t-empty v-else description="暂无进行中的评测。点击「新建评测」开始">
          <template #action>
            <t-button theme="primary" @click="createVisible = true">新建评测</t-button>
          </template>
        </t-empty>
      </t-tab-panel>

      <t-tab-panel value="results" :label="resultTasks.length ? `评测结果 (${resultTasks.length})` : '评测结果'">
        <t-alert theme="info" class="tip">
          跑完、中断或手动「产出结果」后会出现在这里。rag_quality 先收集 RAG 轨迹，再点「RAGAS 打分」选评分模型。
        </t-alert>
        <div v-if="resultTasks.length" class="result-list">
          <article
            v-for="t in resultTasks"
            :key="t.id"
            class="result-card"
            :class="{
              'result-card--issues': resultIssueCount(t) > 0,
              'result-card--failed': t.status === 'failed',
            }"
            @click="onRowClick({ row: t })"
          >
            <div class="live-card-top">
              <div class="live-card-title">
                <t-tag :theme="taskStatusTheme(t)" variant="light" size="small">
                  {{ taskStatusLabel(t) }}
                </t-tag>
                <strong>{{ t.dataset_id }}</strong>
                <span class="muted">{{ suiteLabel(t.suite) }} · {{ t.pipeline_profile }}</span>
              </div>
              <div class="live-card-time">
                <span>{{ taskDurationLabel(t) }}</span>
                <span v-if="t.finished || t.total">{{ t.finished }}/{{ t.total || '?' }} 题</span>
              </div>
            </div>
            <p class="result-card-verdict" :class="{ 'has-issues': resultIssueCount(t) > 0 }">
              {{ resultIssueLine(t) }}
            </p>
            <p v-if="t.status === 'failed' && t.err_msg" class="result-card-error">{{ t.err_msg }}</p>
            <p class="live-card-metrics">{{ highlightMetric(t.metric_summary) }}</p>
            <p class="result-card-id mono">{{ t.id }}</p>
            <div class="live-card-actions" @click.stop>
              <t-button size="small" variant="outline" @click="onRowClick({ row: t })">查看明细</t-button>
              <t-button
                v-if="canRetryFailed(t)"
                size="small"
                theme="warning"
                :loading="Boolean(retryingIds[t.id])"
                @click="confirmRetryFailed(t)"
              >
                重试失败题
              </t-button>
              <t-button
                v-if="canScoreRagas(t)"
                size="small"
                theme="primary"
                :loading="Boolean(scoringIds[t.id])"
                @click="openScoreDialog(t)"
              >
                {{ hasRagasMetrics(t) ? '重新 RAGAS 打分' : 'RAGAS 打分' }}
              </t-button>
              <t-button
                v-if="canProduceResult(t)"
                size="small"
                theme="primary"
                :loading="Boolean(producingIds[t.id])"
                @click="confirmProduceResult(t)"
              >
                产出结果
              </t-button>
              <t-button size="small" theme="danger" variant="outline" @click="confirmDeleteTask(t)">
                删除
              </t-button>
            </div>
          </article>
        </div>
        <t-empty
          v-else-if="!loading"
          description="暂无评测结果。跑完评测，或在测评任务中点击「产出结果」，会出现在这里"
        />
      </t-tab-panel>
    </t-tabs>

    <t-dialog v-model:visible="createVisible" header="新建评测任务" :confirm-on-enter="false" @confirm="submitCreate">
      <t-form label-width="110px">
        <t-form-item label="数据集">
          <t-select v-model="form.dataset_id" filterable>
            <t-option v-for="d in filteredDatasets" :key="d.id" :value="d.id" :label="d.id">
              {{ d.id }} — {{ d.description }}
            </t-option>
          </t-select>
        </t-form-item>
        <t-alert v-if="createDatasetHint" theme="info" class="create-hint">{{ createDatasetHint }}</t-alert>
        <t-form-item label="评测套件">
          <t-radio-group v-model="form.suite">
            <t-radio value="rag_bench">rag_bench（快）</t-radio>
            <t-radio value="intent_bench">intent_bench（意图）</t-radio>
            <t-radio value="rag_quality">rag_quality（先收集，再离线 RAGAS）</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item :label="form.dataset_id.startsWith('squad') ? '抽样题数（按段）' : '抽样题数'">
          <t-input-number v-model="form.sample_limit" :min="1" :max="500" placeholder="全部" theme="normal" />
        </t-form-item>
        <t-form-item label="并行度">
          <t-input-number v-model="form.workers" :min="1" :max="8" theme="normal" />
          <p class="muted form-hint">
            {{
              form.suite === 'rag_quality'
                ? '这是收集 RAG 回答时的并行度，建议 1。RAGAS 打分在结果收集完成后再单独发起。'
                : '遇到供应商 429/TPM 限额时请降到 1；评测会自动等待后重试。'
            }}
          </p>
        </t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog v-model:visible="uploadVisible" header="上传 JSON 数据集" width="640px" @confirm="submitUpload">
      <p class="upload-hint">
        SQuAD 单篇格式：title + paragraphs[{context, qas}]；id 可不填（自动生成）。
        意图数据集仍用 items + intent_gt。可填写描述方便下次选用。
      </p>
      <t-form label-width="80px">
        <t-form-item label="描述">
          <t-input v-model="uploadDescription" placeholder="例如：SQuAD Normans 单篇，含不可答题" />
        </t-form-item>
        <t-form-item label="来源">
          <t-input v-model="uploadSource" placeholder="例如：squad_v2:Normans" />
        </t-form-item>
        <t-form-item label="覆盖">
          <t-checkbox v-model="uploadOverwrite">同 id 已存在时覆盖（内置集除外）</t-checkbox>
        </t-form-item>
        <t-form-item label="文件">
          <input type="file" accept=".json,application/json" @change="onUploadFile" />
        </t-form-item>
      </t-form>
      <t-textarea v-model="uploadJson" :placeholder="uploadExample" :autosize="{ minRows: 10, maxRows: 18 }" />
    </t-dialog>

    <t-dialog v-model:visible="editVisible" header="编辑数据集描述" @confirm="submitEdit">
      <t-form label-width="80px">
        <t-form-item label="ID">{{ editForm.id }}</t-form-item>
        <t-form-item label="描述">
          <t-textarea v-model="editForm.description" :autosize="{ minRows: 3, maxRows: 6 }" />
        </t-form-item>
        <t-form-item label="来源">
          <t-input v-model="editForm.source" />
        </t-form-item>
      </t-form>
    </t-dialog>

    <t-dialog
      v-model:visible="scoreVisible"
      header="离线 RAGAS 打分"
      :confirm-on-enter="false"
      :confirm-btn="{ content: '开始打分', theme: 'primary', loading: Boolean(scoreTarget && scoringIds[scoreTarget.id]) }"
      @confirm="submitScore"
    >
      <p class="muted form-hint">
        使用已收集的 question、检索片段、系统回答和标准答案打分。运行失败或空答的题目不会送进 RAGAS。
        不要用 DeepSeek-R1 这类思考模型打分，会极慢甚至卡住；选普通对话模型（如 V3 / Qwen）。
      </p>
      <t-form label-width="110px">
        <t-form-item label="评分模型" required>
          <ModelSelector
            v-model:selected-model-id="scoreModelId"
            model-type="KnowledgeQA"
            :all-models="evalModels"
            placeholder="用于忠实度、相关性、上下文精确度/召回"
          />
        </t-form-item>
      </t-form>
    </t-dialog>

    <t-drawer v-model:visible="previewVisible" size="large" :header="preview?.id || '数据集预览'">
      <div v-if="previewLoading" class="preview-state">加载中…</div>
      <template v-else-if="preview">
        <p class="ds-desc">{{ preview.description }}</p>
        <div class="preview-toolbar">
          <t-button
            size="small"
            variant="outline"
            :loading="exportingId === preview.id"
            @click="exportDataset(preview)"
          >
            导出 JSON
          </t-button>
          <t-button
            v-if="preview.id === 'squad_v2'"
            size="small"
            variant="outline"
            :loading="squadSyncing"
            @click="resyncSquadV2"
          >
            重新同步 dev-v2.0
          </t-button>
          <t-select
            v-if="preview.id === 'squad_v2'"
            v-model="squadTitleFilter"
            clearable
            placeholder="按 Wikipedia 条目筛选"
            style="min-width: 220px"
            @change="onSquadArticleFilter"
          >
            <t-option
              v-for="a in squadArticles"
              :key="a.title"
              :value="a.title"
              :label="`${a.title}（${a.question_count} 题 / ${a.context_count} 段）`"
            />
          </t-select>
        </div>
        <t-descriptions v-if="contextPage || preview.stats" :column="3" bordered size="small">
          <t-descriptions-item label="总题数">
            {{ contextPage?.total_questions ?? preview.stats?.item_count ?? preview.item_count ?? '—' }}
          </t-descriptions-item>
          <t-descriptions-item label="Context 段">
            {{ contextPage?.total_contexts ?? preview.stats?.passage_count ?? preview.passage_count ?? '—' }}
          </t-descriptions-item>
          <t-descriptions-item v-if="preview.kind !== 'intent'" label="HasAns / NoAns">
            {{ preview.stats?.hasans_count ?? '—' }} / {{ preview.stats?.noans_count ?? '—' }}
          </t-descriptions-item>
        </t-descriptions>

        <template v-if="preview.kind === 'intent'">
          <h3 class="section-title">题目</h3>
          <t-table :data="preview.items || []" row-key="qid" size="small" max-height="360">
            <t-table-column title="#" col-key="qid" width="48" />
            <t-table-column title="问题" col-key="question" min-width="180" />
            <t-table-column title="意图" col-key="intent_gt" min-width="120" />
          </t-table>
        </template>

        <template v-else>
          <p class="upload-hint">每段正文（context）下列出对应的问题与参考答案，与 SQuAD 官方格式一致。</p>
          <div v-if="contextLoading && !contextPage" class="preview-state">加载段落…</div>
          <div v-else-if="contextLoadError" class="preview-state">{{ contextLoadError }}</div>
          <div v-else class="context-list">
            <article v-for="block in contextPage?.contexts || []" :key="block.index" class="context-block">
              <header class="context-head">
                <strong>#{{ block.index + 1 }} · {{ block.article_title }}</strong>
                <span class="context-meta">{{ block.question_count }} 题（HasAns {{ block.hasans_count }} / NoAns {{ block.noans_count }}）</span>
              </header>
              <h4 class="context-label">正文</h4>
              <p class="context-text">{{ block.context }}</p>
              <h4 class="context-label">问题与参考答案（{{ block.qas?.length || 0 }}）</h4>
              <ul v-if="block.qas?.length" class="qa-list">
                <li v-for="(qa, qi) in block.qas" :key="qa.id || `${block.index}-${qi}`" class="qa-item">
                  <div class="qa-question">
                    <span class="qa-index">Q{{ qi + 1 }}</span>
                    {{ qa.question }}
                  </div>
                  <div class="qa-answer">
                    <span class="qa-index">A</span>
                    <t-tag v-if="qa.is_impossible" size="small" variant="light">不可答</t-tag>
                    <span v-else>{{ formatQaAnswer(qa) }}</span>
                  </div>
                </li>
              </ul>
              <p v-else class="qa-empty">该段正文下暂无问答</p>
            </article>
            <t-empty v-if="!contextPage?.contexts?.length && !contextLoading" description="暂无段落" />
          </div>
          <div v-if="contextPage && contextPage.total_contexts > contextPageSize" class="context-pagination">
            <t-pagination
              v-model:current="contextPageNum"
              :page-size="contextPageSize"
              :total="contextPage.total_contexts"
              size="small"
              @current-change="loadContextPage"
            />
          </div>
        </template>
      </template>
    </t-drawer>

    <t-drawer v-model:visible="detailVisible" size="large" :header="detailHeader">
      <template v-if="detailTask">
        <div v-if="isActiveTask(detailTask)" class="detail-live">
          <div class="detail-live-head">
            <t-tag :theme="statusTheme(detailTask.status, isPartialResult(detailTask))" variant="light">
              {{ statusLabel(detailTask.status, isPartialResult(detailTask)) }}
            </t-tag>
            <t-tag v-if="taskPhaseText(detailTask)" size="small" variant="outline">
              {{ taskPhaseText(detailTask) }}
            </t-tag>
            <span>已用 {{ taskElapsed(detailTask) }}</span>
            <span v-if="taskEta(detailTask)">预计剩余 {{ taskEta(detailTask) }}</span>
            <t-button
              v-if="canProduceResult(detailTask)"
              size="small"
              theme="primary"
              :loading="Boolean(producingIds[detailTask.id])"
              @click="confirmProduceResult(detailTask)"
            >
              产出结果
            </t-button>
            <t-button
              size="small"
              theme="danger"
              variant="outline"
              :loading="Boolean(cancellingIds[detailTask.id])"
              @click="confirmCancelTask(detailTask)"
            >
              {{ detailTask.status === 'running' ? '中断' : '取消' }}
            </t-button>
          </div>
          <t-progress
            :percentage="progressPct(detailTask)"
            :label="progressLabel(detailTask)"
            :status="progressStatus(detailTask)"
            :class="{ 'ragas-progress': isRagasScoring(detailTask) }"
          />
          <p class="live-card-metrics">{{ highlightMetric(detailTask.metric_summary) }}</p>
          <p v-if="evalKbId(detailTask)" class="live-card-kb">
            临时知识库
            <router-link :to="`/knowledge-bases/${evalKbId(detailTask)}`">
              {{ evalKbName(detailTask) || `#${evalKbId(detailTask)}` }}
            </router-link>
            <span class="muted"> · 评测结束后自动删除</span>
          </p>
        </div>

        <t-descriptions :column="2" bordered size="small">
          <t-descriptions-item label="数据集">{{ detailTask.dataset_id }}</t-descriptions-item>
          <t-descriptions-item label="套件">{{ suiteLabel(detailTask.suite) }}</t-descriptions-item>
          <t-descriptions-item label="Pipeline">{{ detailTask.pipeline_profile }}</t-descriptions-item>
          <t-descriptions-item label="对话模型">系统默认</t-descriptions-item>
          <t-descriptions-item v-if="detailTask.suite === 'rag_quality'" label="RAGAS 评分模型">
            {{ snapshotModelLabel(detailTask, 'ragas_model_id') === '系统默认' ? '尚未选择（打分时再选）' : snapshotModelLabel(detailTask, 'ragas_model_id') }}
          </t-descriptions-item>
          <t-descriptions-item label="状态">
            <t-tag :theme="taskStatusTheme(detailTask)">
              {{ taskStatusLabel(detailTask) }}
            </t-tag>
          </t-descriptions-item>
          <t-descriptions-item label="耗时">{{ taskDurationLabel(detailTask) }}</t-descriptions-item>
          <t-descriptions-item label="进度">
            <div class="detail-progress">
              <t-progress
                :percentage="progressPct(detailTask)"
                :label="progressLabel(detailTask)"
                size="small"
                :status="progressStatus(detailTask)"
              />
            </div>
          </t-descriptions-item>
          <t-descriptions-item v-if="detailTask.err_msg" label="错误" :span="2">
            {{ detailTask.err_msg }}
          </t-descriptions-item>
        </t-descriptions>

        <div v-if="canRetryFailed(detailTask, detailSamples) && !isActiveTask(detailTask)" class="detail-produce">
          <t-button
            theme="warning"
            :loading="Boolean(retryingIds[detailTask.id])"
            @click="confirmRetryFailed(detailTask)"
          >
            重试失败题（{{ retryableCount(detailTask, detailSamples) }}）
          </t-button>
          <span class="muted">只重跑运行出错{{ detailTask.suite === 'rag_quality' ? '或空答' : '' }}的题目，已成功的题不会动。</span>
        </div>
        <div v-if="canScoreRagas(detailTask) && !isActiveTask(detailTask)" class="detail-produce">
          <t-button
            theme="primary"
            :loading="Boolean(scoringIds[detailTask.id])"
            @click="openScoreDialog(detailTask)"
          >
            {{ hasRagasMetrics(detailTask) ? '重新 RAGAS 打分' : '开始 RAGAS 打分' }}
          </t-button>
          <span class="muted">用已收集的 question / 检索片段 / 回答 / 标准答案离线打分，失败题不会送进 RAGAS。</span>
        </div>
        <div v-if="canProduceResult(detailTask) && !isActiveTask(detailTask)" class="detail-produce">
          <t-button
            theme="primary"
            :loading="Boolean(producingIds[detailTask.id])"
            @click="confirmProduceResult(detailTask)"
          >
            产出结果
          </t-button>
          <span class="muted">按已完成的题目汇总 EM/F1 等指标，生成可对比的评测结果。</span>
        </div>

        <div class="verdict-summary">
          <div class="verdict-summary-head">
            <h3 class="section-title verdict-title">判定汇总</h3>
            <span class="muted">
              答对 {{ detailIssueStats.ok }}
              · 答错 {{ detailIssueStats.wrong + detailIssueStats.errors }}
              <template v-if="detailIssueStats.unscored"> · 未打分 {{ detailIssueStats.unscored }}</template>
              · 共 {{ detailIssueStats.total || sampleTotal }} 题
            </span>
          </div>
          <div class="verdict-chips">
            <button
              v-for="chip in verdictChips"
              :key="chip.value"
              type="button"
              class="verdict-chip"
              :class="[`verdict-chip--${chip.tone}`, { active: sampleFilter === chip.value }]"
              @click="setSampleFilter(chip.value)"
            >
              {{ chip.label }}
              <strong>{{ chip.count }}</strong>
            </button>
          </div>
        </div>

        <h3 class="section-title">
          逐题明细
          <span class="muted">（{{ filteredSamples.length }} / {{ sampleTotal }}）</span>
        </h3>
        <t-table
          :data="pagedSamples"
          :columns="sampleColumns"
          row-key="qid"
          size="small"
          max-height="480"
          :expanded-row-keys="expandedQids"
          :row-class-name="sampleRowClassName"
          :empty="sampleEmptyText"
          @expand-change="onExpandChange"
        >
          <template #verdict="{ row }">
            <t-tag :theme="sampleVerdict(row).theme" size="small" variant="light">
              {{ sampleVerdict(row).label }}
            </t-tag>
          </template>
          <template #gold="{ row }">
            <span class="clip">{{ formatGoldLabel(row) }}</span>
          </template>
          <template #pred="{ row }">
            <span class="clip">{{ formatPredLabel(row) }}</span>
          </template>
          <template #latency="{ row }">
            {{ row.latency_ms != null ? `${row.latency_ms}ms` : '—' }}
          </template>
          <template #sampleMetrics="{ row }">
            <span class="mono clip">{{ formatSampleMetrics(row) }}</span>
          </template>
          <template #expanded-row="{ row }">
            <div class="expand-block">
              <p class="expand-verdict">
                <t-tag :theme="sampleVerdict(row).theme" size="small" variant="light">
                  {{ sampleVerdict(row).label }}
                </t-tag>
                {{ sampleVerdict(row).reason }}
              </p>
              <p class="expand-question"><strong>问题：</strong>{{ row.question || '—' }}</p>
              <p>
                <strong>{{ isIntentSample(row) ? '标注意图：' : '正确答案：' }}</strong>
                {{ formatGoldLabel(row) }}
              </p>
              <p>
                <strong>{{ isIntentSample(row) ? '识别结果：' : '系统答案：' }}</strong>
                {{ formatPredLabel(row) }}
              </p>
              <p v-if="hasRetrieval(row)">
                <strong>检索：</strong>
                系统召回 {{ (row.retrieval_ids || []).join(', ') || '—' }}
                / 应命中 {{ (row.retrieval_gt || []).join(', ') || '—' }}
                <span v-if="retrievalHit(row) === true" class="hit-ok"> · 已命中</span>
                <span v-else-if="retrievalHit(row) === false" class="hit-miss"> · 未命中</span>
              </p>
              <p v-if="isRetryableSample(detailTask, row)" class="expand-retry">
                <t-button
                  size="small"
                  theme="warning"
                  variant="outline"
                  :loading="Boolean(retryingIds[detailTask.id])"
                  @click="confirmRetryFailed(detailTask, [row.qid])"
                >
                  重试本题
                </t-button>
              </p>
              <div v-if="sampleContexts(row).length" class="expand-contexts">
                <strong>检索片段（{{ sampleContexts(row).length }}）：</strong>
                <ol>
                  <li v-for="(ctx, idx) in sampleContexts(row)" :key="idx">{{ ctx }}</li>
                </ol>
              </div>
            </div>
          </template>
        </t-table>
        <t-pagination
          v-if="filteredSamples.length > samplePageSize"
          v-model:current="samplePage"
          :total="filteredSamples.length"
          :page-size="samplePageSize"
          size="small"
          class="sample-pager"
        />

        <h3 class="section-title">汇总指标</h3>
        <EvalMetricCards :summary="detailTask.metric_summary" :sample-count="detailSampleCount" />
        <h3 class="section-title">指标条形图</h3>
        <EvalMetricBars :summary="detailTask.metric_summary" />

        <template v-if="historyTasks.length > 1">
          <h3 class="section-title">同数据集历史</h3>
          <t-table :data="historyTasks" row-key="id" size="small">
            <t-table-column title="任务" col-key="id" ellipsis>
              <template #default="{ row }">
                <span class="mono">{{ row.id }}</span>
              </template>
            </t-table-column>
            <t-table-column title="Pipeline" col-key="pipeline_profile" width="110" />
            <t-table-column title="指标" min-width="180">
              <template #default="{ row }">{{ historyHighlight(row) }}</template>
            </t-table-column>
            <t-table-column title="耗时" width="90">
              <template #default="{ row }">{{ taskDurationLabel(row) }}</template>
            </t-table-column>
          </t-table>
        </template>
      </template>
    </t-drawer>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  overflow: auto;
  padding: 20px 28px;
  box-sizing: border-box;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.title {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  line-height: 32px;
  color: var(--td-text-color-primary);
}

.subtitle {
  margin: 4px 0 0;
  color: var(--td-text-color-placeholder);
  font-size: 14px;
  line-height: 20px;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.tip {
  margin-bottom: 16px;
}

.tip code {
  font-size: 12px;
  background: var(--td-gray-bg-color, #f5f5f5);
  padding: 2px 6px;
  border-radius: 4px;
}

.dataset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  padding: 8px 0 16px;
}

.dataset-card {
  border: 1px solid var(--td-component-border, #e7e7e7);
  border-radius: 10px;
  padding: 14px;
  background: var(--td-bg-color-container, #fff);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  align-items: flex-start;
}

.ds-id {
  font-size: 15px;
}

.card-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.ds-desc {
  margin: 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  line-height: 1.5;
  min-height: 40px;
}

.ds-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.mono {
  font-family: ui-monospace, monospace;
  font-size: 12px;
}

.metrics-cell {
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.section-title {
  margin: 20px 0 8px;
  font-size: 15px;
}

.section-title .muted {
  font-weight: 400;
  font-size: 13px;
}

.progress-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.progress-phase {
  font-size: 11px;
  color: var(--td-text-color-secondary);
}

.detail-progress {
  max-width: 360px;
}

.detail-produce {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 16px 0 0;
}

.live-panel {
  margin: 0 0 16px;
  padding: 14px 16px;
  border: 1px solid var(--td-brand-color-light, #d4e3fc);
  border-radius: 10px;
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--td-brand-color, #0052d9) 6%, transparent),
    transparent
  );
}

.live-panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.live-panel-meta {
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.result-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 0 16px;
}

.result-card {
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-border);
  cursor: pointer;
}

.result-card:hover {
  border-color: var(--td-brand-color);
}

.result-card--issues {
  border-color: var(--td-warning-color, #ed7b2f);
}

.result-card--failed {
  border-color: var(--td-error-color, #d54941);
}

.result-card-verdict {
  margin: 8px 0 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--td-success-color, #2ba471);
}

.result-card-verdict.has-issues {
  color: var(--td-error-color, #d54941);
}

.result-card-error {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--td-error-color, #d54941);
}

.result-card-id {
  margin: 4px 0 8px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.live-card {
  padding: 12px;
  margin-bottom: 10px;
  border-radius: 8px;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-border);
  cursor: pointer;
}

.live-card:last-child {
  margin-bottom: 0;
}

.live-card:hover {
  border-color: var(--td-brand-color);
}

.live-card-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.live-card-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.live-card-time {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.live-card-metrics {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.live-card-kb {
  margin: 6px 0 0;
  font-size: 12px;
}

.live-card-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}

.detail-live {
  margin-bottom: 16px;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--td-brand-color-light, #d4e3fc);
  background: color-mix(in srgb, var(--td-brand-color, #0052d9) 5%, transparent);
}

.detail-live-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.muted {
  color: var(--td-text-color-placeholder);
  font-size: 12px;
}

.form-hint {
  margin: 6px 0 0;
  line-height: 1.4;
  max-width: 360px;
}

.ragas-progress :deep(.t-progress__bar) {
  background: linear-gradient(
    90deg,
    var(--td-brand-color-3, #bbd3fb) 0%,
    var(--td-brand-color, #0052d9) 50%,
    var(--td-brand-color-3, #bbd3fb) 100%
  );
  background-size: 200% 100%;
  animation: ragas-bar 1.6s linear infinite;
}

@keyframes ragas-bar {
  0% {
    background-position: 100% 0;
  }
  100% {
    background-position: -100% 0;
  }
}

.preview-state {
  padding: 24px;
  text-align: center;
  color: var(--td-text-color-placeholder);
}

.preview-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin: 12px 0;
}

.context-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
  margin-top: 16px;
}

.context-block {
  padding: 14px;
  border: 1px solid var(--td-component-border);
  border-radius: 8px;
  background: var(--td-bg-color-container);
}

.context-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  margin-bottom: 8px;
}

.context-meta {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.context-label {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
}

.context-text {
  margin: 0 0 12px;
  line-height: 1.6;
  font-size: 14px;
  white-space: pre-wrap;
  max-height: 240px;
  overflow: auto;
  padding: 10px;
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
}

.qa-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.qa-item {
  padding: 10px 12px;
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  background: var(--td-bg-color-container);
}

.qa-question,
.qa-answer {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  line-height: 1.5;
  font-size: 14px;
}

.qa-question {
  margin-bottom: 6px;
  font-weight: 500;
}

.qa-answer {
  color: var(--td-text-color-secondary);
}

.qa-index {
  flex-shrink: 0;
  min-width: 28px;
  font-size: 12px;
  font-weight: 600;
  color: var(--td-brand-color);
}

.qa-empty {
  margin: 0;
  font-size: 13px;
  color: var(--td-text-color-placeholder);
}

.context-pagination {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}

.upload-hint {
  font-size: 13px;
  color: var(--td-text-color-secondary);
  margin: 0 0 8px;
}

.create-hint {
  margin: 0 0 12px;
}

.clip {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12px;
}

.verdict-summary {
  margin-top: 16px;
}

.verdict-summary-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.verdict-title {
  margin: 0;
}

.verdict-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 4px;
}

.verdict-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--td-component-border);
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

.verdict-chip strong {
  font-size: 13px;
  color: var(--td-text-color-primary);
}

.verdict-chip.active {
  border-color: var(--td-brand-color);
  background: var(--td-brand-color-light, #f2f3ff);
  color: var(--td-brand-color);
}

.verdict-chip--danger.active {
  border-color: var(--td-error-color, #d54941);
  background: var(--td-error-color-1, #fff0ed);
  color: var(--td-error-color, #d54941);
}

.verdict-chip--warning.active {
  border-color: var(--td-warning-color, #ed7b2f);
  background: var(--td-warning-color-1, #fff1e9);
  color: var(--td-warning-color, #ed7b2f);
}

.verdict-chip--success.active {
  border-color: var(--td-success-color, #2ba471);
  background: var(--td-success-color-1, #e8f8f2);
  color: var(--td-success-color, #2ba471);
}

.sample-pager {
  margin-top: 12px;
}

.expand-block {
  padding: 8px 12px;
  font-size: 12px;
  background: var(--td-bg-color-container-hover, #fafafa);
  border-radius: 6px;
}

.expand-block p {
  margin: 0 0 6px;
}

.expand-question {
  white-space: pre-wrap;
  word-break: break-word;
}

.expand-verdict {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px !important;
  color: var(--td-text-color-primary);
}

.expand-retry {
  margin: 8px 0 6px;
}

.expand-contexts ol {
  margin: 6px 0 0;
  padding-left: 1.2em;
  color: var(--td-text-color-secondary);
  line-height: 1.5;
}

.expand-contexts li {
  margin-bottom: 6px;
  white-space: pre-wrap;
  word-break: break-word;
}

.hit-ok {
  color: var(--td-success-color, #2ba471);
}

.hit-miss {
  color: var(--td-error-color, #d54941);
}

:deep(.sample-row-issue) {
  background: var(--td-error-color-1, #fff0ed);
}
</style>
