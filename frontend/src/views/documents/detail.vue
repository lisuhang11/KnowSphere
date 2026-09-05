<script setup lang="ts">
/**
 * 文档详情抽屉（三视图：原文预览 / 合并全文 / 分块列表）：
 * - 抽屉形态：列表页点击文档后滑出，关闭即返回列表
 * - 三视图：原文预览（preview）/ 合并全文（merged）/ 分块视图（chunks）
 * - merged 按 chunk_index 顺序拼接，分块重叠做保守精确匹配去重。
 *   注意：KnowSphere 入库分块不含 start_at/end_at 位置元数据，无法像
 *   后端 chunk 无 start_at/end_at，无法按位置重叠还原原文，只能做边界精确匹配（不会误删内容，
 *   极端情况下会残留少量重复，属预期）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  documentFileUrl,
  getDocumentMeta,
  getChunkById,
  listChunks,
  reparseDocument,
  type ChunkInfo,
  type ChunkingProcessConfig,
} from '@/api/documents'
import { getKnowledgeBase, type KnowledgeBase } from '@/api/knowledgeBases'
import { renderMarkdown } from '@/utils/markdown'
import { resolveAttachmentPreviewKind } from '@/utils/attachmentPreview'
import DocumentFilePreview from '@/components/DocumentFilePreview.vue'
import ChunkPreviewDrawer from './components/ChunkPreviewDrawer.vue'
import ParentChildChunkingFields from '@/components/ParentChildChunkingFields.vue'
import { STRATEGY_OPTIONS, CHUNK_DEFAULTS } from '@/constants/chunking'
import {
  buildProcessConfig,
  docToChunkingForm,
  validateChunkingForm,
  type ChunkingFormState,
} from '@/utils/chunkingConfig'

// 切块密度高，每页 20 条
const CHUNK_PAGE_SIZE = 20
// merged 视图全量拉取的页大小（后端单页上限 100）
const FULL_PAGE_SIZE = 100

const route = useRoute()
const router = useRouter()
const documentId = route.params.documentId as string

interface DocMeta {
  document_id: string
  file_name: string
  chunk_count: number
  updated_at: string | null
  /** 解析状态（pending/processing/completed/failed/cancelled，异步处理驱动） */
  status?: string | null
  /** 失败原因（处理失败 / 超时兜底） */
  error_message?: string | null
  /** 当前处理阶段（诊断用） */
  stage?: string | null
  /** 文档级处理配置（只含显式字段，空=跟随库默认） */
  process_config?: ChunkingProcessConfig | null
  /** 实际生效的切分 tier（heading/heuristic/legacy） */
  applied_strategy?: string | null
  /** 所属知识库（重新解析需原文件 + 库配置兜底） */
  knowledge_base_id?: number | null
  /** MinIO 原文件 key；评测灌库没有原件时为空 */
  stored_name?: string | null
}

/** 解析状态徽标（与列表页一致的文案/主题） */
const STATUS_META: Record<string, { label: string; theme: 'default' | 'warning' | 'success' | 'danger' }> = {
  pending: { label: '等待处理', theme: 'default' },
  processing: { label: '处理中', theme: 'warning' },
  completed: { label: '已完成', theme: 'success' },
  failed: { label: '处理失败', theme: 'danger' },
  cancelled: { label: '已取消', theme: 'default' },
}

function statusMeta(s?: string | null) {
  return STATUS_META[s ?? 'pending'] ?? STATUS_META.pending
}

/* 切块策略选项 */
const strategyOptions = STRATEGY_OPTIONS

const TIER_LABELS: Record<string, string> = {
  heading: 'heading · 按标题',
  heuristic: 'heuristic · 启发式',
  legacy: 'recursive · 递归字符（兜底）',
  recursive: 'recursive · 递归字符',
}

function tierLabelLocal(t: string | null | undefined): string {
  if (!t) return '跟随库默认'
  return TIER_LABELS[t] ?? t
}

type ViewMode = 'preview' | 'merged' | 'chunks'

const drawerVisible = ref(false)
const viewMode = ref<ViewMode>('merged')

const docInfo = ref<DocMeta | null>(null)
const loading = ref(false)
const chunks = ref<ChunkInfo[]>([])
const total = ref(0)
const page = ref(1)

// merged 视图全量缓存（分块视图保持分页，合并全文需要完整顺序）
const allChunks = ref<ChunkInfo[]>([])
const mergedLoading = ref(false)

const chunkingVisible = ref(false)

/* ---------- 重新解析（换配置重切 + 重新向量化） ---------- */
const reparseVisible = ref(false)
const reparseLoading = ref(false)
const kbForReparse = ref<KnowledgeBase | null>(null)
const reparseForm = ref<ChunkingFormState>({
  strategy: 'auto',
  chunkSize: CHUNK_DEFAULTS.chunkSize,
  chunkOverlap: CHUNK_DEFAULTS.chunkOverlap,
  enableParentChild: CHUNK_DEFAULTS.enableParentChild,
  parentChunkSize: CHUNK_DEFAULTS.parentChunkSize,
  childChunkSize: CHUNK_DEFAULTS.childChunkSize,
})

/** 分块列表是否包含 parent_text 父块 */
const showParentText = ref(false)
/** 父块上下文 lazy-load 缓存（parent_chunk_id → content） */
const parentContextCache = ref(new Map<number, string>())
const parentLoadingId = ref<number | null>(null)

async function openReparse() {
  if (!docInfo.value?.knowledge_base_id) {
    MessagePlugin.warning('文档未归属知识库，无法重新解析')
    return
  }
  try {
    kbForReparse.value = await getKnowledgeBase(docInfo.value.knowledge_base_id)
  } catch {
    kbForReparse.value = null
  }
  reparseForm.value = docToChunkingForm(
    docInfo.value.process_config?.chunking_config,
    kbForReparse.value,
  )
  reparseVisible.value = true
}

function buildReparseConfig(): ChunkingProcessConfig | null {
  const kb = kbForReparse.value
  if (!kb) return null
  return buildProcessConfig(reparseForm.value, kb)
}

async function confirmReparse() {
  const err = validateChunkingForm(reparseForm.value)
  if (err) {
    MessagePlugin.warning(err)
    return
  }
  if (docInfo.value?.status === 'pending' || docInfo.value?.status === 'processing') {
    MessagePlugin.warning('文档正在处理中，请等待完成后再重新解析')
    return
  }
  reparseLoading.value = true
  try {
    // 异步 reparse：202 即返回，后台任务驱动 processing → completed/failed
    await reparseDocument(documentId, buildReparseConfig())
    MessagePlugin.success('已提交重新解析，完成后自动刷新')
    reparseVisible.value = false
    await refreshUntilSettled()
  } catch (e) {
    MessagePlugin.error(`提交失败: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    reparseLoading.value = false
  }
}

/** 短轮询直到文档进入终态，终态后刷新分块视图 */
async function refreshUntilSettled() {
  const poll = async () => {
    try {
      docInfo.value = await getDocumentMeta(documentId)
    } catch {
      /* 保留旧数据继续轮询 */
    }
    const status = docInfo.value?.status
    if (status === 'pending' || status === 'processing') {
      setTimeout(() => void poll(), 1500)
      return
    }
    // 终态：刷新分块数据
    page.value = 1
    total.value = 0
    allChunks.value = []
    await loadChunks()
    if (viewMode.value === 'merged') loadAllChunks()
    if (status === 'failed') {
      MessagePlugin.error(docInfo.value?.error_message || '重新解析失败')
    } else if (status === 'cancelled') {
      MessagePlugin.warning('解析已取消')
    } else if (status === 'completed') {
      MessagePlugin.success(`重新解析完成：${docInfo.value?.applied_strategy ?? '默认配置'}，共 ${docInfo.value?.chunk_count ?? 0} 个分块`)
    }
  }
  await poll()
}

async function loadChunks() {
  loading.value = true
  try {
    const res = await listChunks(documentId, page.value, CHUNK_PAGE_SIZE, showParentText.value)
    chunks.value = res.chunks
    total.value = res.total
  } finally {
    loading.value = false
  }
}

watch(showParentText, () => {
  page.value = 1
  loadChunks()
})

function hasParentChunk(chunk: ChunkInfo): boolean {
  return chunk.parent_chunk_id != null && chunk.parent_chunk_id > 0
}

async function loadParentContext(parentId: number): Promise<string> {
  if (parentContextCache.value.has(parentId)) {
    return parentContextCache.value.get(parentId) ?? ''
  }
  parentLoadingId.value = parentId
  try {
    const row = await getChunkById(parentId)
    const content = row.content || ''
    parentContextCache.value.set(parentId, content)
    return content
  } catch {
    MessagePlugin.error('父块上下文加载失败')
    return ''
  } finally {
    parentLoadingId.value = null
  }
}

function onPageChange(p: number) {
  page.value = p
  loadChunks()
}

/** 全量拉取所有分块（循环分页），供合并全文使用 */
async function loadAllChunks() {
  if (total.value > 0 && allChunks.value.length >= total.value) return
  mergedLoading.value = true
  try {
    const all: ChunkInfo[] = []
    const first = await listChunks(documentId, 1, FULL_PAGE_SIZE)
    all.push(...first.chunks)
    const pages = Math.ceil(first.total / FULL_PAGE_SIZE)
    for (let p = 2; p <= pages; p++) {
      const res = await listChunks(documentId, p, FULL_PAGE_SIZE)
      all.push(...res.chunks)
    }
    allChunks.value = all.sort((a, b) => a.chunk_index - b.chunk_index)
  } finally {
    mergedLoading.value = false
  }
}

// 切入合并全文视图时懒加载全量分块
watch(viewMode, (mode) => {
  if (mode === 'merged' || (mode === 'preview' && !canPreviewOriginal.value)) loadAllChunks()
})

function isMarkdown(name: string): boolean {
  return /\.(md|markdown)$/i.test(name)
}


/** 是否有上传到对象存储的原始文件（评测灌库只有切块，没有 PDF/Word 原件） */
const hasOriginalFile = computed(() => Boolean(docInfo.value?.stored_name))

/** 是否支持「原文预览」Tab（PDF/Office/图片/文本等，对齐 WeKnora document-preview） */
const canPreviewOriginal = computed(() => {
  if (!docInfo.value || !hasOriginalFile.value) return false
  return resolveAttachmentPreviewKind(docInfo.value.file_name) !== 'unsupported'
})

const isEvalPassage = computed(() => documentId.startsWith('eval_passage_'))

const filePreviewUrl = computed(() => documentFileUrl(documentId))

function fmtTime(v: string | null): string {
  if (!v) return '-'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN', { hour12: false })
}

/* ---------- 合并全文（保守精确匹配去重） ---------- */
// 后端切块重叠 15%（600 字 → 约 90 字），窗口留足余量
const OVERLAP_WINDOW = 200
// 低于该长度的边界匹配视为误撞（如共用标题/分隔行），忽略
const MIN_OVERLAP = 12

function overlapLen(prev: string, next: string): number {
  const limit = Math.min(OVERLAP_WINDOW, prev.length, next.length)
  for (let len = limit; len >= MIN_OVERLAP; len--) {
    if (prev.slice(-len) === next.slice(0, len)) return len
  }
  return 0
}

const mergedContent = computed(() => {
  if (!allChunks.value.length) return ''
  let merged = ''
  for (const c of allChunks.value) {
    const content = c.content
    if (!content) continue
    if (!merged) {
      merged = content
      continue
    }
    const ol = overlapLen(merged, content)
    merged += ol > 0 ? content.slice(ol) : '\n\n' + content
  }
  return merged
})

/* ---------- 初始化 ---------- */
onMounted(async () => {
  drawerVisible.value = true
  try {
    docInfo.value = await getDocumentMeta(documentId)
    if (docInfo.value?.knowledge_base_id) {
      try {
        kbForReparse.value = await getKnowledgeBase(docInfo.value.knowledge_base_id)
      } catch {
        kbForReparse.value = null
      }
    }
  } catch {
    /* 元信息加载失败不阻塞切块展示 */
  }

  loadChunks()

  if (docInfo.value && canPreviewOriginal.value) {
    viewMode.value = 'preview'
  } else {
    void loadAllChunks()
  }
})

function handleClose() {
  drawerVisible.value = false
  // 有历史记录则回退，直达链接（无历史）回所属知识库；没有 /documents 列表路由
  if (window.history.length > 1) {
    router.back()
  } else if (docInfo.value?.knowledge_base_id) {
    router.push(`/knowledge-bases/${docInfo.value.knowledge_base_id}`)
  } else {
    router.push('/knowledge-bases')
  }
}
</script>

<template>
  <div class="document-detail-page">
    <ChunkPreviewDrawer v-model="chunkingVisible" />
    <t-drawer
      v-model:visible="drawerVisible"
      attach="body"
      size="860px"
      placement="right"
      :footer="false"
      :close-on-overlay-click="true"
      class="document-detail-drawer"
      @close="handleClose"
    >
      <template #header>
        <div class="drawer-header">
          <div class="header-left">
            <div class="header-title">{{ docInfo?.file_name ?? documentId }}</div>
            <div class="header-meta">
              <t-tag v-if="docInfo" :theme="statusMeta(docInfo.status).theme" variant="light" size="small">
                {{ statusMeta(docInfo.status).label }}
              </t-tag>
              <t-tag v-if="docInfo && docInfo.status === 'completed'" theme="success" variant="light" size="small">
                {{ docInfo.chunk_count }} 块
              </t-tag>
              <t-tag v-if="docInfo && docInfo.status !== 'completed'" size="small" variant="light" theme="default">
                {{ docInfo.chunk_count }} 块
              </t-tag>
              <t-tag
                v-if="docInfo?.process_config?.chunking_config"
                size="small"
                variant="light"
                theme="warning"
              >
                文档配置
              </t-tag>
              <t-tag v-if="docInfo" size="small" variant="light" theme="brand">
                {{ tierLabelLocal(docInfo.applied_strategy) }}
              </t-tag>
              <t-tag
                v-if="kbForReparse?.enable_parent_child || docInfo?.process_config?.chunking_config?.enable_parent_child"
                size="small"
                variant="light"
                theme="warning"
              >
                父子分块
              </t-tag>
              <span v-if="docInfo?.updated_at" class="header-time">{{ fmtTime(docInfo.updated_at) }}</span>
              <span class="header-id">#{{ documentId }}</span>
            </div>
          </div>
          <div class="header-actions">
            <t-button
              variant="outline"
              size="small"
              :disabled="
                !docInfo?.knowledge_base_id ||
                !hasOriginalFile ||
                docInfo?.status === 'pending' ||
                docInfo?.status === 'processing'
              "
              :title="
                hasOriginalFile
                  ? '用新的切块配置重新解析（保留 document_id，旧分块全删重切；处理中不可操作）'
                  : '评测灌库没有原始文件，无法重新解析'
              "
              @click="openReparse"
            >
              <template #icon><t-icon name="refresh" /></template>
              重新解析
            </t-button>
            <t-button variant="outline" size="small" @click="chunkingVisible = true">
              <template #icon><t-icon name="play-circle" /></template>
              测试切块
            </t-button>
          </div>
        </div>
      </template>

      <div class="doc-drawer-body">
        <!-- 视图切换 -->
        <div class="view-mode-buttons">
          <t-button
            size="small"
            :variant="viewMode === 'preview' ? 'base' : 'outline'"
            :theme="viewMode === 'preview' ? 'primary' : 'default'"
            @click="viewMode = 'preview'"
          >原文预览</t-button>
          <t-button
            size="small"
            :variant="viewMode === 'merged' ? 'base' : 'outline'"
            :theme="viewMode === 'merged' ? 'primary' : 'default'"
            @click="viewMode = 'merged'"
          >合并全文</t-button>
          <t-button
            size="small"
            :variant="viewMode === 'chunks' ? 'base' : 'outline'"
            :theme="viewMode === 'chunks' ? 'primary' : 'default'"
            @click="viewMode = 'chunks'"
          >分块视图</t-button>
        </div>

        <div class="doc-view-panel">
          <!-- 原文预览 -->
          <div v-if="viewMode === 'preview'" class="doc-view-panel__preview">
            <DocumentFilePreview
              v-if="canPreviewOriginal"
              :file-url="filePreviewUrl"
              :file-name="docInfo?.file_name ?? documentId"
              :active="viewMode === 'preview'"
              fill-height
            />
            <div v-else class="doc-view-panel__scroll">
              <div v-if="mergedLoading" class="state-block">
                <t-loading size="small" />
                <span>正文加载中…</span>
              </div>
              <div v-else-if="!mergedContent" class="empty-hint">
                {{ isEvalPassage ? '评测灌库没有原始文件，且还没有切块。' : '该格式暂不支持原文预览，可查看「合并全文」或「分块视图」' }}
              </div>
              <template v-else>
                <div class="merged-hint">
                  {{
                    isEvalPassage
                      ? '评测灌库只写入切块，没有上传 PDF/Word 原件。以下是按分块还原的段落正文。'
                      : '没有原始文件，以下为按分块还原的正文。'
                  }}
                </div>
                <pre class="plain-text">{{ mergedContent }}</pre>
              </template>
            </div>
          </div>

          <!-- 合并全文 -->
          <div v-else-if="viewMode === 'merged'" class="doc-view-panel__scroll">
            <div v-if="mergedLoading" class="state-block">
              <t-loading size="small" />
              <span>全文加载中…</span>
            </div>
            <div v-else-if="!mergedContent" class="empty-hint">暂无分块数据</div>
            <template v-else>
              <div
                v-if="docInfo && isMarkdown(docInfo.file_name)"
                class="markdown-body"
                v-html="renderMarkdown(mergedContent)"
              />
              <pre v-else class="plain-text">{{ mergedContent }}</pre>
              <div class="merged-hint">
                按入库分块顺序拼接，重叠部分已做保守去重；因无位置元数据，可能与原文存在细微差异
              </div>
            </template>
          </div>

          <!-- 分块视图 -->
          <div v-else class="doc-view-panel__scroll">
            <div class="chunk-toolbar">
              <span class="chunk-count-line">按入库顺序 · 共 {{ total }} 块</span>
              <t-checkbox v-model="showParentText">显示父块</t-checkbox>
            </div>
            <div v-if="loading" class="state-block">
              <t-loading size="small" />
              <span>分块加载中…</span>
            </div>
            <div v-else-if="!chunks.length" class="empty-hint">暂无分块数据</div>
            <div v-else class="chunk-list">
              <div
                v-for="(chunk, index) in chunks"
                :key="chunk.id"
                class="chunk-item"
                :class="{ 'chunk-parent': chunk.chunk_type === 'parent_text' }"
              >
                <div class="chunk-header">
                  <span class="chunk-index">
                    {{ chunk.chunk_type === 'parent_text' ? '父块' : '子块' }}
                    {{ (page - 1) * CHUNK_PAGE_SIZE + index + 1 }}
                  </span>
                  <span class="chunk-meta">{{ chunk.char_count }} 字 · ~{{ chunk.token_count }} tok</span>
                  <t-popup
                    v-if="hasParentChunk(chunk)"
                    trigger="click"
                    placement="left"
                    :overlay-style="{ maxWidth: '420px' }"
                    @visible-change="(v: boolean) => v && chunk.parent_chunk_id && loadParentContext(chunk.parent_chunk_id)"
                  >
                    <t-button variant="text" size="small" title="查看父块上下文">
                      <template #icon><t-icon name="git-branch" /></template>
                    </t-button>
                    <template #content>
                      <div class="parent-popup">
                        <div class="parent-popup-title">父块上下文</div>
                        <div v-if="parentLoadingId === chunk.parent_chunk_id" class="parent-popup-loading">
                          <t-loading size="small" /> 加载中…
                        </div>
                        <pre v-else class="parent-popup-body">{{
                          parentContextCache.get(chunk.parent_chunk_id!) ?? ''
                        }}</pre>
                      </div>
                    </template>
                  </t-popup>
                </div>
                <pre class="chunk-content">{{ chunk.content }}</pre>
              </div>
            </div>
            <div v-if="total > CHUNK_PAGE_SIZE" class="pagination-wrap">
              <t-pagination
                :current="page"
                :page-size="CHUNK_PAGE_SIZE"
                :total="total"
                :show-jumper="total / CHUNK_PAGE_SIZE > 8"
                @current-change="onPageChange"
              />
            </div>
          </div>
        </div>
      </div>
    </t-drawer>

    <!-- 重新解析弹窗：换配置重切（预填当前生效配置，未改动则沿用） -->
    <t-dialog
      v-model:visible="reparseVisible"
      header="重新解析"
      :confirm-btn="{ content: '重新解析', loading: reparseLoading, theme: 'primary' }"
      cancel-btn="取消"
      width="520px"
      @confirm="confirmReparse"
    >
      <p class="reparse-tip">
        将用以下配置重新切块并向量化，替换本文档全部 {{ docInfo?.chunk_count ?? '-' }} 个旧分块（保留 document_id / file_name，重新嵌入）。
      </p>
      <t-form label-align="top">
        <t-form-item label="切块策略" help="预填当前生效配置（文档级覆盖 + 库默认兜底）">
          <t-select v-model="reparseForm.strategy" :options="strategyOptions" />
        </t-form-item>
        <ParentChildChunkingFields v-model="reparseForm" />
      </t-form>
    </t-dialog>
  </div>
</template>

<style scoped>
.document-detail-page {
  /* 抽屉 attach=body，页面本身不承载布局 */
}

.doc-drawer-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.doc-view-panel {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.doc-view-panel__preview {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.doc-view-panel__scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-top: 14px;
}

/* 抽屉头部 */
.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}

.header-left {
  min-width: 0;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-meta {
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.header-id {
  color: var(--td-text-color-placeholder);
  font-family: Consolas, Monaco, monospace;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.reparse-tip {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--td-text-color-secondary);
}

.reparse-number-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

/* 视图切换 */
.view-mode-buttons {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0 14px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.chunk-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.chunk-count-line {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  margin-bottom: 0;
}

.chunk-parent {
  border-left: 3px solid var(--td-warning-color);
}

.parent-popup {
  padding: 8px;
  max-height: 360px;
  overflow: auto;
}

.parent-popup-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.parent-popup-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.parent-popup-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.plain-text {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--td-text-color-primary);
}

.merged-hint {
  margin-top: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.truncated-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--td-warning-color);
}

.state-block {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 24px 0;
  color: var(--td-text-color-secondary);
  font-size: 13px;
}

.empty-hint {
  padding: 24px 0;
  text-align: center;
  color: var(--td-text-color-placeholder);
  font-size: 13px;
}

/* 分块列表 */
.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chunk-item {
  border-radius: 6px;
  padding: 12px 14px;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-border);
}

.chunk-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 24px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.chunk-index {
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
}

.chunk-meta {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
  font-family: Consolas, Monaco, monospace;
}

.chunk-content {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--td-text-color-primary);
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>

<style lang="less">
.t-drawer.document-detail-drawer {
  .t-drawer__content-wrapper,
  .t-drawer__content {
    height: 100%;
  }

  .t-drawer__header {
    padding: 14px 18px;
    border-bottom: 1px solid var(--td-component-stroke);
    flex-shrink: 0;
  }

  .t-drawer__body {
    flex: 1;
    min-height: 0;
    padding: 12px 16px 16px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
}
</style>
