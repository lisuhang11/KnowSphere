<script setup lang="ts">
/**
 * 上传确认弹窗（含父子分块）：
 * 选文件 → 先配置再入库。一次配置作用于整批；只把与知识库默认不同的字段
 * 打包成 process_config 随上传持久化（omitempty 语义）。
 */
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { ChevronDownIcon, PlayCircleIcon } from 'tdesign-icons-vue-next'
import {
  previewChunking,
  type ChunkingPreviewResult,
  type ChunkingProcessConfig,
} from '@/api/documents'
import type { KnowledgeBase } from '@/api/knowledgeBases'
import ParentChildChunkingFields from '@/components/ParentChildChunkingFields.vue'
import { STRATEGY_OPTIONS, tierLabel } from '@/constants/chunking'
import { KB_ASR_REQUIRED_HINT, isChatAudioFile } from '@/utils/audio'
import {
  buildProcessConfig,
  kbToChunkingForm,
  validateChunkingForm,
  type ChunkingFormState,
} from '@/utils/chunkingConfig'

export interface UploadConfirmPayload {
  kbId: number
  files: File[]
  processConfig: ChunkingProcessConfig | null
}

const visible = defineModel<boolean>({ default: false })
const props = defineProps<{ kb: KnowledgeBase | null; files: File[] }>()
const emit = defineEmits<{
  (e: 'confirm', payload: UploadConfirmPayload): void
  (e: 'close'): void
}>()

const localFiles = computed(() => props.files)
const MAX_PREVIEW_CHARS = 64 * 1024

const form = ref<ChunkingFormState>(kbToChunkingForm(null))
const previewing = ref(false)
const previewError = ref('')
const previewResult = ref<ChunkingPreviewResult | null>(null)
const previewFile = ref<File | null>(null)
const collapsedChunks = ref(new Set<number>())

watch(visible, (open) => {
  if (open && props.kb) resetForm()
})

function resetForm() {
  form.value = kbToChunkingForm(props.kb)
  previewResult.value = null
  previewError.value = ''
  previewFile.value = null
  collapsedChunks.value = new Set()
}

function buildProcessConfigLocal(): ChunkingProcessConfig | null {
  if (!props.kb) return null
  return buildProcessConfig(form.value, props.kb)
}

function fileIcon(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase()
  if (ext === 'pdf') return 'file-pdf'
  if (ext === 'md') return 'file-markdown'
  if (['mp3', 'wav', 'm4a', 'flac', 'ogg', 'aac'].includes(ext || '')) return 'sound'
  return 'file'
}

const hasAudioFiles = computed(() => localFiles.value.some((f) => isChatAudioFile(f)))
const asrReady = computed(() => Boolean(props.kb?.asr_enabled && props.kb?.asr_model_id))

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const previewable = computed(
  () => localFiles.value.find((f) => /\.(md|txt)$/i.test(f.name)) ?? null,
)

async function runPreview() {
  const target = previewable.value
  if (!target) {
    previewError.value = '仅 md/txt 支持弹窗内预览；PDF 上传后可在文档详情页查看切块'
    return
  }
  previewing.value = true
  previewError.value = ''
  previewResult.value = null
  previewFile.value = target
  collapsedChunks.value = new Set()
  try {
    const text = (await target.text()).slice(0, MAX_PREVIEW_CHARS - 1)
    if (!text.trim()) throw new Error('文件内容为空')
    previewResult.value = await previewChunking(text, {
      strategy: form.value.strategy,
      chunkSize: form.value.chunkSize,
      chunkOverlap: form.value.chunkOverlap,
      kbId: props.kb?.id,
      enableParentChild: form.value.enableParentChild,
      parentChunkSize: form.value.parentChunkSize,
      childChunkSize: form.value.childChunkSize,
    })
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    previewError.value = msg
    MessagePlugin.error(`预览失败: ${msg}`)
  } finally {
    previewing.value = false
  }
}

function toggleChunk(seq: number) {
  const next = new Set(collapsedChunks.value)
  if (next.has(seq)) next.delete(seq)
  else next.add(seq)
  collapsedChunks.value = next
}

function previewStatsLine(result: ChunkingPreviewResult): string {
  if (result.enable_parent_child) {
    return `父块 ${result.parent_count} · 子块 ${result.chunk_count} · 平均 ${result.stats.avg_chars ?? '-'} 字`
  }
  return `共 ${result.stats.chunk_count} 块 · 平均 ${result.stats.avg_chars} 字`
}

function confirmUpload() {
  if (!props.kb || !localFiles.value.length) return
  if (hasAudioFiles.value && !asrReady.value) {
    MessagePlugin.warning(KB_ASR_REQUIRED_HINT)
    return
  }
  const err = validateChunkingForm(form.value)
  if (err) {
    MessagePlugin.warning(err)
    return
  }
  emit('confirm', {
    kbId: props.kb.id,
    files: [...localFiles.value],
    processConfig: buildProcessConfigLocal(),
  })
  visible.value = false
}
</script>

<template>
  <t-dialog
    v-model:visible="visible"
    :header="`确认上传（${localFiles.length} 个文件）`"
    width="760px"
    :confirm-btn="{
      content: '开始上传',
      theme: 'primary',
      disabled: !localFiles.length,
    }"
    cancel-btn="取消"
    :close-on-overlay-click="false"
    @confirm="confirmUpload"
    @close="emit('close')"
  >
    <div class="upload-dialog-body">
      <section class="file-panel">
        <div class="panel-title">待上传文件</div>
        <p class="panel-hint">一次配置作用于整批，如个别文件需单独配置请分次上传</p>
        <p v-if="hasAudioFiles && !asrReady" class="panel-hint" style="color: var(--td-error-color)">
          {{ KB_ASR_REQUIRED_HINT }}
        </p>
        <ul class="file-list">
          <li v-for="f in localFiles" :key="`${f.name}-${f.size}`" class="file-row">
            <t-icon :name="fileIcon(f.name)" size="18px" class="file-icon" />
            <div class="file-row-meta">
              <span class="file-row-name" :title="f.name">{{ f.name }}</span>
              <span class="file-row-size">{{ fmtSize(f.size) }}</span>
            </div>
          </li>
        </ul>
      </section>

      <section class="config-panel">
        <div class="panel-title">切块配置</div>
        <t-form label-align="top" class="config-form">
          <t-form-item label="切块策略" help="预填知识库默认，可针对本次上传单独修改">
            <t-select v-model="form.strategy" :options="STRATEGY_OPTIONS" />
          </t-form-item>
          <ParentChildChunkingFields v-model="form" compact />
        </t-form>

        <div class="preview-bar">
          <span class="preview-note">
            {{ previewable ? `预览源：${previewable.name}` : '仅 md/txt 可弹窗内预览' }}
          </span>
          <t-button
            variant="outline"
            size="small"
            :loading="previewing"
            :disabled="!previewable"
            @click="runPreview"
          >
            <template #icon><play-circle-icon /></template>
            预览切块
          </t-button>
        </div>

        <div v-if="previewing" class="preview-state">
          <t-loading size="small" />
          <span>正在切块…</span>
        </div>
        <div v-else-if="previewError" class="preview-state error">{{ previewError }}</div>

        <div v-else-if="previewResult" class="preview-result">
          <div class="result-header">
            <span>{{ previewStatsLine(previewResult) }}</span>
            <span class="tier-tag" :class="'tier-' + previewResult.selected_tier">
              实际策略：{{ tierLabel(previewResult.selected_tier) }}
            </span>
          </div>
          <div v-if="previewResult.enable_parent_child" class="pc-preview-tip">
            预览展示子块（检索粒度）；共 {{ previewResult.parent_count }} 个父块
          </div>
          <div v-if="previewResult.rejected.length" class="rejected-tip">
            降级：{{ previewResult.rejected.map((r) => `${tierLabel(r.tier)} 不可用（${r.reason}）`).join('；') }}
          </div>
          <ol class="chunks-list">
            <li
              v-for="c in previewResult.chunks.slice(0, 10)"
              :key="c.seq"
              class="chunk-card"
              :class="{ collapsed: collapsedChunks.has(c.seq) && c.seq !== 0 }"
            >
              <button type="button" class="chunk-meta" @click="toggleChunk(c.seq)">
                <span class="chunk-seq">#{{ c.seq + 1 }}</span>
                <span v-if="c.parent_index != null && c.parent_index >= 0" class="parent-ref">P{{ c.parent_index + 1 }}</span>
                <span class="chunk-size">{{ c.char_count }} 字 · ~{{ c.token_count }} tok</span>
                <chevron-down-icon class="chunk-toggle" :class="{ open: !collapsedChunks.has(c.seq) || c.seq === 0 }" />
              </button>
              <div v-if="!collapsedChunks.has(c.seq) || c.seq === 0" class="chunk-body">
                <div v-if="c.context_header" class="chunk-header-pill">
                  <span class="pill-label">面包屑</span>
                  <span class="pill-text">{{ c.context_header }}</span>
                </div>
                <pre class="chunk-text">{{ c.content }}</pre>
              </div>
            </li>
            <li v-if="previewResult.chunks.length > 10" class="more-hint">
              仅展示前 10 块，其余 {{ previewResult.chunks.length - 10 }} 块入库后可在详情页查看
            </li>
          </ol>
        </div>
      </section>
    </div>
  </t-dialog>
</template>

<style scoped>
.upload-dialog-body {
  display: grid;
  grid-template-columns: 240px 1fr;
  gap: 16px;
  min-height: 320px;
}

.file-panel {
  border-right: 1px solid var(--td-component-border);
  padding-right: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.panel-hint {
  margin: 0;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.6;
}

.file-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  overflow-y: auto;
  max-height: 420px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  background: var(--td-bg-color-container);
}

.file-icon {
  color: var(--td-brand-color);
  flex-shrink: 0;
}

.file-row-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.file-row-name {
  font-size: 13px;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-row-size {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.config-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.config-form {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.preview-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border: 1px dashed var(--td-component-border);
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
}

.preview-note {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  color: var(--td-text-color-secondary);
  font-size: 13px;
}

.preview-state.error {
  color: var(--td-error-color);
}

.preview-result {
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  padding: 10px;
  max-height: 360px;
  overflow-y: auto;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.pc-preview-tip {
  font-size: 12px;
  color: var(--td-warning-color);
}

.tier-tag {
  padding: 2px 10px;
  border-radius: 999px;
  font-weight: 600;
  color: #fff;
}

.tier-heading { background: var(--td-brand-color); }
.tier-heuristic { background: var(--td-warning-color); }
.tier-legacy { background: var(--td-success-color); }

.rejected-tip {
  font-size: 12px;
  color: var(--td-warning-color);
  line-height: 1.6;
}

.chunks-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chunk-card {
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  overflow: hidden;
}

.chunk-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  background: var(--td-bg-color-secondarycontainer);
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.chunk-seq {
  font-family: Consolas, Monaco, monospace;
  font-weight: 600;
  color: var(--td-brand-color);
}

.parent-ref {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--td-warning-color-light);
  color: var(--td-warning-color);
  font-size: 11px;
}

.chunk-size {
  flex: 1;
  text-align: left;
}

.chunk-toggle {
  transition: transform 0.2s;
}

.chunk-toggle.open {
  transform: rotate(180deg);
}

.chunk-body {
  padding: 8px 10px;
  background: var(--td-bg-color-container);
}

.chunk-header-pill {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--td-brand-color-light);
  font-size: 12px;
}

.pill-label {
  color: var(--td-brand-color);
  font-weight: 600;
}

.pill-text {
  color: var(--td-text-color-primary);
  white-space: pre-wrap;
}

.chunk-text {
  margin: 0;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--td-text-color-primary);
  max-height: 160px;
  overflow-y: auto;
}

.more-hint {
  text-align: center;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  padding: 8px 0;
}
</style>
