<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import {
  createEvalTask,
  deleteEvalDataset,
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
import {
  formatMetricValue,
  highlightMetric,
  isRagasScoring,
  progressLabel,
  progressPercentage,
  taskDurationLabel,
} from '@/utils/evalMetrics'

const loading = ref(false)
const activeTab = ref<'datasets' | 'results'>('datasets')
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
  pipeline_profile: 'rag_fixed',
  sample_limit: undefined,
  workers: 2,
})

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
const sampleFilter = ref<'all' | 'hasans' | 'noans' | 'hit' | 'miss' | 'error'>('all')
const samplePage = ref(1)
const samplePageSize = ref(20)
const expandedQids = ref<(string | number)[]>([])

let pollTimer: ReturnType<typeof setInterval> | null = null

const hasRunning = computed(() => tasks.value.some((t) => t.status === 'running' || t.status === 'pending'))

const statusTheme = (s: string) => {
  if (s === 'success') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'running') return 'primary'
  return 'default'
}

const statusLabel = (s: string) => {
  const map: Record<string, string> = {
    pending: '等待中',
    running: '运行中',
    success: '已完成',
    failed: '失败',
  }
  return map[s] || s
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
    return 'SQuAD 2.0 使用 EM/F1 与不可答题拒答率。squad_normans 为单篇冒烟；squad_v2 在线加载全量 validation。'
  }
  if (form.value.suite !== 'rag_quality') return ''
  if (form.value.dataset_id === 'hotpot') {
    return 'RAGAS 将在线加载 HotpotQA（需网络）。建议抽样 10–50 题。'
  }
  return 'RAGAS 将对所选 JSON 数据集跑 Agent 检索并打分（faithfulness / relevancy 等）。'
})

const filteredDatasets = computed(() => {
  if (form.value.suite === 'intent_bench') {
    const intentOnly = datasets.value.filter((d) => d.kind === 'intent' || d.id.includes('intent'))
    return intentOnly.length ? intentOnly : datasets.value
  }
  return datasets.value.filter((d) => d.kind !== 'intent')
})

const detailSampleCount = computed(() => {
  const n = detailTask.value?.metric_summary?.sample_count
  return typeof n === 'number' ? n : sampleTotal.value || detailSamples.value.length || null
})

const historyTasks = computed(() => {
  const ds = detailTask.value?.dataset_id
  if (!ds) return []
  return tasks.value
    .filter((t) => t.dataset_id === ds && t.status === 'success' && t.metric_summary)
    .slice(0, 8)
})

function retrievalHit(row: EvalSample): boolean | null {
  const gt = row.retrieval_gt || []
  const ids = row.retrieval_ids || []
  if (!gt.length) return null
  return gt.some((id) => ids.includes(id))
}

function squadFlags(row: EvalSample) {
  const squad = row.metrics?.squad as Record<string, number> | undefined
  return {
    impossible: squad?.impossible === 1,
    abstained: squad?.abstained === 1,
  }
}

const filteredSamples = computed(() => {
  return detailSamples.value.filter((row) => {
    if (sampleFilter.value === 'all') return true
    if (sampleFilter.value === 'error') return Boolean(row.error)
    if (sampleFilter.value === 'hasans') return !squadFlags(row).impossible
    if (sampleFilter.value === 'noans') return squadFlags(row).impossible
    const hit = retrievalHit(row)
    if (sampleFilter.value === 'hit') return hit === true
    if (sampleFilter.value === 'miss') return hit === false
    return true
  })
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

function formatSampleMetrics(metrics: Record<string, unknown> | null | undefined): string {
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
      form.value.pipeline_profile = 'rag_fixed'
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

async function loadTasks() {
  loading.value = true
  try {
    const data = await listEvalTasks()
    tasks.value = data.items
  } finally {
    loading.value = false
  }
}

async function loadDatasets() {
  datasets.value = await listEvalDatasets()
  if (!datasets.value.find((d) => d.id === form.value.dataset_id) && datasets.value.length) {
    form.value.dataset_id = datasets.value[0].id
  }
}

async function submitCreate() {
  try {
    const payload: CreateEvalPayload = {
      ...form.value,
      pipeline_profile:
        form.value.suite === 'rag_quality'
          ? 'rag_agent'
          : form.value.suite === 'intent_bench'
            ? 'intent'
            : form.value.pipeline_profile,
    }
    if (payload.suite === 'rag_quality' && !payload.sample_limit) {
      payload.sample_limit = 10
    }
    await createEvalTask(payload)
    MessagePlugin.success('评测任务已创建，请确保 Celery worker 已启动')
    createVisible.value = false
    activeTab.value = 'results'
    await loadTasks()
    startPolling()
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
    form.value.pipeline_profile = 'rag_fixed'
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

async function onRowClick(ctx: { row: EvalTask }) {
  const id = ctx.row.id
  detailVisible.value = true
  sampleFilter.value = 'all'
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
  pollTimer = setInterval(async () => {
    await loadTasks()
    if (detailVisible.value && detailTask.value) {
      const id = detailTask.value.id
      detailTask.value = await getEvalTask(id)
      const samples = await listEvalSamples(id, 500, 0)
      detailSamples.value = samples.items
      sampleTotal.value = samples.total
    }
    if (!hasRunning.value) stopPolling()
  }, 3000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(hasRunning, (v) => {
  if (v) startPolling()
})

onMounted(async () => {
  await Promise.all([loadTasks(), loadDatasets()])
  startPolling()
})

onUnmounted(stopPolling)
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

      <t-tab-panel value="results" label="评测结果">
        <t-alert theme="info" class="tip">
          评测任务由 Celery 异步执行。请在本机运行：
          <code>celery -A api.celery_app.celery worker -B --loglevel=info</code>
        </t-alert>
        <t-table :data="tasks" :loading="loading" row-key="id" hover stripe @row-click="onRowClick">
          <t-table-column title="任务 ID" col-key="id" width="240">
            <template #default="{ row }">
              <span class="mono">{{ row.id }}</span>
            </template>
          </t-table-column>
          <t-table-column title="数据集" col-key="dataset_id" width="140" />
          <t-table-column title="套件" col-key="suite" width="110">
            <template #default="{ row }">{{ suiteLabel(row.suite) }}</template>
          </t-table-column>
          <t-table-column title="状态" col-key="status" width="90">
            <template #default="{ row }">
              <t-tag :theme="statusTheme(row.status)" variant="light">{{ statusLabel(row.status) }}</t-tag>
            </template>
          </t-table-column>
          <t-table-column title="进度" col-key="finished" width="160">
            <template #default="{ row }">
              <div v-if="row.total > 0 || isRagasScoring(row)" class="progress-cell">
                <t-progress
                  :percentage="progressPct(row)"
                  :label="progressLabel(row)"
                  size="small"
                  :status="isRagasScoring(row) ? 'active' : undefined"
                  :class="{ 'ragas-progress': isRagasScoring(row) }"
                />
              </div>
              <span v-else>—</span>
            </template>
          </t-table-column>
          <t-table-column title="关键指标" col-key="metric_summary" min-width="180">
            <template #default="{ row }">
              <span class="metrics-cell">{{ highlightMetric(row.metric_summary) }}</span>
            </template>
          </t-table-column>
          <t-table-column title="耗时" col-key="finished_at" width="90">
            <template #default="{ row }">{{ taskDurationLabel(row) }}</template>
          </t-table-column>
          <t-table-column title="操作" width="80">
            <template #default="{ row }">
              <t-button size="small" theme="danger" variant="text" @click.stop="confirmDeleteTask(row)">
                删除
              </t-button>
            </template>
          </t-table-column>
        </t-table>
        <t-empty v-if="!loading && tasks.length === 0" description="暂无评测任务，点击「新建评测」开始" />
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
            <t-radio value="rag_quality">rag_quality（RAGAS，慢）</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item v-if="form.suite === 'rag_bench'" label="Pipeline">
          <t-radio-group v-model="form.pipeline_profile">
            <t-radio value="rag_fixed">rag_fixed</t-radio>
            <t-radio value="rag_agent">rag_agent</t-radio>
          </t-radio-group>
        </t-form-item>
        <t-form-item label="抽样题数">
          <t-input-number v-model="form.sample_limit" :min="1" :max="500" placeholder="全部" theme="normal" />
        </t-form-item>
        <t-form-item label="并行度">
          <t-input-number v-model="form.workers" :min="1" :max="8" theme="normal" />
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

    <t-drawer v-model:visible="previewVisible" size="large" :header="preview?.id || '数据集预览'">
      <div v-if="previewLoading" class="preview-state">加载中…</div>
      <template v-else-if="preview">
        <p class="ds-desc">{{ preview.description }}</p>
        <div v-if="preview.id === 'squad_v2'" class="preview-toolbar">
          <t-button size="small" variant="outline" :loading="squadSyncing" @click="resyncSquadV2">
            重新同步 dev-v2.0
          </t-button>
          <t-select
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

    <t-drawer v-model:visible="detailVisible" size="large" :header="detailTask?.id || '任务详情'">
      <template v-if="detailTask">
        <t-descriptions :column="2" bordered size="small">
          <t-descriptions-item label="数据集">{{ detailTask.dataset_id }}</t-descriptions-item>
          <t-descriptions-item label="套件">{{ suiteLabel(detailTask.suite) }}</t-descriptions-item>
          <t-descriptions-item label="Pipeline">{{ detailTask.pipeline_profile }}</t-descriptions-item>
          <t-descriptions-item label="状态">
            <t-tag :theme="statusTheme(detailTask.status)">{{ statusLabel(detailTask.status) }}</t-tag>
          </t-descriptions-item>
          <t-descriptions-item label="耗时">{{ taskDurationLabel(detailTask) }}</t-descriptions-item>
          <t-descriptions-item label="进度">
            <div v-if="detailTask.total > 0 || isRagasScoring(detailTask)" class="detail-progress">
              <t-progress
                :percentage="progressPct(detailTask)"
                :label="progressLabel(detailTask)"
                size="small"
              />
            </div>
            <span v-else>—</span>
          </t-descriptions-item>
          <t-descriptions-item v-if="detailTask.err_msg" label="错误" :span="2">
            {{ detailTask.err_msg }}
          </t-descriptions-item>
        </t-descriptions>

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

        <h3 class="section-title">逐题明细（{{ filteredSamples.length }} / {{ sampleTotal }}）</h3>
        <t-radio-group v-model="sampleFilter" variant="default-filled" size="small" class="sample-filter">
          <t-radio-button value="all">全部</t-radio-button>
          <t-radio-button value="hasans">可答</t-radio-button>
          <t-radio-button value="noans">不可答</t-radio-button>
          <t-radio-button value="hit">检索命中</t-radio-button>
          <t-radio-button value="miss">检索未中</t-radio-button>
          <t-radio-button value="error">出错</t-radio-button>
        </t-radio-group>
        <t-table
          :data="pagedSamples"
          row-key="qid"
          size="small"
          max-height="420"
          :expanded-row-keys="expandedQids"
          @expand-change="onExpandChange"
        >
          <t-table-column title="#" col-key="qid" width="48" />
          <t-table-column title="问题" col-key="question" min-width="140" />
          <t-table-column title="标记" width="120">
            <template #default="{ row }">
              <t-tag v-if="row.error" size="small" theme="danger" variant="light">错</t-tag>
              <t-tag v-else-if="squadFlags(row).impossible" size="small" variant="light">NoAns</t-tag>
              <t-tag v-if="squadFlags(row).abstained" size="small" theme="warning" variant="light">拒答</t-tag>
              <t-tag v-if="retrievalHit(row) === true" size="small" theme="success" variant="light">命中</t-tag>
              <t-tag v-else-if="retrievalHit(row) === false" size="small" theme="danger" variant="light">未中</t-tag>
            </template>
          </t-table-column>
          <t-table-column title="Gold / 预测" col-key="response" min-width="140">
            <template #default="{ row }">
              <span v-if="detailTask?.suite === 'intent_bench'" class="clip">
                {{ row.reference || '—' }} → {{ row.response || row.error || '—' }}
              </span>
              <span v-else class="clip">{{ row.response || row.error || '—' }}</span>
            </template>
          </t-table-column>
          <t-table-column title="耗时" col-key="latency_ms" width="72">
            <template #default="{ row }">
              {{ row.latency_ms != null ? `${row.latency_ms}ms` : '—' }}
            </template>
          </t-table-column>
          <t-table-column title="指标" col-key="metrics" width="180">
            <template #default="{ row }">
              <span class="mono clip">{{ formatSampleMetrics(row.metrics) }}</span>
            </template>
          </t-table-column>
          <template #expanded-row="{ row }">
            <div class="expand-block">
              <p><strong>Gold：</strong>{{ row.reference || '（空）' }}</p>
              <p><strong>预测：</strong>{{ row.response || row.error || '—' }}</p>
              <p>
                <strong>检索：</strong>
                pred {{ (row.retrieval_ids || []).join(', ') || '—' }}
                / gt {{ (row.retrieval_gt || []).join(', ') || '—' }}
              </p>
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

.progress-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-progress {
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

.sample-filter {
  margin-bottom: 8px;
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
</style>
