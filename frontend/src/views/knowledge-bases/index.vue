<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { DialogPlugin, MessagePlugin } from 'tdesign-vue-next'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBases,
  updateKnowledgeBase,
  type KnowledgeBase,
} from '@/api/knowledgeBases'
import { listModels, type ModelInfo } from '@/api/models'
import ModelSelector from '@/components/ModelSelector.vue'
import ParentChildChunkingFields from '@/components/ParentChildChunkingFields.vue'
import { STRATEGY_OPTIONS, strategyLabel, CHUNK_DEFAULTS } from '@/constants/chunking'
import type { ChunkingFormState } from '@/utils/chunkingConfig'
import { validateChunkingForm } from '@/utils/chunkingConfig'
import {
  selectInitialModelId,
} from '@/utils/modelDefaults'
import { buildModelLabelMap, labelForModelId } from '@/utils/modelDisplay'

const router = useRouter()
const loading = ref(false)
const kbs = ref<KnowledgeBase[]>([])
const allModels = ref<ModelInfo[]>([])
const modelLabels = ref<Map<string, string>>(new Map())

/* ---------- 创建 / 编辑弹窗 ---------- */
const dialogVisible = ref(false)
const editingKb = ref<KnowledgeBase | null>(null)
const form = ref({
  name: '',
  description: '',
  embedding_model_id: '',
  summary_model_id: '',
  graph_enabled: false,
  asr_enabled: false,
  asr_model_id: '',
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

/* 切块策略选项 */
const strategyOptions = STRATEGY_OPTIONS

function defaultEmbeddingId(): string {
  return selectInitialModelId(allModels.value, 'Embedding') ?? ''
}

function defaultSummaryId(): string {
  return selectInitialModelId(allModels.value, 'KnowledgeQA') ?? ''
}

function embeddingLabel(id: string) {
  return labelForModelId(id, modelLabels.value)
}

async function loadModels() {
  try {
    const models = await listModels()
    allModels.value = models
    modelLabels.value = buildModelLabelMap(models)
  } catch {
    allModels.value = []
    modelLabels.value = new Map()
  }
}

function openCreate() {
  editingKb.value = null
  form.value = {
    name: '',
    description: '',
    embedding_model_id: defaultEmbeddingId(),
    summary_model_id: defaultSummaryId(),
    graph_enabled: false,
    asr_enabled: false,
    asr_model_id: '',
    chunking: {
      strategy: 'auto',
      chunkSize: CHUNK_DEFAULTS.chunkSize,
      chunkOverlap: CHUNK_DEFAULTS.chunkOverlap,
      enableParentChild: CHUNK_DEFAULTS.enableParentChild,
      parentChunkSize: CHUNK_DEFAULTS.parentChunkSize,
      childChunkSize: CHUNK_DEFAULTS.childChunkSize,
    },
  }
  dialogVisible.value = true
}

function openEdit(kb: KnowledgeBase) {
  editingKb.value = kb
  form.value = {
    name: kb.name,
    description: kb.description,
    embedding_model_id: kb.embedding_model_id,
    summary_model_id: kb.summary_model_id || defaultSummaryId(),
    graph_enabled: Boolean(kb.graph_enabled),
    asr_enabled: Boolean(kb.asr_enabled),
    asr_model_id: kb.asr_model_id || '',
    chunking: {
      strategy: kb.chunk_strategy || 'auto',
      chunkSize: kb.chunk_size,
      chunkOverlap: kb.chunk_overlap,
      enableParentChild: kb.enable_parent_child ?? false,
      parentChunkSize: kb.parent_chunk_size ?? CHUNK_DEFAULTS.parentChunkSize,
      childChunkSize: kb.child_chunk_size ?? CHUNK_DEFAULTS.childChunkSize,
    },
  }
  dialogVisible.value = true
}

async function saveKb() {
  const name = form.value.name.trim()
  if (!name) {
    MessagePlugin.warning('请输入知识库名称')
    return
  }
  const chunkErr = validateChunkingForm(form.value.chunking)
  if (chunkErr) {
    MessagePlugin.warning(chunkErr)
    return
  }
  if (form.value.asr_enabled && !form.value.asr_model_id) {
    MessagePlugin.warning('开启语音识别需选择 ASR 模型')
    return
  }
  saving.value = true
  try {
    const c = form.value.chunking
    if (editingKb.value) {
      await updateKnowledgeBase(editingKb.value.id, {
        name,
        description: form.value.description.trim(),
        chunk_size: c.chunkSize,
        chunk_overlap: c.chunkOverlap,
        chunk_strategy: c.strategy as KnowledgeBase['chunk_strategy'],
        summary_model_id: form.value.summary_model_id || null,
        enable_parent_child: c.enableParentChild,
        parent_chunk_size: c.parentChunkSize,
        child_chunk_size: c.childChunkSize,
        graph_enabled: form.value.graph_enabled,
        asr_enabled: form.value.asr_enabled,
        asr_model_id: form.value.asr_model_id || '',
      })
      MessagePlugin.success('知识库已更新')
    } else {
      if (!form.value.embedding_model_id) {
        MessagePlugin.warning('请选择 Embedding 模型')
        saving.value = false
        return
      }
      await createKnowledgeBase({
        name,
        description: form.value.description.trim(),
        chunk_size: c.chunkSize,
        chunk_overlap: c.chunkOverlap,
        embedding_model_id: form.value.embedding_model_id,
        summary_model_id: form.value.summary_model_id || null,
        chunk_strategy: c.strategy as KnowledgeBase['chunk_strategy'],
        enable_parent_child: c.enableParentChild,
        parent_chunk_size: c.parentChunkSize,
        child_chunk_size: c.childChunkSize,
        graph_enabled: form.value.graph_enabled,
        asr_enabled: form.value.asr_enabled,
        asr_model_id: form.value.asr_model_id || '',
      })
      MessagePlugin.success('知识库已创建')
    }
    dialogVisible.value = false
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

/* ---------- 删除 ---------- */
function confirmDelete(kb: KnowledgeBase) {
  const dialog = DialogPlugin.confirm({
    header: '删除知识库',
    body: `确定删除「${kb.name}」？库内 ${kb.document_count ?? 0} 个文档、${kb.chunk_count ?? 0} 个分块将一并删除，不可恢复。`,
    confirmBtn: { content: '删除', theme: 'danger' },
    cancelBtn: '取消',
    onConfirm: async () => {
      try {
        const res = await deleteKnowledgeBase(kb.id)
        MessagePlugin.success(`已删除 ${res.deleted_documents} 个文档、${res.deleted_chunks} 个分块`)
        await load()
      } catch (e) {
        MessagePlugin.error((e as Error).message)
      } finally {
        dialog.destroy()
      }
    },
  })
}

/* ---------- 列表 ---------- */
async function load() {
  loading.value = true
  try {
    kbs.value = await listKnowledgeBases()
  } catch (e) {
    MessagePlugin.error(`加载知识库失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

function openKb(kb: KnowledgeBase) {
  router.push(`/knowledge-bases/${kb.id}`)
}

function fmtTime(v: string | null): string {
  if (!v) return '-'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN', { hour12: false })
}

function isEvalKb(kb: KnowledgeBase): boolean {
  return (
    kb.name.startsWith('eval_') ||
    kb.name.startsWith('ragas_') ||
    (kb.description || '').includes('评测临时库')
  )
}

onMounted(() => {
  void loadModels()
  void load()
})
</script>

<template>
  <div class="kb-list-container">
    <div class="kb-list-content">
      <div class="header">
        <div class="header-title">
          <div class="title-row">
            <h2>知识库</h2>
            <t-tooltip content="新建知识库" placement="bottom">
              <t-button
                variant="text"
                theme="default"
                size="small"
                class="header-action-btn"
                @click="openCreate"
              >
                <template #icon><t-icon name="folder-add" size="16px" /></template>
              </t-button>
            </t-tooltip>
          </div>
          <p class="header-subtitle">管理多知识库，对话时可选择检索范围</p>
        </div>
      </div>

      <div class="kb-list-main">
        <div v-if="loading && kbs.length === 0" class="kb-card-wrap">
          <div v-for="n in 6" :key="'skel-' + n" class="kb-card kb-card-skeleton">
            <div class="card-header">
              <t-skeleton animation="gradient" :row-col="[{ width: '60%', height: '20px' }]" />
            </div>
            <div class="card-content">
              <t-skeleton
                animation="gradient"
                :row-col="[{ width: '100%', height: '14px' }, { width: '80%', height: '14px' }]"
              />
            </div>
            <div class="card-bottom">
              <t-skeleton
                animation="gradient"
                :row-col="[[{ width: '28px', height: '28px', type: 'rect' }, { width: '28px', height: '28px', type: 'rect' }]]"
              />
            </div>
          </div>
        </div>

        <div v-else-if="kbs.length === 0" class="kb-empty">
          <t-icon name="folder-open" size="48px" />
          <p class="empty-txt">还没有知识库</p>
          <p class="empty-desc">点击右上角按钮创建第一个知识库</p>
        </div>

        <div v-else class="kb-card-wrap">
          <div
            v-for="kb in kbs"
            :key="kb.id"
            class="kb-card kb-type-document"
            @click="openKb(kb)"
          >
            <div class="card-header">
              <span class="card-title" :title="kb.name">
                <span class="card-title-text">{{ kb.name }}</span>
                <t-tag v-if="isEvalKb(kb)" size="small" variant="light" theme="warning">评测临时</t-tag>
              </span>
              <t-popup overlay-class-name="card-more-popup" trigger="click" destroy-on-close placement="bottom-right">
                <div class="more-wrap" @click.stop>
                  <img class="more-icon" src="@/assets/img/more.png" alt="" />
                </div>
                <template #content>
                  <div class="popup-menu" @click.stop>
                    <div class="popup-menu-item" @click.stop="openEdit(kb)">
                      <t-icon class="menu-icon" name="edit-1" />
                      <span>编辑</span>
                    </div>
                    <div class="popup-menu-item delete" @click.stop="confirmDelete(kb)">
                      <t-icon class="menu-icon" name="delete" />
                      <span>删除</span>
                    </div>
                  </div>
                </template>
              </t-popup>
            </div>

            <div class="card-content">
              <div class="card-description">{{ kb.description || '暂无描述' }}</div>
            </div>

            <div class="card-bottom">
              <div class="bottom-left">
                <div class="feature-badges">
                  <t-tooltip content="文档数量" placement="top">
                    <div class="feature-badge type-document">
                      <t-icon name="folder" size="14px" />
                      <span class="badge-count">{{ kb.document_count ?? 0 }}</span>
                    </div>
                  </t-tooltip>
                  <t-tooltip :content="`分块 ${kb.chunk_count ?? 0}`" placement="top">
                    <div class="feature-badge chunks">
                      <t-icon name="layers" size="14px" />
                      <span class="badge-count">{{ kb.chunk_count ?? 0 }}</span>
                    </div>
                  </t-tooltip>
                  <t-tooltip v-if="kb.graph_enabled" content="知识图谱" placement="top">
                    <div class="feature-badge kg">
                      <t-icon name="relation" size="14px" />
                    </div>
                  </t-tooltip>
                  <t-tooltip v-if="kb.asr_enabled" content="语音识别入库" placement="top">
                    <div class="feature-badge asr">
                      <t-icon name="sound" size="14px" />
                    </div>
                  </t-tooltip>
                  <t-tooltip
                    :content="kb.enable_parent_child
                      ? `父子分块 parent ${kb.parent_chunk_size} / child ${kb.child_chunk_size} · ${strategyLabel(kb.chunk_strategy || 'auto')}`
                      : `切块 ${kb.chunk_size}/${kb.chunk_overlap} · ${strategyLabel(kb.chunk_strategy || 'auto')} · ${embeddingLabel(kb.embedding_model_id)}`"
                    placement="top"
                  >
                    <div class="feature-badge strategy">
                      <span class="badge-count">{{ strategyLabel(kb.chunk_strategy || 'auto') }}</span>
                    </div>
                  </t-tooltip>
                </div>
              </div>
              <div class="bottom-right">
                <span class="card-time">{{ fmtTime(kb.updated_at) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <t-dialog
      v-model:visible="dialogVisible"
      :header="editingKb ? '编辑知识库' : '新建知识库'"
      :confirm-btn="{ content: '保存', loading: saving, theme: 'primary' }"
      cancel-btn="取消"
      width="520px"
      @confirm="saveKb"
    >
      <t-form label-align="top">
        <t-form-item label="名称" required-mark>
          <t-input v-model="form.name" placeholder="例如：产品手册" :maxlength="100" />
        </t-form-item>
        <t-form-item label="描述">
          <t-textarea
            v-model="form.description"
            placeholder="可选，知识库用途说明"
            :maxlength="500"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </t-form-item>
        <t-form-item label="Embedding 模型" help="创建后不可修改，决定向量的语义空间与维度">
          <ModelSelector
            v-model:selected-model-id="form.embedding_model_id"
            model-type="Embedding"
            :all-models="allModels"
            :disabled="!!editingKb"
            placeholder="选择向量化模型"
          />
        </t-form-item>
        <t-form-item label="摘要 / 对话模型" help="该知识库对话时默认使用的问答模型（summary_model_id）">
          <ModelSelector
            v-model:selected-model-id="form.summary_model_id"
            model-type="KnowledgeQA"
            :all-models="allModels"
            placeholder="选择问答模型"
          />
        </t-form-item>
        <t-form-item
          label="知识图谱"
          help="开启后，文档入库会抽取实体关系写入 Neo4j（需 .env 中 NEO4J_ENABLE=true 并启动 neo4j 服务）。已有文档需重新解析才会建图。"
        >
          <t-switch v-model="form.graph_enabled" />
        </t-form-item>
        <t-form-item
          label="语音识别"
          help="开启后可上传 mp3/wav/m4a 等音频，入库前用 ASR 转写成文字再切块。"
        >
          <t-switch v-model="form.asr_enabled" />
        </t-form-item>
        <t-form-item v-if="form.asr_enabled" label="ASR 模型" help="用于音频转写的语音识别模型">
          <ModelSelector
            v-model:selected-model-id="form.asr_model_id"
            model-type="ASR"
            :all-models="allModels"
            placeholder="选择 ASR 模型"
          />
        </t-form-item>
        <t-form-item
          label="切块策略"
          help="auto 会扫描文档结构自动选择（Markdown 标题 → heading；编号章节/分页符 → heuristic；否则递归字符）"
        >
          <t-select v-model="form.chunking.strategy" :options="strategyOptions" />
        </t-form-item>
        <ParentChildChunkingFields v-model="form.chunking" />
      </t-form>
    </t-dialog>
  </div>
</template>

<style scoped lang="less">
.kb-list-container {
  margin: 0;
  height: 100%;
  box-sizing: border-box;
  flex: 1;
  display: flex;
  position: relative;
  min-height: 0;
}

.kb-list-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 20px 0 0 28px;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-right: 28px;

  .header-title {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .title-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  h2 {
    margin: 0;
    color: var(--td-text-color-primary);
    font-family: var(--app-font-family);
    font-size: 24px;
    font-weight: 600;
    line-height: 32px;
  }
}

.header-subtitle {
  margin: 0;
  color: var(--td-text-color-placeholder);
  font-family: var(--app-font-family);
  font-size: 14px;
  font-weight: 400;
  line-height: 20px;
}

.header-action-btn {
  padding: 0 !important;
  min-width: 28px !important;
  width: 28px !important;
  height: 28px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  background: var(--td-bg-color-secondarycontainer) !important;
  border: 1px solid var(--td-component-stroke) !important;
  border-radius: 6px !important;
  color: var(--td-text-color-secondary);
  cursor: pointer;
  box-shadow: inset 0 1px 0 color-mix(in srgb, var(--td-bg-color-container) 72%, transparent);
  transition: background 0.2s, border-color 0.2s, color 0.2s;

  &:hover {
    background: var(--td-bg-color-secondarycontainer) !important;
    border-color: var(--td-component-stroke) !important;
    color: var(--td-text-color-primary);
  }

  :deep(.t-icon) {
    color: var(--td-brand-color);
  }
}

.kb-list-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 28px 8px 0;
}

@keyframes contentFadeIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.kb-card-wrap {
  display: grid;
  gap: 12px;
  grid-template-columns: 1fr;
  animation: contentFadeIn 0.32s ease-out;
}

.kb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 80px 0;
  color: var(--td-text-color-placeholder);

  .empty-txt {
    margin: 8px 0 0;
    color: var(--td-text-color-placeholder);
    font-size: 16px;
    font-weight: 600;
    line-height: 26px;
  }

  .empty-desc {
    margin: 0;
    color: var(--td-text-color-disabled);
    font-size: 14px;
    line-height: 22px;
  }
}

.kb-card {
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  overflow: hidden;
  box-sizing: border-box;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  background: var(--td-bg-color-container);
  position: relative;
  cursor: pointer;
  transition: all 0.25s ease;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  height: 136px;
  min-height: 136px;

  &.kb-card-skeleton {
    cursor: default;

    .card-header {
      margin-bottom: 12px;
    }

    .card-content {
      flex: 1;
    }

    .card-bottom {
      margin-top: auto;
    }
  }

  &:hover {
    border-color: var(--td-brand-color);
    box-shadow: 0 4px 12px rgba(7, 192, 95, 0.12);
  }

  &.kb-type-document {
    background: linear-gradient(135deg, var(--td-bg-color-container) 0%, rgba(7, 192, 95, 0.04) 100%);

    &:hover {
      border-color: var(--td-brand-color);
      background: linear-gradient(135deg, var(--td-bg-color-container) 0%, rgba(7, 192, 95, 0.08) 100%);
    }

    &::after {
      content: '';
      position: absolute;
      top: 0;
      right: 0;
      width: 60px;
      height: 60px;
      background: linear-gradient(135deg, rgba(7, 192, 95, 0.08) 0%, transparent 100%);
      border-radius: 0 12px 0 100%;
      pointer-events: none;
      z-index: 0;
    }
  }

  .card-header,
  .card-content,
  .card-bottom {
    position: relative;
    z-index: 1;
  }
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;

  .card-title {
    flex: 1;
    font-size: 15px;
    font-weight: 600;
    color: var(--td-text-color-primary);
    letter-spacing: 0.01em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .card-title-text {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.more-wrap {
  display: flex;
  width: 24px;
  height: 24px;
  justify-content: center;
  align-items: center;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.2s ease;
  opacity: 0;

  .kb-card:hover & {
    opacity: 0.6;
  }

  &:hover {
    background: var(--td-bg-color-container-hover);
    opacity: 1 !important;
  }

  .more-icon {
    width: 14px;
    height: 14px;
  }
}

.card-content {
  flex: 1;
  min-height: 0;
  margin-bottom: 8px;
  overflow: hidden;
}

.card-description {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  overflow: hidden;
  color: var(--td-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
  line-height: 18px;
}

.card-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 8px;
  border-top: 0.5px solid var(--td-component-stroke);
}

.bottom-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
}

.bottom-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;

  .card-time {
    font-size: 12px;
    color: var(--td-text-color-placeholder);
  }
}

.feature-badges {
  display: flex;
  align-items: center;
  gap: 4px;
}

.feature-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  cursor: default;

  &.type-document,
  &.chunks,
  &.strategy {
    width: auto;
    padding: 0 6px;
    gap: 3px;
    background: rgba(7, 192, 95, 0.08);
    color: var(--td-brand-color-active);
  }

  &.chunks {
    background: rgba(0, 0, 0, 0.04);
    color: var(--td-text-color-secondary);
  }

  &.strategy {
    background: var(--td-bg-color-secondarycontainer);
    color: var(--td-text-color-secondary);
  }

  &.kg {
    background: rgba(124, 77, 255, 0.08);
    color: #7c4dff;
  }

  &.asr {
    background: rgba(0, 168, 112, 0.1);
    color: #00a870;
  }

  .badge-count {
    font-size: 11px;
    font-weight: 500;
  }
}

@media (min-width: 900px) {
  .kb-card-wrap {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (min-width: 1250px) {
  .kb-card-wrap {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (min-width: 1600px) {
  .kb-card-wrap {
    grid-template-columns: repeat(4, 1fr);
  }
}

@media (min-width: 1900px) {
  .kb-card-wrap {
    grid-template-columns: repeat(5, 1fr);
  }
}
</style>
