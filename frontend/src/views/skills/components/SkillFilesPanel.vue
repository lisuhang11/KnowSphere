<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { copyToClipboard } from '@/utils/clipboard'
import { renderMarkdown } from '@/utils/markdown'
import { getSkillFile, listSkillFiles, type SkillFileContent } from '@/api/skills'
import {
  buildSkillFileTree,
  collectSkillDirs,
  flattenVisibleSkillRows,
  highlightSkillFile,
  isMarkdownPath,
  skillFileIcon,
  splitMarkdownFrontmatter,
  type SkillFileNode,
  type SkillFileRow,
} from '@/utils/skillFiles'
import { MessagePlugin } from 'tdesign-vue-next'

const props = defineProps<{
  skillName: string
}>()

const listLoading = ref(false)
const fileLoading = ref(false)
const listError = ref('')
const fileError = ref('')
const nodes = ref<SkillFileNode[]>([])
const expandedDirs = ref<Set<string>>(new Set())
const selectedPath = ref('')
const file = ref<SkillFileContent | null>(null)
const markdownHtml = ref('')
const highlightedHtml = ref('')
const markdownMode = ref<'preview' | 'source'>('preview')
const frontmatterFields = computed(() => {
  const text = file.value?.encoding === 'utf-8' ? file.value.content || '' : ''
  if (!text || !isMarkdownPath(selectedPath.value)) return []
  return splitMarkdownFrontmatter(text).fields
})

const visibleRows = computed(() => {
  const out: SkillFileRow[] = []
  flattenVisibleSkillRows(nodes.value, 0, expandedDirs.value, out)
  return out
})

const canToggleMarkdown = computed(
  () => Boolean(selectedPath.value && isMarkdownPath(selectedPath.value) && file.value?.encoding === 'utf-8'),
)
const canCopy = computed(() => Boolean(file.value?.content && file.value.encoding === 'utf-8'))
const showMarkdown = computed(() => canToggleMarkdown.value && markdownMode.value === 'preview')
const imageSrc = computed(() => {
  const current = file.value
  if (!current || current.encoding !== 'base64' || !current.content || !current.media_type) return ''
  return `data:${current.media_type};base64,${current.content}`
})

function toggleDir(path: string) {
  const next = new Set(expandedDirs.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expandedDirs.value = next
}

function onRowClick(row: { path: string; isDir: boolean }) {
  if (row.isDir) {
    toggleDir(row.path)
    return
  }
  void selectFile(row.path)
}

async function selectFile(path: string) {
  if (!props.skillName) return
  selectedPath.value = path
  fileLoading.value = true
  fileError.value = ''
  file.value = null
  markdownHtml.value = ''
  highlightedHtml.value = ''
  markdownMode.value = 'preview'
  try {
    const data = await getSkillFile(props.skillName, path)
    file.value = data
    if (data.encoding === 'utf-8' && data.content != null) {
      highlightedHtml.value = highlightSkillFile(data.content, path)
      if (isMarkdownPath(path)) {
        const { body } = splitMarkdownFrontmatter(data.content)
        markdownHtml.value = body.trim() ? renderMarkdown(body) : ''
      }
    }
  } catch {
    fileError.value = '无法读取该文件。'
  } finally {
    fileLoading.value = false
  }
}

async function copyContent() {
  const text = file.value?.content
  if (!text) return
  const ok = await copyToClipboard(text)
  if (ok) MessagePlugin.success('已复制')
  else MessagePlugin.error('复制失败')
}

async function loadFiles() {
  if (!props.skillName) return
  listLoading.value = true
  listError.value = ''
  nodes.value = []
  selectedPath.value = ''
  file.value = null
  fileError.value = ''
  try {
    const entries = await listSkillFiles(props.skillName)
    const paths = entries.map((item) => item.path)
    const tree = buildSkillFileTree(paths)
    nodes.value = tree
    const dirs = new Set<string>()
    collectSkillDirs(tree, dirs)
    expandedDirs.value = dirs
    const initial = paths.includes('SKILL.md') ? 'SKILL.md' : paths[0]
    if (initial) await selectFile(initial)
  } catch {
    listError.value = '无法加载技能文件。'
  } finally {
    listLoading.value = false
  }
}

watch(
  () => props.skillName,
  () => {
    void loadFiles()
  },
  { immediate: true },
)
</script>

<template>
  <div class="skill-files-panel">
    <aside class="skill-files-panel__nav">
      <t-loading :loading="listLoading" size="small">
        <p v-if="listError" class="skill-files-panel__hint">{{ listError }}</p>
        <p v-else-if="!visibleRows.length && !listLoading" class="skill-files-panel__hint">这个技能还没有可预览的文件。</p>
        <ul v-else class="skill-files-panel__list">
          <li v-for="row in visibleRows" :key="row.path">
            <button
              type="button"
              class="skill-files-panel__item"
              :class="{ 'is-active': !row.isDir && row.path === selectedPath }"
              @click="onRowClick(row)"
            >
              <span class="skill-files-panel__indent" :style="{ width: `${row.depth * 14}px` }" />
              <t-icon class="skill-files-panel__icon" :name="skillFileIcon(row.name, row.isDir)" size="16px" />
              <span class="skill-files-panel__name">{{ row.name }}</span>
            </button>
          </li>
        </ul>
      </t-loading>
    </aside>

    <section class="skill-files-panel__main">
      <div class="skill-files-panel__bar">
        <span class="skill-files-panel__path">{{ selectedPath || '选择一个文件' }}</span>
        <button
          v-if="canToggleMarkdown"
          type="button"
          class="skill-files-panel__link"
          @click="markdownMode = markdownMode === 'preview' ? 'source' : 'preview'"
        >
          {{ markdownMode === 'preview' ? '查看源码' : '预览' }}
        </button>
        <button v-if="canCopy" type="button" class="skill-files-panel__link" @click="copyContent">复制</button>
      </div>
      <t-loading :loading="fileLoading" size="small" class="skill-files-panel__view-loading">
        <div class="skill-files-panel__view">
          <p v-if="fileError" class="skill-files-panel__hint">{{ fileError }}</p>
          <p v-else-if="!selectedPath && !fileLoading" class="skill-files-panel__hint">从左侧选择文件以预览 SKILL.md 或脚本。</p>
          <template v-else-if="file">
            <p v-if="file.truncated" class="skill-files-panel__warn">内容已截断，仅显示前半部分。</p>
            <dl v-if="showMarkdown && frontmatterFields.length" class="skill-files-panel__meta">
              <div v-for="field in frontmatterFields" :key="field.key" class="skill-files-panel__meta-row">
                <dt>{{ field.key }}</dt>
                <dd :class="{ 'is-code': field.code }">{{ field.value }}</dd>
              </div>
            </dl>
            <p v-if="file.encoding === 'binary'" class="skill-files-panel__hint">该文件不是 UTF-8 文本，无法在页面中预览。</p>
            <img v-else-if="imageSrc" class="skill-files-panel__image" :src="imageSrc" :alt="selectedPath" />
            <div
              v-else-if="showMarkdown"
              class="skill-files-panel__markdown markdown-content"
              v-html="markdownHtml"
            />
            <pre v-else-if="file.encoding === 'utf-8'" class="skill-files-panel__code"><code class="hljs" v-html="highlightedHtml" /></pre>
          </template>
        </div>
      </t-loading>
    </section>
  </div>
</template>

<style scoped lang="less">
@import '@/components/css/chat-markdown.less';

.skill-files-panel {
  display: flex;
  min-height: 0;
  height: 100%;
  flex: 1;
  overflow: hidden;
  background: var(--td-bg-color-container);
}

.skill-files-panel__nav {
  width: 220px;
  flex-shrink: 0;
  min-width: 0;
  min-height: 0;
  height: 100%;
  border-right: 1px solid var(--td-component-stroke);
  overflow: auto;
  padding: 8px 8px 12px;
  background: var(--td-bg-color-secondarycontainer);
}

.skill-files-panel__list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.skill-files-panel__item {
  display: flex;
  align-items: center;
  width: 100%;
  margin-bottom: 2px;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-size: 13px;
  color: var(--td-text-color-primary);
  cursor: pointer;
  text-align: left;
}

.skill-files-panel__item:hover {
  background-color: var(--td-bg-color-container-hover);
}

.skill-files-panel__item.is-active {
  background-color: var(--td-bg-color-container);
  color: var(--td-brand-color);
  font-weight: 500;
}

.skill-files-panel__indent {
  flex-shrink: 0;
  height: 1px;
}

.skill-files-panel__icon {
  flex-shrink: 0;
  margin-right: 8px;
  color: inherit;
}

.skill-files-panel__name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-files-panel__main {
  min-width: 0;
  min-height: 0;
  height: 100%;
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.skill-files-panel__bar {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  min-height: 40px;
  padding: 0 12px 0 16px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.skill-files-panel__path {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.skill-files-panel__link {
  flex-shrink: 0;
  height: 24px;
  padding: 0 8px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font-size: 12px;
  line-height: 24px;
  color: var(--td-text-color-secondary);
  cursor: pointer;
}

.skill-files-panel__link:hover {
  background: var(--td-bg-color-container-hover);
  color: var(--td-text-color-primary);
}

.skill-files-panel__view-loading {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.skill-files-panel__view {
  min-height: 0;
  flex: 1;
  overflow: auto;
  padding: 16px 20px 32px;
}

.skill-files-panel__hint {
  margin: 24px 8px;
  text-align: center;
  font-size: 12px;
  line-height: 1.5;
  color: var(--td-text-color-placeholder);
}

.skill-files-panel__warn {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--td-warning-color);
}

.skill-files-panel__meta {
  margin: 0 0 20px;
  padding: 8px 12px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-secondarycontainer);
}

.skill-files-panel__meta-row {
  display: grid;
  grid-template-columns: 108px minmax(0, 1fr);
  gap: 4px 12px;
  padding: 6px 0;
}

.skill-files-panel__meta-row + .skill-files-panel__meta-row {
  border-top: 1px solid var(--td-component-stroke);
}

.skill-files-panel__meta-row dt {
  margin: 0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
  color: var(--td-text-color-secondary);
}

.skill-files-panel__meta-row dd {
  margin: 0;
  min-width: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--td-text-color-primary);
  word-break: break-word;
  white-space: pre-wrap;
}

.skill-files-panel__meta-row dd.is-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
}

.skill-files-panel__image {
  max-width: 100%;
  height: auto;
}

.skill-files-panel__code {
  margin: 0;
  padding: 0;
  background: transparent;
  overflow: visible;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  color: var(--td-text-color-primary);
  white-space: pre-wrap;
  word-break: break-word;
}

.skill-files-panel__markdown {
  min-width: 0;
  .chat-markdown-typography();
}
</style>
