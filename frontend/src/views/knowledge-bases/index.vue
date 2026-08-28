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

onMounted(() => {
  void loadModels()
  void load()
})
</script>

<template>
  <div class="kb-page">
    <div class="kb-header">
      <div>
        <h2>知识库</h2>
        <p class="kb-sub">管理多知识库，对话时可选择检索范围</p>
      </div>
      <t-button theme="primary" @click="openCreate">
        <template #icon><t-icon name="add" /></template>
        新建知识库
      </t-button>
    </div>

    <div v-if="loading" class="kb-loading">
      <t-loading text="加载中..." />
    </div>

    <div v-else-if="kbs.length === 0" class="kb-empty">
      <t-icon name="folder-open" size="48px" />
      <p>还没有知识库，点击右上角「新建知识库」开始</p>
    </div>

    <div v-else class="kb-grid">
      <div
        v-for="kb in kbs"
        :key="kb.id"
        class="kb-card"
        @click="openKb(kb)"
      >
        <div class="kb-card-head">
          <div class="kb-icon"><t-icon name="folder" size="22px" /></div>
          <div class="kb-card-title" :title="kb.name">{{ kb.name }}</div>
          <div class="kb-card-actions" @click.stop>
            <t-button variant="text" shape="square" size="small" title="编辑" @click="openEdit(kb)">
              <template #icon><t-icon name="edit" size="16px" /></template>
            </t-button>
            <t-button variant="text" shape="square" size="small" title="删除" @click="confirmDelete(kb)">
              <template #icon><t-icon name="delete" size="16px" /></template>
            </t-button>
          </div>
        </div>
        <div class="kb-card-desc" :title="kb.description">{{ kb.description || '暂无描述' }}</div>
        <div class="kb-card-stats">
          <span><t-icon name="file" size="14px" /> {{ kb.document_count ?? 0 }} 文档</span>
          <span><t-icon name="layers" size="14px" /> {{ kb.chunk_count ?? 0 }} 分块</span>
          <span class="kb-card-time">{{ fmtTime(kb.updated_at) }}</span>
        </div>
        <div
          class="kb-card-config"
          :title="kb.enable_parent_child
            ? `父子分块 parent ${kb.parent_chunk_size} / child ${kb.child_chunk_size} · ${strategyLabel(kb.chunk_strategy || 'auto')}`
            : `切块 ${kb.chunk_size}/${kb.chunk_overlap} · ${strategyLabel(kb.chunk_strategy || 'auto')} · ${embeddingLabel(kb.embedding_model_id)} (${kb.embedding_dim}维)`"
        >
          <t-icon name="setting" size="13px" />
          <span v-if="kb.enable_parent_child">父子 {{ kb.parent_chunk_size }}/{{ kb.child_chunk_size }}</span>
          <span v-else>切块 {{ kb.chunk_size }}/{{ kb.chunk_overlap }}</span>
          <span class="kb-strategy-tag">{{ strategyLabel(kb.chunk_strategy || 'auto') }}</span>
          <span v-if="kb.enable_parent_child" class="kb-strategy-tag pc-tag">父子</span>
          <span class="kb-card-ellipsis">{{ embeddingLabel(kb.embedding_model_id) }}</span>
        </div>
      </div>
    </div>

    <!-- 创建/编辑弹窗 -->
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

<style scoped>
.kb-page {
  height: 100%;
  overflow-y: auto;
  padding: 24px;
  box-sizing: border-box;
  background: var(--td-bg-color-page);
}

.kb-header {
  max-width: 1080px;
  margin: 0 auto 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.kb-header h2 {
  margin: 0;
  font-size: 20px;
  color: var(--td-text-color-primary);
}

.kb-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.kb-loading,
.kb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--td-text-color-placeholder);
}

.kb-grid {
  max-width: 1080px;
  margin: 0 auto;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.kb-card {
  background: #fff;
  border: 1px solid var(--td-border-level-1-color);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kb-card:hover {
  border-color: var(--td-brand-color);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
}

.kb-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.kb-icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
  display: flex;
  align-items: center;
  justify-content: center;
}

.kb-card-title {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-card-actions {
  flex-shrink: 0;
  display: flex;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.kb-card:hover .kb-card-actions {
  opacity: 1;
}

.kb-card-desc {
  font-size: 13px;
  color: var(--td-text-color-secondary);
  line-height: 1.6;
  height: 42px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-card-stats {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.kb-card-stats span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.kb-card-time {
  margin-left: auto;
  color: var(--td-text-color-placeholder);
}

.kb-card-config {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
  padding-top: 8px;
  border-top: 1px dashed var(--td-border-level-1-color);
}

.kb-card-ellipsis {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-strategy-tag {
  flex-shrink: 0;
  padding: 0 8px;
  border-radius: 999px;
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
  font-weight: 600;
}

.pc-tag {
  background: var(--td-warning-color-light);
  color: var(--td-warning-color);
}
</style>
