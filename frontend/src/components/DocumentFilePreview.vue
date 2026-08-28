<script setup lang="ts">
/**
 * 文档原文预览（对齐 WeKnora document-preview.vue）
 * PDF/图片：iframe / img；docx / xlsx / pptx：客户端渲染
 */
import { computed, defineAsyncComponent, nextTick, onUnmounted, ref, shallowRef, watch } from 'vue'
import DOMPurify from 'dompurify'
import { renderMarkdown } from '@/utils/markdown'
import {
  resolveAttachmentFileType,
  resolveAttachmentPreviewKind,
  type AttachmentPreviewKind,
} from '@/utils/attachmentPreview'

const VueOfficePptx = defineAsyncComponent(() => import('@vue-office/pptx'))

const props = withDefaults(
  defineProps<{
    fileUrl: string
    fileName: string
    active?: boolean
    fillHeight?: boolean
  }>(),
  {
    active: true,
    fillHeight: false,
  },
)

const loading = ref(false)
const error = ref('')
const blobUrl = ref('')
const textContent = ref('')
const excelHtml = ref('')
const pptxData = shallowRef<ArrayBuffer | null>(null)
const docxContainer = ref<HTMLElement | null>(null)

let loadedKey = ''

const kind = computed<AttachmentPreviewKind>(() => resolveAttachmentPreviewKind(props.fileName))
const fileExt = computed(() => resolveAttachmentFileType(props.fileName))
const markdownHtml = computed(() =>
  kind.value === 'markdown' ? renderMarkdown(textContent.value) : '',
)

function cleanup() {
  if (blobUrl.value) {
    URL.revokeObjectURL(blobUrl.value)
    blobUrl.value = ''
  }
  textContent.value = ''
  excelHtml.value = ''
  pptxData.value = null
  if (docxContainer.value) docxContainer.value.innerHTML = ''
  loadedKey = ''
}

async function renderDocx(blob: Blob) {
  const { renderAsync } = await import('docx-preview')
  await nextTick()
  if (!docxContainer.value) return
  docxContainer.value.innerHTML = ''
  await renderAsync(blob, docxContainer.value, undefined, {
    className: 'docx-preview-wrapper',
    inWrapper: true,
    ignoreWidth: false,
    ignoreHeight: false,
    breakPages: true,
    ignoreLastRenderedPageBreak: true,
    trimXmlDeclaration: true,
    useBase64URL: true,
  })
}

async function renderExcel(blob: Blob, ext: string) {
  const XLSX = await import('xlsx')
  const lower = ext.toLowerCase()
  let workbook
  if (lower === 'csv' || lower === 'tsv' || lower === 'tab') {
    const text = await blob.text()
    const fs = lower === 'tsv' || lower === 'tab' ? '\t' : undefined
    workbook = XLSX.read(text, { type: 'string', FS: fs })
  } else {
    workbook = XLSX.read(await blob.arrayBuffer(), { type: 'array' })
  }

  let html = ''
  workbook.SheetNames.forEach((name, sheetIdx) => {
    const sheet = workbook.Sheets[name]
    const sheetHtml = XLSX.utils.sheet_to_html(sheet, { id: `sheet-${sheetIdx}` })
    html += '<div class="excel-sheet">'
    if (workbook.SheetNames.length > 1) {
      html += `<div class="excel-sheet-title">${DOMPurify.sanitize(name)}</div>`
    }
    html += sheetHtml
    html += '</div>'
  })
  excelHtml.value = DOMPurify.sanitize(html)
}

async function loadPreview() {
  const key = `${props.fileUrl}\0${props.fileName}`
  if (!props.active || !props.fileUrl) return
  if (loadedKey === key) return

  cleanup()
  loading.value = true
  error.value = ''

  const k = kind.value
  if (k === 'unsupported') {
    loading.value = false
    return
  }

  try {
    const resp = await fetch(props.fileUrl, { credentials: 'include' })
    if (!resp.ok) throw new Error(`加载失败 (${resp.status})`)
    const blob = await resp.blob()
    loadedKey = key
    loading.value = false
    await nextTick()

    switch (k) {
      case 'pdf':
      case 'html':
      case 'image':
        blobUrl.value = URL.createObjectURL(blob)
        break
      case 'docx':
        await renderDocx(blob)
        break
      case 'pptx':
        pptxData.value = await blob.arrayBuffer()
        break
      case 'excel':
        await renderExcel(blob, fileExt.value)
        break
      case 'text':
      case 'markdown':
        textContent.value = await blob.text()
        break
    }
  } catch (e) {
    error.value = (e as Error).message || '加载失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.active, props.fileUrl, props.fileName] as const,
  () => {
    void loadPreview()
  },
  { immediate: true },
)

onUnmounted(cleanup)
</script>

<template>
  <div
    class="doc-file-preview"
    :class="{ 'doc-file-preview--fill': fillHeight }"
  >
    <div v-if="loading" class="doc-file-preview__state">
      <t-loading size="small" text="加载预览…" />
    </div>
    <div v-else-if="error" class="doc-file-preview__state doc-file-preview__error">
      {{ error }}
    </div>
    <template v-else>
      <div v-if="kind === 'pdf'" class="doc-file-preview__pdf">
        <iframe :src="blobUrl" :title="fileName" class="doc-file-preview__iframe" />
      </div>
      <iframe
        v-else-if="kind === 'html'"
        :src="blobUrl"
        :title="fileName"
        class="doc-file-preview__iframe doc-file-preview__html-frame"
      />
      <img
        v-else-if="kind === 'image'"
        :src="blobUrl"
        :alt="fileName"
        class="doc-file-preview__image"
      />
      <div v-else-if="kind === 'docx'" ref="docxContainer" class="doc-file-preview__docx docx-container" />
      <div v-else-if="kind === 'pptx' && pptxData" class="doc-file-preview__pptx">
        <VueOfficePptx :src="pptxData" />
      </div>
      <div
        v-else-if="kind === 'excel' && excelHtml"
        class="doc-file-preview__excel excel-container"
        v-html="excelHtml"
      />
      <div
        v-else-if="kind === 'markdown'"
        class="doc-file-preview__markdown chat-markdown"
        v-html="markdownHtml"
      />
      <pre v-else-if="kind === 'text'" class="doc-file-preview__text">{{ textContent }}</pre>
      <div v-else class="doc-file-preview__state">
        <p>暂不支持在线预览此格式</p>
        <a :href="fileUrl" target="_blank" rel="noopener noreferrer">下载查看</a>
      </div>
    </template>
  </div>
</template>

<style scoped lang="less">
@preview-max-h: calc(100vh - 200px);
@border-radius: 6px;

.doc-file-preview {
  width: 100%;
  min-height: 500px;
  max-height: @preview-max-h;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.doc-file-preview--fill {
  flex: 1;
  min-height: 0;
  max-height: none;
  height: 100%;
  display: flex;
  flex-direction: column;

  .doc-file-preview__state,
  .doc-file-preview__pdf,
  .doc-file-preview__iframe,
  .doc-file-preview__docx,
  .doc-file-preview__pptx,
  .doc-file-preview__excel,
  .doc-file-preview__markdown,
  .doc-file-preview__text {
    flex: 1;
    min-height: 0;
    max-height: none;
  }

  .doc-file-preview__pdf {
    height: auto;
  }

  .doc-file-preview__iframe {
    min-height: 0;
  }

  .doc-file-preview__image {
    max-height: 100%;
  }
}

.doc-file-preview__state {
  flex: 1;
  min-height: 320px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--td-text-color-secondary);
  font-size: 14px;
}

.doc-file-preview__error {
  color: var(--td-error-color-6);
}

.doc-file-preview__pdf {
  flex: 1;
  min-height: 500px;
  height: @preview-max-h;
  max-height: @preview-max-h;
}

.doc-file-preview__iframe {
  width: 100%;
  height: 100%;
  min-height: 500px;
  border: 0;
  border-radius: @border-radius;
  background: var(--td-bg-color-container);
}

.doc-file-preview__html-frame {
  border: 1px solid var(--td-component-stroke);
}

.doc-file-preview__image {
  max-width: 100%;
  max-height: calc(100vh - 280px);
  margin: 0 auto;
  object-fit: contain;
  border-radius: @border-radius;
}

.doc-file-preview__docx {
  flex: 1;
  min-height: 500px;
  max-height: @preview-max-h;
  overflow: auto;
  border: 1px solid var(--td-component-stroke);
  border-radius: @border-radius;
  background: var(--td-bg-color-container);
  padding: 8px;
}

.doc-file-preview__pptx {
  flex: 1;
  min-height: 500px;
  max-height: @preview-max-h;
  overflow: auto;
  border: 1px solid var(--td-component-stroke);
  border-radius: @border-radius;
  background: var(--td-bg-color-secondarycontainer, #f5f5f5);
}

.doc-file-preview__pptx :deep(.pptx-preview-wrapper) {
  height: auto !important;
  overflow-y: visible !important;
}

.doc-file-preview__excel {
  flex: 1;
  min-height: 500px;
  max-height: @preview-max-h;
  overflow: auto;
  border: 1px solid var(--td-component-stroke);
  border-radius: @border-radius;
  background: var(--td-bg-color-container);
  padding: 12px;
}

.doc-file-preview__excel :deep(table) {
  border-collapse: collapse;
  font-size: 13px;
}

.doc-file-preview__excel :deep(td),
.doc-file-preview__excel :deep(th) {
  border: 1px solid var(--td-component-stroke);
  padding: 4px 8px;
}

.doc-file-preview__excel :deep(.excel-sheet-title) {
  font-weight: 600;
  margin: 12px 0 8px;
  color: var(--td-text-color-primary);
}

.doc-file-preview__markdown {
  flex: 1;
  min-height: 420px;
  max-height: @preview-max-h;
  overflow: auto;
  padding: 16px 20px;
  border: 1px solid var(--td-component-stroke);
  border-radius: @border-radius;
  background: var(--td-bg-color-container);
}

.doc-file-preview__text {
  flex: 1;
  min-height: 420px;
  max-height: @preview-max-h;
  margin: 0;
  padding: 16px;
  overflow: auto;
  border-radius: @border-radius;
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-secondarycontainer, #f5f5f5);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
