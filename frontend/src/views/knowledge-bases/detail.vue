<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DialogPlugin, MessagePlugin, type MessageInstance } from 'tdesign-vue-next'
import {
  cancelDocument,
  deleteDocument,
  documentImageUrl,
  isStatusInFlight,
  listDocuments,
  reparseDocument,
  uploadDocument,
  type ChunkingProcessConfig,
  type DocumentInfo,
  type DocumentStatus,
} from '@/api/documents'
import {
  getKnowledgeBase,
  moveDocument,
  updateKnowledgeBase,
  listKnowledgeBases,
  type KnowledgeBase,
} from '@/api/knowledgeBases'
import UploadConfirmDialog from './components/UploadConfirmDialog.vue'
import ParentChildChunkingFields from '@/components/ParentChildChunkingFields.vue'
import { STRATEGY_OPTIONS, strategyLabel as strategyOptionLabel, CHUNK_DEFAULTS } from '@/constants/chunking'
import type { ChunkingFormState } from '@/utils/chunkingConfig'
import { validateChunkingForm } from '@/utils/chunkingConfig'

const route = useRoute()
const router = useRouter()

const kbId = computed(() => Number(route.params.kbId))
const kb = ref<KnowledgeBase | null>(null)
const loading = ref(false)
const docs = ref<DocumentInfo[]>([])

const columns = [
  { colKey: 'file_name', title: '文件名', ellipsis: true },
  { colKey: 'status', title: '状态', width: 130, align: 'center' as const },
  { colKey: 'chunk_count', title: '分块数', width: 110, align: 'center' as const },
  { colKey: 'updated_at', title: '更新时间', width: 190 },
  { colKey: 'op', title: '操作', width: 280 },
]

/** 解析状态徽标 */
const STATUS_META: Record<DocumentStatus, { label: string; theme: 'default' | 'warning' | 'success' | 'danger' }> = {
  pending: { label: '等待处理', theme: 'default' },
  processing: { label: '处理中', theme: 'warning' },
  completed: { label: '已完成', theme: 'success' },
  failed: { label: '处理失败', theme: 'danger' },
  cancelled: { label: '已取消', theme: 'default' },
}

function statusMeta(s?: DocumentStatus | null) {
  return STATUS_META[s ?? 'pending'] ?? STATUS_META.pending
}

/** 处理阶段徽标（parsing → chunking → embedding → indexing；reparse 首段为 reparsing） */
const STAGE_LABELS: Record<string, string> = {
  parsing: '解析中',
  chunking: '切块中',
  embedding: '向量化中',
  indexing: '写入索引中',
  reparsing: '重新解析中',
}

function stageLabel(s?: string | null): string {
  if (!s) return ''
  return STAGE_LABELS[s] ?? s
}

async function load() {
  loading.value = true
  try {
    kb.value = await getKnowledgeBase(kbId.value)
    docs.value = await listDocuments(kbId.value)
  } catch (e) {
    MessagePlugin.error(`加载知识库失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
  // 存在在途文档则启动轮询（上传/取消/重试后也会触发）
  pollInFlight()
}

watch(kbId, () => {
  if (Number.isFinite(kbId.value) && kbId.value > 0) void load()
})

/* ---------- 状态轮询：过滤在途项 → 定时刷新 → 递归续轮 ---------- */
let pollTimer: ReturnType<typeof setTimeout> | null = null

/** 存在在途项（pending/processing）则 1.5s 后静默刷新列表，递归直到全部终态 */
function pollInFlight() {
  if (pollTimer) clearTimeout(pollTimer)
  if (!docs.value.some((d) => isStatusInFlight(d.status))) return
  pollTimer = setTimeout(async () => {
    try {
      docs.value = await listDocuments(kbId.value)
    } catch {
      /* 轮询失败保留旧数据，下轮重试 */
    }
    pollInFlight()
  }, 1500)
}

onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})

/** 原生 input 作文件选择器：按钮 click() 触发，@change 同步收 File[] */
const selectedFiles = ref<File[]>([])
const uploadVisible = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)

function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  const files = input.files ? Array.from(input.files) : []
  if (!files.length) return
  const seen = new Set(selectedFiles.value.map((f) => `${f.name}-${f.size}-${f.lastModified}`))
  for (const f of files) {
    const key = `${f.name}-${f.size}-${f.lastModified}`
    if (!seen.has(key)) {
      seen.add(key)
      selectedFiles.value.push(f)
    }
  }
  input.value = '' // 允许再次选择同一文件
  if (selectedFiles.value.length) uploadVisible.value = true
}

/** 弹窗任何方式关闭（取消/遮罩/X/确认后）都清空已选文件，避免残留状态 */
function onUploadClose() {
  // localFiles 实时跟随 props.files：延迟清空，避免弹窗关闭动画期间文件列表闪烁为空
  setTimeout(() => {
    selectedFiles.value = []
  }, 300)
}

/** 弹窗确认即返回（已关闭），上传在后台逐个执行，完成后自动刷新列表 */
function onUploadConfirm(payload: { kbId: number; files: File[]; processConfig: ChunkingProcessConfig | null }) {
  // 上传用的是 payload 快照，清空交给 onUploadClose 的延迟逻辑（确认后 t-dialog 会触发 close）
  void runUploads(payload)
}

/** 异步上传：HTTP 202 即返回，文档状态由后端驱动，列表轮询自动跟进 */
let uploadLoadingMsg: MessageInstance | null = null
function closeUploadLoadingMsg() {
  uploadLoadingMsg?.close()
  uploadLoadingMsg = null
}

async function runUploads(payload: { kbId: number; files: File[]; processConfig: ChunkingProcessConfig | null }) {
  const total = payload.files.length
  let ok = 0
  let fail = 0
  closeUploadLoadingMsg()
  if (total > 0) uploadLoadingMsg = await MessagePlugin.loading({ content: `正在提交 0/${total} …` })
  for (const [i, f] of payload.files.entries()) {
    try {
      await uploadDocument(f, payload.kbId, payload.processConfig)
      ok += 1
    } catch (e) {
      fail += 1
      closeUploadLoadingMsg()
      MessagePlugin.error({ content: `「${f.name}」提交失败: ${e instanceof Error ? e.message : String(e)}` })
    }
    closeUploadLoadingMsg()
    uploadLoadingMsg = await MessagePlugin.loading({ content: `正在提交 ${i + 1}/${total} …` })
  }
  closeUploadLoadingMsg()
  if (fail === 0) {
    MessagePlugin.success({ content: `${ok} 个文件已提交后台处理，列表状态自动更新（${payload.processConfig ? '按本次配置切块，随文档持久化' : '跟随库默认配置'}）` })
  } else {
    MessagePlugin.warning({ content: `提交完成：${ok} 成功 / ${fail} 失败` })
  }
  void load()
}

/** 取消解析：pending/processing → cancelled（保留已写分块，可重新解析） */
function confirmCancel(doc: DocumentInfo) {
  const dialog = DialogPlugin.confirm({
    header: '取消解析',
    body: `确定取消「${doc.file_name}」的解析？已写入的分块将保留，可稍后重新解析。`,
    confirmBtn: { content: '取消解析', theme: 'warning' },
    cancelBtn: '返回',
    onConfirm: async () => {
      try {
        await cancelDocument(doc.document_id)
        MessagePlugin.success('已取消解析')
        await load()
      } finally {
        dialog.destroy()
      }
    },
  })
}

/** 失败重试 / 取消后重新解析：任意终态 → processing（异步） */
async function retryReparse(doc: DocumentInfo) {
  try {
    await reparseDocument(doc.document_id)
    MessagePlugin.success(`已重新提交解析「${doc.file_name}」`)
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

/* 实际生效策略的展示标签（applied_strategy 为切分 tier，区别于 KB 默认策略） */
const TIER_LABELS: Record<string, string> = {
  heading: '按标题',
  heuristic: '启发式',
  legacy: '递归字符',
  recursive: '递归字符',
}

function strategyLabel(row: DocumentInfo): string {
  if (row.applied_strategy) return TIER_LABELS[row.applied_strategy] ?? row.applied_strategy
  return '跟随库默认'
}

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase()
  if (ext === 'pdf') return 'file-pdf'
  if (ext === 'md') return 'file-markdown'
  return 'file'
}

function fmtTime(v: string | null): string {
  if (!v) return '-'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN', { hour12: false })
}

/** 打开文档详情抽屉（独立路由承载，可直达/刷新） */
function goDetail(doc: DocumentInfo) {
  router.push(`/documents/${doc.document_id}`)
}

/* ---------- 内嵌图片预览（经后端鉴权代理 /documents/{id}/images/{name} 从 MinIO 读取） ---------- */
const imagePreviewVisible = ref(false)
const previewDoc = ref<DocumentInfo | null>(null)

function openImages(doc: DocumentInfo) {
  previewDoc.value = doc
  imagePreviewVisible.value = true
}

function confirmDelete(doc: DocumentInfo) {
  const dialog = DialogPlugin.confirm({
    header: '删除文档',
    body: doc.status === 'completed'
      ? `确定删除「${doc.file_name}」？其全部 ${doc.chunk_count} 个向量分块将一并删除。`
      : `确定删除「${doc.file_name}」？处理中的解析任务将终止，已写入的分块将一并删除。`,
    confirmBtn: { content: '删除', theme: 'danger' },
    cancelBtn: '取消',
    onConfirm: async () => {
      try {
        const res = await deleteDocument(doc.document_id)
        MessagePlugin.success(`已删除 ${res.deleted_chunks} 个分块`)
        await load()
      } finally {
        dialog.destroy()
      }
    },
  })
}

/* ---------- 编辑知识库弹窗 ---------- */
const editVisible = ref(false)
const editForm = ref({
  name: '',
  description: '',
  chunking: {
    strategy: 'auto',
    chunkSize: CHUNK_DEFAULTS.chunkSize,
    chunkOverlap: CHUNK_DEFAULTS.chunkOverlap,
    enableParentChild: CHUNK_DEFAULTS.enableParentChild,
    parentChunkSize: CHUNK_DEFAULTS.parentChunkSize,
    childChunkSize: CHUNK_DEFAULTS.childChunkSize,
  } as ChunkingFormState,
})
const saving = ref(false)

function openEdit() {
  if (!kb.value) return
  editForm.value = {
    name: kb.value.name,
    description: kb.value.description,
    chunking: {
      strategy: kb.value.chunk_strategy || 'auto',
      chunkSize: kb.value.chunk_size,
      chunkOverlap: kb.value.chunk_overlap,
      enableParentChild: kb.value.enable_parent_child ?? false,
      parentChunkSize: kb.value.parent_chunk_size ?? CHUNK_DEFAULTS.parentChunkSize,
      childChunkSize: kb.value.child_chunk_size ?? CHUNK_DEFAULTS.childChunkSize,
    },
  }
  editVisible.value = true
}

async function saveEdit() {
  const name = editForm.value.name.trim()
  if (!name) {
    MessagePlugin.warning('请输入知识库名称')
    return
  }
  const chunkErr = validateChunkingForm(editForm.value.chunking)
  if (chunkErr) {
    MessagePlugin.warning(chunkErr)
    return
  }
  saving.value = true
  try {
    const c = editForm.value.chunking
    await updateKnowledgeBase(kbId.value, {
      name,
      description: editForm.value.description.trim(),
      chunk_size: c.chunkSize,
      chunk_overlap: c.chunkOverlap,
      chunk_strategy: c.strategy as KnowledgeBase['chunk_strategy'],
      enable_parent_child: c.enableParentChild,
      parent_chunk_size: c.parentChunkSize,
      child_chunk_size: c.childChunkSize,
    })
    MessagePlugin.success('知识库已更新（分块参数与策略仅影响之后上传的文档）')
    editVisible.value = false
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

/* ---------- 移动文档弹窗 ---------- */
const moveVisible = ref(false)
const movingDoc = ref<DocumentInfo | null>(null)
const moveTarget = ref<number | null>(null)
const moveOptions = ref<{ label: string; value: number }[]>([])
const moving = ref(false)

async function openMove(doc: DocumentInfo) {
  movingDoc.value = doc
  moveTarget.value = null
  moveVisible.value = true
  try {
    const all = await listKnowledgeBases()
    moveOptions.value = all
      .filter((k) => k.id !== kbId.value)
      .map((k) => ({ label: k.name, value: k.id }))
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

async function confirmMove() {
  if (!movingDoc.value || !moveTarget.value) {
    MessagePlugin.warning('请选择目标知识库')
    return
  }
  moving.value = true
  try {
    const res = await moveDocument(movingDoc.value.document_id, moveTarget.value)
    MessagePlugin.success(`已移动 ${res.moved_chunks} 个分块到目标知识库`)
    moveVisible.value = false
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    moving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <!-- 页头：返回 + 库名 + 操作 -->
    <div class="page-header">
      <div class="header-title">
        <div class="header-back">
          <t-button variant="text" shape="square" title="返回知识库列表" @click="router.push('/knowledge-bases')">
            <template #icon><t-icon name="chevron-left" size="20px" /></template>
          </t-button>
          <h2>{{ kb?.name || '...' }}</h2>
          <t-tag v-if="kb" size="small" variant="light" theme="primary">{{ kb.document_count ?? 0 }} 文档 · {{ kb.chunk_count ?? 0 }} 分块</t-tag>
          <t-tag v-if="kb" size="small" variant="light" theme="warning" title="切块大小/重叠 · 知识库独立配置">
            <span v-if="kb.enable_parent_child">父子 {{ kb.parent_chunk_size }}/{{ kb.child_chunk_size }}</span>
            <span v-else>切块 {{ kb.chunk_size }}/{{ kb.chunk_overlap }}</span>
          </t-tag>
          <t-tag v-if="kb?.enable_parent_child" size="small" variant="light" theme="warning">父子分块</t-tag>
          <t-tag v-if="kb" size="small" variant="light" theme="brand" :title="`切块策略：${strategyOptionLabel(kb.chunk_strategy || 'auto')}`">
            {{ strategyOptionLabel(kb.chunk_strategy || 'auto') }}
          </t-tag>
          <t-tag v-if="kb" size="small" variant="light" theme="success" :title="`embedding 维度 ${kb.embedding_dim}`">
            {{ kb.embedding_model_id }}
          </t-tag>
        </div>
        <p class="header-subtitle">{{ kb?.description || '上传文档到本知识库，自动切块向量化入库，供对话检索引用' }}</p>
      </div>
      <div class="header-actions">
        <t-button variant="outline" @click="openEdit">
          <template #icon><t-icon name="edit" /></template>
          编辑
        </t-button>
        <t-button theme="primary" @click="fileInputRef?.click()">
          <template #icon><t-icon name="upload" /></template>
          上传文档
        </t-button>
        <input
          ref="fileInputRef"
          type="file"
          multiple
          accept=".pdf,.docx,.doc,.pptx,.xlsx,.md,.txt,.html,.htm,.jpg,.jpeg,.png,.gif,.bmp,.tiff,.webp"
          class="hidden-file-input"
          @change="onFileInputChange"
        />
      </div>
    </div>

    <!-- 文档表格 -->
    <div class="page-card">
      <t-table
        row-key="document_id"
        :data="docs"
        :columns="columns"
        :loading="loading"
        hover
        empty="暂无文档，点击右上角「上传文档」开始"
      >
        <template #file_name="{ row }">
          <div class="file-cell">
            <t-icon :name="fileIcon(row.file_name)" size="20px" class="file-icon" />
            <div class="file-meta">
              <span class="file-name link" title="查看文档详情" @click="goDetail(row)">{{ row.file_name }}</span>
              <span class="file-sub-row">
                <span class="file-id">{{ row.document_id.slice(0, 8) }}</span>
                <t-tag
                  v-if="row.process_config?.chunking_config"
                  size="small"
                  variant="light"
                  theme="warning"
                >
                  文档配置
                </t-tag>
                <t-tag size="small" variant="light" theme="brand">{{ strategyLabel(row) }}</t-tag>
                <t-tag
                  v-if="row.image_refs?.length"
                  size="small"
                  variant="light"
                  theme="success"
                  title="点击预览文档内嵌图片"
                  style="cursor: pointer"
                  @click.stop="openImages(row)"
                >
                  {{ row.image_refs.length }} 图
                </t-tag>
              </span>
            </div>
          </div>
        </template>
        <template #status="{ row }">
          <div class="status-cell">
            <t-tag :theme="statusMeta(row.status).theme" variant="light" size="small">
              {{ statusMeta(row.status).label }}
            </t-tag>
            <t-tag
              v-if="row.status === 'processing' && row.stage"
              theme="warning"
              variant="outline"
              size="small"
            >
              {{ stageLabel(row.stage) }}
            </t-tag>
            <t-tooltip
              v-if="row.status === 'failed' && row.error_message"
              :content="row.error_message"
              placement="top"
            >
              <t-icon name="error-circle" size="16px" class="err-icon" />
            </t-tooltip>
          </div>
        </template>
        <template #chunk_count="{ row }">
          <t-tag v-if="row.status === 'completed'" theme="success" variant="light" size="small">{{ row.chunk_count }} 块</t-tag>
          <span v-else class="time-cell">-</span>
        </template>
        <template #updated_at="{ row }">
          <span class="time-cell">{{ fmtTime(row.updated_at) }}</span>
        </template>
        <template #op="{ row }">
          <t-button variant="text" theme="primary" size="small" @click="goDetail(row)">查看</t-button>
          <t-button v-if="isStatusInFlight(row.status)" variant="text" theme="warning" size="small" @click="confirmCancel(row)">取消</t-button>
          <t-button v-if="row.status === 'failed' || row.status === 'cancelled'" variant="text" theme="primary" size="small" @click="retryReparse(row)">
            {{ row.status === 'failed' ? '重试' : '重新解析' }}
          </t-button>
          <t-button v-if="row.status === 'completed'" variant="text" theme="default" size="small" @click="openMove(row)">移动到</t-button>
          <t-button variant="text" theme="danger" size="small" @click="confirmDelete(row)">删除</t-button>
        </template>
      </t-table>
    </div>

    <!-- 编辑知识库弹窗 -->
    <t-dialog
      v-model:visible="editVisible"
      header="编辑知识库"
      :confirm-btn="{ content: '保存', loading: saving, theme: 'primary' }"
      cancel-btn="取消"
      width="520px"
      @confirm="saveEdit"
    >
      <t-form label-align="top">
        <t-form-item label="名称" required-mark>
          <t-input v-model="editForm.name" :maxlength="100" />
        </t-form-item>
        <t-form-item label="描述">
          <t-textarea
            v-model="editForm.description"
            :maxlength="500"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </t-form-item>
        <t-form-item label="切块策略" help="只影响之后上传的文档">
          <t-select v-model="editForm.chunking.strategy" :options="STRATEGY_OPTIONS" />
        </t-form-item>
        <ParentChildChunkingFields v-model="editForm.chunking" />
      </t-form>
    </t-dialog>

    <!-- 移动文档弹窗 -->
    <t-dialog
      v-model:visible="moveVisible"
      :header="`移动「${movingDoc?.file_name || ''}」`"
      :confirm-btn="{ content: '移动', loading: moving, theme: 'primary', disabled: !moveTarget }"
      cancel-btn="取消"
      width="480px"
      @confirm="confirmMove"
    >
      <t-form label-align="top">
        <t-form-item label="目标知识库">
          <t-select
            v-model="moveTarget"
            :options="moveOptions"
            placeholder="选择目标知识库"
            clearable
          />
        </t-form-item>
      </t-form>
    </t-dialog>

    <!-- 上传确认弹窗：只收集切块配置，确认后立即关闭，上传由本页在后台执行 -->
    <UploadConfirmDialog
      v-model:visible="uploadVisible"
      :kb="kb"
      :files="selectedFiles"
      @confirm="onUploadConfirm"
      @close="onUploadClose"
    />

    <!-- 内嵌图片预览（MinIO 经后端鉴权代理读取） -->
    <t-dialog
      v-model:visible="imagePreviewVisible"
      :header="`「${previewDoc?.file_name || ''}」内嵌图片（${previewDoc?.image_refs?.length ?? 0} 张）`"
      :footer="false"
      width="640px"
      destroy-on-close
    >
      <div v-if="previewDoc?.image_refs?.length" class="image-grid">
        <a
          v-for="ref in previewDoc.image_refs"
          :key="ref.filename"
          class="image-item"
          :href="documentImageUrl(previewDoc.document_id, ref.filename)"
          target="_blank"
          rel="noopener"
          :title="ref.filename"
        >
          <img
            :src="documentImageUrl(previewDoc.document_id, ref.filename)"
            :alt="ref.filename"
            loading="lazy"
          />
        </a>
      </div>
      <t-empty v-else description="该文档无内嵌图片" />
    </t-dialog>
  </div>
</template>

<style scoped>
.hidden-file-input {
  display: none;
}

.page {
  height: 100%;
  overflow-y: auto;
  padding: 20px 28px 16px;
  box-sizing: border-box;
}

.page-header {
  margin: 0 0 16px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.header-title {
  min-width: 0;
}

.header-back {
  display: flex;
  align-items: center;
  gap: 6px;
}

.header-back h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  line-height: 32px;
  color: var(--td-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-subtitle {
  margin: 4px 0 0 4px;
  font-size: 14px;
  line-height: 20px;
  color: var(--td-text-color-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  flex-shrink: 0;
  display: flex;
  gap: 8px;
  align-items: center;
}

.page-card {
  margin: 0;
  background: var(--td-bg-color-container);
  border-radius: 8px;
  padding: 8px 16px 16px;
  border: 1px solid var(--td-component-stroke);
}

.file-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
}

.file-icon {
  color: var(--td-brand-color);
  flex-shrink: 0;
}

.file-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.file-name {
  font-size: 14px;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-name.link {
  cursor: pointer;
}

.file-name.link:hover {
  color: var(--td-brand-color);
}

.file-id {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  font-family: Consolas, Monaco, monospace;
}

.file-sub-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-width: 0;
}

.time-cell {
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.status-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.err-icon {
  color: var(--td-error-color);
  cursor: help;
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}

.image-item img {
  width: 100%;
  height: 150px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid var(--td-border-level-1-color);
  background: var(--td-bg-color-container);
}
</style>
