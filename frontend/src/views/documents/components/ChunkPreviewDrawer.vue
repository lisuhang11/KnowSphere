<script setup lang="ts">
/**
 * 切块预览抽屉：
 * 粘贴样文或从本地 md/txt 文件取前 N 字 → 后端用与入库完全相同的
 * 切块器预切一遍 → 逐块展示切块结果。只读，不写库、不生成 embedding。
 */
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { ChevronDownIcon, PlayCircleIcon } from 'tdesign-icons-vue-next'
import { previewChunking, type ChunkingPreviewResult } from '@/api/documents'
import ParentChildChunkingFields from '@/components/ParentChildChunkingFields.vue'
import { STRATEGY_OPTIONS, CHUNK_DEFAULTS } from '@/constants/chunking'
import type { ChunkingFormState } from '@/utils/chunkingConfig'

// 与后端 PREVIEW_MAX_CHARS 保持一致
const MAX_CHARS = 64 * 1024
// 从本地文件取样的字符数
const FILE_SAMPLE_CHARS = 4000

const visible = defineModel<boolean>({ default: false })

const sample = ref('')
const loading = ref(false)
const error = ref('')
const result = ref<ChunkingPreviewResult | null>(null)

const chunkForm = ref<ChunkingFormState>({
  strategy: 'auto',
  chunkSize: CHUNK_DEFAULTS.chunkSize,
  chunkOverlap: CHUNK_DEFAULTS.chunkOverlap,
  enableParentChild: CHUNK_DEFAULTS.enableParentChild,
  parentChunkSize: CHUNK_DEFAULTS.parentChunkSize,
  childChunkSize: CHUNK_DEFAULTS.childChunkSize,
})
const profileOpen = ref(false)
// 默认展开：用户目的是"看切块后的样子"，直接可见内容更直观
const collapsedChunks = ref(new Set<number>())

/* 切块策略选项 */
const strategyOptions = STRATEGY_OPTIONS

const TIER_LABELS: Record<string, string> = {
  auto: 'auto（自适应）',
  heading: 'heading · 按标题（Tier1）',
  heuristic: 'heuristic · 启发式边界（Tier2）',
  recursive: 'recursive（递归字符）',
  legacy: 'recursive · 递归字符（Tier3 兜底）',
}

function tierLabelLocal(t: string) {
  return TIER_LABELS[t] ?? t
}

/* 文档画像 → 展示网格 */
const profileItems = computed(() => {
  const p = result.value?.profile
  if (!p) return []
  const headings = Object.entries(p.md_heading_counts)
    .map(([level, count]) => `H${level}×${count}`)
    .join(' ')
  return [
    { label: '字符数', value: p.char_count },
    { label: '行数', value: p.line_count },
    { label: '平均行长', value: p.avg_line_len },
    { label: '标题层级', value: headings || '无' },
    { label: '主导标题层级', value: p.dominant_heading_level || '-' },
    { label: '编号章节', value: p.numbered_section_count },
    { label: '分页符 \\f', value: p.form_feed_count },
    { label: '章节标记', value: p.chapter_marker_count },
    { label: '伪标题(全大写)', value: p.all_caps_short_line_count },
    { label: '视觉分隔线', value: p.visual_sep_count },
    { label: '重复页脚', value: p.repeated_footer_count },
    { label: '表格 / 代码', value: `${p.has_tables ? '有' : '无'} / ${p.has_code ? '有' : '无'}` },
    { label: '语言', value: p.detected_langs.join(', ') || '-' },
  ]
})

/* 预设样例（精选两个） */
const SAMPLE_MD = `# KnowSphere 使用指南

KnowSphere 是一个 BYOD（Bring Your Own Document）知识问答助手：上传自己的文档，获得有据可查的智能问答。

## 文档摄取

上传 PDF / Markdown / TXT 后，系统会自动完成三步处理：切块（600 字、15% 重叠、中文标点感知）、向量化（bge-m3）、写入 pgvector 向量库。

## 混合检索

用户提问时，系统同时走两条召回路径：向量余弦相似度负责语义匹配，pg_trgm 词法相似度负责关键词命中，两路结果用 RRF 融合排序，再经 bge-reranker 精排。

## 上传限制

- 单文件建议小于 20MB
- 扫描版 PDF（纯图片）暂不支持文字提取
- 编码默认 UTF-8，其他编码的 txt 可能乱码`

const SAMPLE_PLAIN = `知识库的检索质量受多个因素影响，最直接的是切分策略与嵌入模型的匹配度。切分过粗会导致单段语义混杂、相关度被稀释；切分过细则丢失上下文，单独检索某一段无法回答跨段问题。一般建议切分大小落在嵌入模型推荐窗口的 50%–80%，既保证语义完整又留出余量。

除了切分大小，重叠（overlap）也会显著影响召回完整性。重叠为 0 时，跨段问题往往只能召回半句话；重叠 10%–20% 通常就足够覆盖大多数边界情况；重叠超过 30% 会让相邻分块大量同质化，反而增加索引成本而不提升召回。

分隔符的选择应贴合文档真实结构。纯文本可保留默认双换行作为强分隔；Markdown 文档建议保留标题层级，先按标题切，再在大标题内部按段落切。如果文档里大量混合中英文，分隔符里务必同时包含中文标点和英文标点，否则只对一种语言生效。`

const samples = [
  { id: 'markdown', label: 'Markdown 样例', text: SAMPLE_MD },
  { id: 'plain', label: '纯文本样例', text: SAMPLE_PLAIN },
]

/* 首次打开且无输入时自动载入默认样例 */
watch(visible, (open) => {
  if (open && sample.value.trim() === '') {
    loadSample('markdown')
  }
})

function loadSample(id: string) {
  const preset = samples.find((s) => s.id === id)
  if (!preset) return
  sample.value = preset.text
  result.value = null
  error.value = ''
  collapsedChunks.value = new Set()
  profileOpen.value = false
}

/* 从本地 md/txt 文件取前 N 字填充样文 */
const fileInput = ref<HTMLInputElement | null>(null)

function pickFile() {
  fileInput.value?.click()
}

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许重复选择同一文件
  if (!file) return
  if (!/\.(md|txt)$/i.test(file.name)) {
    MessagePlugin.warning('仅支持从 .md / .txt 文件取样（PDF 为二进制格式）')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    const text = String(reader.result ?? '')
    sample.value = text.slice(0, FILE_SAMPLE_CHARS)
    result.value = null
    error.value = ''
    collapsedChunks.value = new Set()
    MessagePlugin.success(`已从「${file.name}」取前 ${sample.value.length} 字`)
  }
  reader.onerror = () => MessagePlugin.error('文件读取失败')
  reader.readAsText(file, 'utf-8')
}

async function runPreview() {
  loading.value = true
  error.value = ''
  result.value = null
  collapsedChunks.value = new Set()
  profileOpen.value = false
  try {
    result.value = await previewChunking(sample.value, {
      strategy: chunkForm.value.strategy,
      chunkSize: chunkForm.value.chunkSize,
      chunkOverlap: chunkForm.value.chunkOverlap,
      enableParentChild: chunkForm.value.enableParentChild,
      parentChunkSize: chunkForm.value.parentChunkSize,
      childChunkSize: chunkForm.value.childChunkSize,
    })
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg
    MessagePlugin.error(`预览失败: ${msg}`)
  } finally {
    loading.value = false
  }
}

function toggleChunk(seq: number) {
  const next = new Set(collapsedChunks.value)
  if (next.has(seq)) next.delete(seq)
  else next.add(seq)
  collapsedChunks.value = next
}

</script>

<template>
  <t-drawer
    v-model:visible="visible"
    header="测试切块"
    size="720px"
    placement="right"
    :footer="false"
    :close-on-overlay-click="true"
    :z-index="3000"
  >
    <div class="drawer-body">
      <!-- 输入区 -->
      <section class="drawer-section">
        <div class="section-title-row">
          <div class="section-title">样文</div>
          <div class="sample-presets">
            <t-button
              v-for="p in samples"
              :key="p.id"
              variant="text"
              size="small"
              @click="loadSample(p.id)"
            >
              {{ p.label }}
            </t-button>
            <t-button variant="text" size="small" @click="pickFile">从文件取样</t-button>
            <input
              ref="fileInput"
              type="file"
              accept=".md,.txt"
              class="hidden-file-input"
              @change="onFilePicked"
            />
          </div>
        </div>
        <t-textarea
          v-model="sample"
          placeholder="粘贴一段文本，预览它将被切成什么样（与实际入库使用同一套切块参数）"
          :autosize="{ minRows: 6, maxRows: 12 }"
          :maxlength="MAX_CHARS"
        />
        <div class="preview-config-row">
          <div class="strategy-select">
            <t-select
              v-model="chunkForm.strategy"
              :options="strategyOptions"
              size="small"
              style="width: 220px"
            />
            <span class="strategy-desc">
              {{ strategyOptions.find((s) => s.value === chunkForm.strategy)?.desc }}
            </span>
          </div>
          <t-button
            theme="primary"
            :loading="loading"
            :disabled="!sample || sample.length === 0"
            @click="runPreview"
          >
            <template #icon><play-circle-icon /></template>
            运行预览
          </t-button>
        </div>
        <ParentChildChunkingFields v-model="chunkForm" compact />
      </section>

      <!-- 加载中 -->
      <div v-if="loading" class="state-block">
        <t-loading size="small" />
        <span>正在切块…</span>
      </div>

      <!-- 错误 -->
      <div v-else-if="error" class="state-block error">
        <span>预览失败：{{ error }}</span>
      </div>

      <!-- 结果 -->
      <section v-else-if="result" class="drawer-section">
        <div class="result-header">
          <span v-if="result.enable_parent_child">
            父子分块：父 {{ result.parent_chunk_size }} / 子 {{ result.child_chunk_size }} · 重叠 {{ result.chunk_overlap }}
          </span>
          <span v-else>
            切块参数：{{ result.chunk_size }} 字 / 重叠 {{ result.chunk_overlap }}（与入库一致）
          </span>
          <span class="chunk-stats">
            <template v-if="result.enable_parent_child">
              父块 {{ result.parent_count }} · 子块 <strong>{{ result.chunk_count }}</strong>
            </template>
            <template v-else>
              共 <strong>{{ result.stats.chunk_count }}</strong> 块 · 平均
              {{ result.stats.avg_chars }} 字 · 最短 {{ result.stats.min_chars }} · 最长
              {{ result.stats.max_chars }}
            </template>
          </span>
        </div>

        <!-- 策略诊断：实际选中的 tier + 降级原因 -->
        <div class="tier-bar">
          <span class="tier-tag" :class="'tier-' + result.selected_tier">
            实际策略：{{ tierLabelLocal(result.selected_tier) }}
          </span>
          <span v-if="result.rejected.length" class="rejected-tip">
            降级：{{ result.rejected.map((r) => `${tierLabelLocal(r.tier)}不可用（${r.reason}）`).join('；') }}
          </span>
        </div>
        <div v-if="result.enable_parent_child" class="pc-preview-tip">
          预览展示子块（检索粒度），与入库后 hybrid_search 命中粒度一致
        </div>

        <!-- 文档画像（可折叠诊断） -->
        <div class="profile-panel">
          <button type="button" class="profile-toggle" @click="profileOpen = !profileOpen">
            <span>文档画像</span>
            <chevron-down-icon class="chunk-toggle" :class="{ open: profileOpen }" />
          </button>
          <div v-if="profileOpen" class="profile-grid">
            <div v-for="item in profileItems" :key="item.label" class="profile-item">
              <span class="profile-label">{{ item.label }}</span>
              <strong class="profile-value">{{ item.value }}</strong>
            </div>
          </div>
        </div>

        <div v-if="!result.chunks.length" class="empty-hint">切块结果为空，请检查文本内容</div>

        <ol class="chunks-list">
          <li
            v-for="c in result.chunks"
            :key="c.seq"
            class="chunk-card"
            :class="{ collapsed: collapsedChunks.has(c.seq) }"
          >
            <button type="button" class="chunk-meta" @click="toggleChunk(c.seq)">
              <span class="chunk-seq">#{{ c.seq + 1 }}</span>
              <span v-if="c.parent_index != null && c.parent_index >= 0" class="parent-ref">P{{ c.parent_index + 1 }}</span>
              <span class="chunk-size">
                {{ c.char_count }} 字 · ~{{ c.token_count }} tok
              </span>
              <chevron-down-icon class="chunk-toggle" :class="{ open: !collapsedChunks.has(c.seq) }" />
            </button>
            <div class="chunk-body" :class="{ collapsed: collapsedChunks.has(c.seq) }">
              <div v-if="c.context_header" class="chunk-header-pill">
                <span class="pill-label">面包屑</span>
                <span class="pill-text">{{ c.context_header }}</span>
              </div>
              <pre class="chunk-text">{{ c.content }}</pre>
            </div>
          </li>
        </ol>
      </section>
    </div>
  </t-drawer>
</template>

<style scoped>
.drawer-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.drawer-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.sample-presets {
  display: flex;
  align-items: center;
  gap: 4px;
}

.hidden-file-input {
  display: none;
}

.preview-config-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}

.strategy-select {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.strategy-desc {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 策略诊断条 */
.tier-bar {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
  font-size: 12px;
}

.tier-tag {
  padding: 2px 10px;
  border-radius: 999px;
  font-weight: 600;
  color: #fff;
}

.tier-heading {
  background: var(--td-brand-color);
}

.tier-heuristic {
  background: var(--td-warning-color);
}

.tier-legacy {
  background: var(--td-success-color);
}

.rejected-tip {
  color: var(--td-warning-color);
  line-height: 1.6;
}

.pc-preview-tip {
  font-size: 12px;
  color: var(--td-warning-color);
}

.parent-ref {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--td-warning-color-light);
  color: var(--td-warning-color);
  font-size: 11px;
}

/* 文档画像 */
.profile-panel {
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  overflow: hidden;
}

.profile-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 12px;
  background: var(--td-bg-color-secondarycontainer);
  border: none;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.profile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px;
  padding: 10px 12px;
  background: var(--td-bg-color-container);
}

.profile-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.profile-label {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.profile-value {
  font-size: 13px;
  color: var(--td-text-color-primary);
  word-break: break-all;
}

/* 面包屑 pill */
.chunk-header-pill {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
  padding: 4px 8px;
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

.state-block {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--td-text-color-secondary);
  font-size: 13px;
}

.state-block.error {
  color: var(--td-error-color);
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.chunk-stats strong {
  color: var(--td-text-color-primary);
}

.empty-hint {
  padding: 24px 0;
  text-align: center;
  color: var(--td-text-color-placeholder);
  font-size: 13px;
}

.chunks-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chunk-card {
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  overflow: hidden;
}

.chunk-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 8px 12px;
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
  padding: 10px 12px;
  background: var(--td-bg-color-container);
}

.chunk-body.collapsed {
  display: none;
}

.chunk-text {
  margin: 0;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--td-text-color-primary);
}
</style>
