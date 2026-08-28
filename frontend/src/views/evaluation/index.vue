<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  createEvalTask,
  getEvalTask,
  listEvalDatasets,
  listEvalSamples,
  listEvalTasks,
  uploadEvalDataset,
  type CreateEvalPayload,
  type EvalSample,
  type EvalTask,
  type EvalDatasetInfo,
} from '@/api/evaluation'
import EvalMetricCards from '@/components/EvalMetricCards.vue'
import {
  formatMetricsSummary,
  formatMetricValue,
  isRagasScoring,
  progressLabel,
  progressPercentage,
  taskPhase,
} from '@/utils/evalMetrics'

const loading = ref(false)
const tasks = ref<EvalTask[]>([])
const datasets = ref<EvalDatasetInfo[]>([])

const createVisible = ref(false)
const uploadVisible = ref(false)
const detailVisible = ref(false)
const detailTask = ref<EvalTask | null>(null)
const detailSamples = ref<EvalSample[]>([])

const form = ref<CreateEvalPayload>({
  dataset_id: 'campus_demo',
  suite: 'rag_bench',
  pipeline_profile: 'rag_fixed',
  sample_limit: undefined,
  workers: 2,
})

const uploadJson = ref('')
const uploadExample = `{
  "id": "my_corpus",
  "corpus_mode": "shared",
  "passages": [
    { "pid": 0, "title": "文档A", "text": "..." }
  ],
  "items": [
    { "qid": 0, "question": "...", "pids": [0], "answer": "..." }
  ]
}`

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
  return typeof n === 'number' ? n : detailSamples.value.length || null
})

function progressPct(t: EvalTask) {
  return progressPercentage(t)
}

function formatMetrics(summary: Record<string, unknown> | null) {
  return formatMetricsSummary(summary)
}

function formatSampleMetrics(metrics: Record<string, unknown> | null | undefined): string {
  if (!metrics) return '—'
  const rag = metrics.ragas as Record<string, number> | undefined
  const ret = metrics.retrieval as Record<string, number> | undefined
  const gen = metrics.generation as Record<string, number> | undefined
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
  if (gen) parts.push(`R1 ${formatMetricValue(gen.rouge1)}`)
  if (rag && Object.keys(rag).length) {
    parts.push(Object.entries(rag).map(([k, v]) => `${k.slice(0, 4)} ${formatMetricValue(v)}`).join(' '))
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
      const ragDs = datasets.value.find((d) => d.kind !== 'intent')
      if (ragDs) form.value.dataset_id = ragDs.id
    }
  },
)

async function loadTasks() {
  loading.value = true
  try {
    const res = await listEvalTasks()
    tasks.value = res.items
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function loadDatasets() {
  try {
    datasets.value = await listEvalDatasets()
    if (!datasets.value.find((d) => d.id === form.value.dataset_id) && datasets.value.length) {
      form.value.dataset_id = datasets.value[0].id
    }
  } catch (e) {
    MessagePlugin.error((e as Error).message)
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
    await loadTasks()
    startPolling()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

async function submitUpload() {
  try {
    const parsed = JSON.parse(uploadJson.value || uploadExample)
    const saved = await uploadEvalDataset(parsed)
    MessagePlugin.success(`数据集 ${saved.id} 已上传`)
    uploadVisible.value = false
    uploadJson.value = ''
    await loadDatasets()
    form.value.dataset_id = saved.id
  } catch (e) {
    MessagePlugin.error(e instanceof SyntaxError ? 'JSON 格式错误' : (e as Error).message)
  }
}

async function onRowClick(ctx: { row: EvalTask }) {
  await openDetail(ctx.row)
}

async function openDetail(t: EvalTask) {
  detailTask.value = t
  detailVisible.value = true
  try {
    detailTask.value = await getEvalTask(t.id)
    const samples = await listEvalSamples(t.id)
    detailSamples.value = samples.items
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

function startPolling() {
  stopPolling()
  if (!hasRunning.value) return
  pollTimer = setInterval(async () => {
    await loadTasks()
    if (detailVisible.value && detailTask.value) {
      const id = detailTask.value.id
      if (tasks.value.find((t) => t.id === id && (t.status === 'running' || t.status === 'pending'))) {
        detailTask.value = await getEvalTask(id)
        const samples = await listEvalSamples(id)
        detailSamples.value = samples.items
      }
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
        <p class="subtitle">rag_bench / rag_quality（RAGAS）/ intent_bench（意图识别）批量测评</p>
      </div>
      <div class="header-actions">
        <t-button variant="outline" @click="uploadVisible = true">上传数据集</t-button>
        <t-button theme="primary" @click="createVisible = true">新建评测</t-button>
      </div>
    </header>

    <t-alert theme="info" class="tip">
      评测任务由 Celery 异步执行。请在本机运行：
      <code>celery -A api.celery_app.celery worker -B --loglevel=info</code>
    </t-alert>

    <t-table
      :data="tasks"
      :loading="loading"
      row-key="id"
      hover
      stripe
      @row-click="onRowClick"
    >
      <t-table-column title="任务 ID" col-key="id" width="280">
        <template #default="{ row }">
          <span class="mono">{{ row.id }}</span>
        </template>
      </t-table-column>
      <t-table-column title="数据集" col-key="dataset_id" width="120" />
      <t-table-column title="套件" col-key="suite" width="110">
        <template #default="{ row }">{{ suiteLabel(row.suite) }}</template>
      </t-table-column>
      <t-table-column title="状态" col-key="status" width="100">
        <template #default="{ row }">
          <t-tag :theme="statusTheme(row.status)" variant="light">{{ statusLabel(row.status) }}</t-tag>
        </template>
      </t-table-column>
      <t-table-column title="进度" col-key="finished" width="180">
        <template #default="{ row }">
          <div v-if="row.total > 0 || isRagasScoring(row)" class="progress-cell">
            <t-progress
              :percentage="progressPct(row)"
              :label="progressLabel(row)"
              size="small"
              :status="isRagasScoring(row) ? 'active' : undefined"
              :class="{ 'ragas-progress': isRagasScoring(row) }"
            />
            <span v-if="taskPhase(row) === 'agent' && row.suite === 'rag_quality'" class="phase-tag">Agent</span>
            <span v-else-if="isRagasScoring(row)" class="phase-tag ragas">RAGAS</span>
          </div>
          <span v-else>—</span>
        </template>
      </t-table-column>
      <t-table-column title="指标摘要" col-key="metric_summary" min-width="240">
        <template #default="{ row }">
          <span class="metrics-cell">{{ formatMetrics(row.metric_summary) }}</span>
        </template>
      </t-table-column>
      <t-table-column title="创建时间" col-key="created_at" width="170">
        <template #default="{ row }">{{ row.created_at?.replace('T', ' ').slice(0, 19) || '—' }}</template>
      </t-table-column>
    </t-table>

    <t-empty v-if="!loading && tasks.length === 0" description="暂无评测任务，点击「新建评测」开始" />

    <!-- 新建评测 -->
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

    <!-- 上传数据集 -->
    <t-dialog v-model:visible="uploadVisible" header="上传 JSON 数据集" width="640px" @confirm="submitUpload">
      <p class="upload-hint">
        RAG：id、passages[{pid,title,text}]、items[{qid,question,pids,answer}]；
        意图：passages 可为 []，items 须含 intent_gt 或 meta.intent_gt
      </p>
      <t-textarea v-model="uploadJson" :placeholder="uploadExample" :autosize="{ minRows: 12, maxRows: 20 }" />
    </t-dialog>

    <!-- 任务详情 -->
    <t-drawer v-model:visible="detailVisible" size="large" :header="detailTask?.id || '任务详情'">
      <template v-if="detailTask">
        <t-descriptions :column="2" bordered size="small">
          <t-descriptions-item label="数据集">{{ detailTask.dataset_id }}</t-descriptions-item>
          <t-descriptions-item label="套件">{{ suiteLabel(detailTask.suite) }}</t-descriptions-item>
          <t-descriptions-item label="Pipeline">{{ detailTask.pipeline_profile }}</t-descriptions-item>
          <t-descriptions-item label="状态">
            <t-tag :theme="statusTheme(detailTask.status)">{{ statusLabel(detailTask.status) }}</t-tag>
          </t-descriptions-item>
          <t-descriptions-item label="进度" :span="2">
            <div v-if="detailTask.total > 0 || isRagasScoring(detailTask)" class="detail-progress">
              <t-progress
                :percentage="progressPct(detailTask)"
                :label="progressLabel(detailTask)"
                size="small"
                :status="isRagasScoring(detailTask) ? 'active' : undefined"
                :class="{ 'ragas-progress': isRagasScoring(detailTask) }"
              />
            </div>
            <span v-else>—</span>
          </t-descriptions-item>
          <t-descriptions-item v-if="detailTask.err_msg" label="错误" :span="2">
            {{ detailTask.err_msg }}
          </t-descriptions-item>
        </t-descriptions>

        <h3 class="section-title">汇总指标</h3>
        <EvalMetricCards
          :summary="detailTask.metric_summary"
          :sample-count="detailSampleCount"
        />

        <h3 class="section-title">逐题明细（{{ detailSamples.length }}）</h3>
        <t-table :data="detailSamples" row-key="qid" size="small" max-height="420">
          <t-table-column title="#" col-key="qid" width="48" />
          <t-table-column title="问题" col-key="question" min-width="140" />
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
        </t-table>
      </template>
    </t-drawer>
  </div>
</template>

<style scoped>
.page {
  height: 100%;
  overflow: auto;
  padding: 24px 28px;
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
  font-size: 22px;
  font-weight: 600;
}

.subtitle {
  margin: 6px 0 0;
  color: var(--td-text-color-secondary);
  font-size: 13px;
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

.phase-tag {
  font-size: 11px;
  color: var(--td-text-color-secondary);
  align-self: flex-start;
}

.phase-tag.ragas {
  color: var(--td-brand-color, #0052d9);
  font-weight: 500;
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

.json-block {
  background: var(--td-gray-bg-color, #f5f5f5);
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  overflow: auto;
  max-height: 200px;
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
</style>
