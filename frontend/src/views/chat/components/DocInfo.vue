<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import type { Citation } from '@/api/sessions'
import { useChatReferencesDrawer } from '@/composables/useChatReferencesDrawer'
import { citationsToReferences } from '@/utils/referenceSources'

const props = defineProps<{
  citations: Citation[]
  messageId?: string
}>()

const referencesDrawer = useChatReferencesDrawer()
const showReferBox = ref(false)
const expandedGroups = reactive<Record<string, boolean>>({})

const references = computed(() => citationsToReferences(props.citations))

const groupedRefs = computed(() => {
  const groupMap = new Map<
    string,
    { key: string; title: string; chunks: Array<{ content?: string; chunkIndex?: number }> }
  >()
  for (const c of props.citations) {
    const key = c.document_id || c.file_name || String(c.index)
    if (!groupMap.has(key)) {
      groupMap.set(key, { key, title: c.file_name || `来源 ${c.index}`, chunks: [] })
    }
    groupMap.get(key)!.chunks.push({ content: c.snippet, chunkIndex: c.chunk_index })
  }
  return Array.from(groupMap.values())
})

const headerText = computed(() => {
  const docCount = groupedRefs.value.length
  if (docCount === 0) return '引用来源'
  return `引用 ${docCount} 篇文档`
})

function referBoxSwitch() {
  if (referencesDrawer && references.value.length) {
    referencesDrawer.toggle({
      references: references.value,
      messageId: props.messageId || '',
    })
    return
  }
  showReferBox.value = !showReferBox.value
}

function toggleGroup(key: string) {
  expandedGroups[key] = !expandedGroups[key]
}

function truncateContent(text: string | undefined, maxLen: number) {
  if (!text) return ''
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLen) return normalized
  return `${normalized.slice(0, maxLen)}…`
}
</script>

<template>
  <div v-if="citations.length" class="refer">
    <div class="refer_header" @click="referBoxSwitch">
      <div class="refer_title">
        <t-icon name="file" class="refer-title-icon" />
        <span>{{ headerText }}</span>
        <div class="refer_show_icon">
          <t-icon :name="showReferBox ? 'chevron-down' : 'chevron-right'" />
        </div>
      </div>
    </div>
    <div v-show="showReferBox" class="refer_box">
      <div v-for="group in groupedRefs" :key="group.key" class="doc-group">
        <div class="doc-group-header" @click="toggleGroup(group.key)">
          <div class="doc-group-left">
            <t-icon name="file" size="14px" class="doc-group-icon" />
            <span class="doc-group-title" :title="group.title">{{ group.title }}</span>
            <span class="doc-group-count">{{ group.chunks.length }} 段</span>
          </div>
          <t-icon
            :name="expandedGroups[group.key] ? 'chevron-down' : 'chevron-right'"
            size="14px"
            class="doc-group-chevron"
          />
        </div>
        <div v-show="expandedGroups[group.key]" class="doc-group-chunks">
          <div v-for="(chunk, cIdx) in group.chunks" :key="cIdx" class="doc-chunk-item">
            <span class="doc-chunk-text">
              <span class="doc-chunk-index">片段 {{ cIdx + 1 }}</span>
              {{ truncateContent(chunk.content, 80) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.refer {
  display: flex;
  flex-direction: column;
  font-size: 12px;
  width: 100%;
  border-radius: 8px;
  background-color: var(--td-bg-color-container);
  border: 0.5px solid var(--td-component-stroke);
  box-shadow: 0 2px 4px color-mix(in srgb, var(--td-brand-color) 8%, transparent);
  overflow: hidden;
  margin-bottom: 8px;
}

.refer_header {
  display: flex;
  align-items: center;
  padding: 6px 14px;
  color: var(--td-text-color-primary);
  font-weight: 500;
  cursor: pointer;
}

.refer_header:hover {
  background-color: color-mix(in srgb, var(--td-brand-color) 4%, transparent);
}

.refer_title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.refer_title span {
  white-space: nowrap;
  font-size: 12px;
}

.refer-title-icon {
  color: var(--td-brand-color);
  font-size: 14px;
}

.refer_show_icon {
  font-size: 14px;
  padding: 0 2px 1px;
  color: var(--td-brand-color);
}

.refer_box {
  padding: 4px 14px 8px;
  border-top: 1px solid var(--td-bg-color-secondarycontainer);
}

.doc-group + .doc-group {
  margin-top: 6px;
}

.doc-group-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 0;
  cursor: pointer;
}

.doc-group-left {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.doc-group-icon {
  flex-shrink: 0;
  color: var(--td-text-color-placeholder);
}

.doc-group-title {
  font-size: 13px;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}

.doc-group-count {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.doc-group-chevron {
  color: var(--td-text-color-placeholder);
}

.doc-chunk-item {
  padding: 2px 0 2px 20px;
}

.doc-chunk-text {
  display: block;
  font-size: 12px;
  line-height: 1.5;
  color: var(--td-text-color-secondary);
  cursor: default;
}

.doc-chunk-index {
  color: var(--td-text-color-placeholder);
  margin-right: 4px;
}
</style>
