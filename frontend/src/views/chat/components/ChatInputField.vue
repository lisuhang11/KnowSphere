<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import type { KnowledgeBase } from '@/api/knowledgeBases'
import type { ModelInfo } from '@/api/models'
import type { AgentInfo } from '@/api/agents'
import ChatKnowledgeBaseSelector from '@/components/chat/ChatKnowledgeBaseSelector.vue'
import ChatInputModelDropdown from '@/components/chat/ChatInputModelDropdown.vue'
import ChatInputAgentDropdown from '@/components/chat/ChatInputAgentDropdown.vue'
import {
  AGENT_AUDIO_DISABLED_HINT,
  NO_ASR_AUDIO_UPLOAD_HINT,
  hasUsableAsr,
  isAgentAudioUploadEnabled,
} from '@/utils/audio'
import {
  CHAT_IMAGE_ACCEPT,
  CHAT_IMAGE_MAX_COUNT,
  NO_VLM_IMAGE_UPLOAD_HINT,
  chatAttachmentAccept,
  hasUsableVlm,
  type PendingChatAttachment,
} from '@/utils/chatImages'
import { formatFileSize, getFileExt } from '@/utils/fileFormat'
import { SKILL_ICON, mentionQueryAt, uniqueSkillNames } from '@/utils/skillMention'
import sendIcon from '@/assets/img/sending-aircraft.svg'

const props = defineProps<{
  streaming: boolean
  canSend: boolean
  kbList: KnowledgeBase[]
  allModels: ModelInfo[]
  agents: AgentInfo[]
  selectedKbIds: number[]
  selectedChatModelId: string
  selectedVlmModelId: string
  selectedAgentId: string
  webSearchEnabled: boolean
  webSearchAvailable: boolean
  graphAvailable: boolean
  pendingAttachments: PendingChatAttachment[]
}>()

const emit = defineEmits<{
  'update:selectedKbIds': [number[]]
  'update:selectedChatModelId': [string]
  'update:selectedVlmModelId': [string]
  'update:selectedAgentId': [string]
  'update:webSearchEnabled': [boolean]
  send: []
  stop: []
  attachmentSelect: [Event]
  removeAttachment: [PendingChatAttachment]
}>()

const input = defineModel<string>('input', { default: '' })
const selectedSkillNames = defineModel<string[]>('selectedSkillNames', { default: () => [] })

const textareaRef = ref<{ $el?: HTMLElement }>()
const imageInputRef = ref<HTMLInputElement>()
const fileInputRef = ref<HTMLInputElement>()
const kbButtonRef = ref<HTMLElement>()
const showKbSelector = ref(false)

const selectedKbIds = computed({
  get: () => props.selectedKbIds,
  set: (v) => emit('update:selectedKbIds', v),
})

const selectedChatModelId = computed({
  get: () => props.selectedChatModelId,
  set: (v) => emit('update:selectedChatModelId', v),
})

const selectedAgentId = computed({
  get: () => props.selectedAgentId,
  set: (v) => emit('update:selectedAgentId', v),
})

const webSearchEnabled = computed({
  get: () => props.webSearchEnabled && props.webSearchAvailable,
  set: (v) => emit('update:webSearchEnabled', v),
})

const selectedAgent = computed(() =>
  props.agents.find((a) => a.id === props.selectedAgentId && a.status !== 'disabled'),
)

const boundSkills = computed(() => selectedAgent.value?.skills || [])
const selectedSkillChips = computed(() => {
  const wanted = new Set(selectedSkillNames.value)
  return boundSkills.value.filter((s) => wanted.has(s.name))
})
const showSkillMention = computed(() => boundSkills.value.length > 0)

const mentionOpen = ref(false)
const mentionQuery = ref('')
const mentionStart = ref(-1)
const mentionIndex = ref(0)
const mentionByButton = ref(false)
const rootRef = ref<HTMLElement>()

const filteredSkills = computed(() => {
  const selected = new Set(selectedSkillNames.value)
  const q = mentionQuery.value.toLowerCase()
  return boundSkills.value.filter((s) => {
    if (selected.has(s.name)) return false
    if (!q) return true
    return s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  })
})

function textareaEl(): HTMLTextAreaElement | null {
  return (textareaRef.value?.$el?.querySelector('textarea') as HTMLTextAreaElement | null) || null
}

function closeMention() {
  mentionOpen.value = false
  mentionByButton.value = false
  mentionQuery.value = ''
  mentionStart.value = -1
}

function detectMention() {
  if (!showSkillMention.value) {
    closeMention()
    return
  }
  if (mentionByButton.value && mentionOpen.value) {
    const el = textareaEl()
    const cursor = el?.selectionStart ?? input.value.length
    if (cursor !== mentionStart.value) {
      mentionByButton.value = false
    } else {
      return
    }
  }
  const el = textareaEl()
  const cursor = el?.selectionStart ?? input.value.length
  const hit = mentionQueryAt(input.value, cursor)
  if (!hit) {
    closeMention()
    return
  }
  mentionByButton.value = false
  mentionStart.value = hit.start
  mentionQuery.value = hit.query
  mentionOpen.value = true
  const max = Math.max(filteredSkills.value.length - 1, 0)
  if (mentionIndex.value > max) mentionIndex.value = 0
}

function stripMentionQuery() {
  if (mentionByButton.value || mentionStart.value < 0) return
  const el = textareaEl()
  const cursor = el?.selectionStart ?? input.value.length
  const before = input.value.slice(0, mentionStart.value)
  const after = input.value.slice(cursor)
  input.value = `${before}${after}`
  void nextTick(() => {
    const node = textareaEl()
    if (!node) return
    const pos = before.length
    node.focus()
    node.setSelectionRange(pos, pos)
    autoResize()
  })
}

function pickSkill(name: string) {
  selectedSkillNames.value = uniqueSkillNames([...selectedSkillNames.value, name])
  stripMentionQuery()
  closeMention()
  void nextTick(() => textareaEl()?.focus())
}

function removeSkillChip(name: string) {
  selectedSkillNames.value = selectedSkillNames.value.filter((item) => item !== name)
}

function openMentionFromButton() {
  if (!showSkillMention.value || props.streaming) return
  mentionByButton.value = true
  mentionQuery.value = ''
  mentionIndex.value = 0
  const el = textareaEl()
  mentionStart.value = el?.selectionStart ?? input.value.length
  mentionOpen.value = true
  void nextTick(() => el?.focus())
}

function onDocumentPointerDown(event: MouseEvent) {
  if (!mentionOpen.value) return
  const root = rootRef.value
  if (root && event.target instanceof Node && root.contains(event.target)) return
  closeMention()
}

watch(
  () => props.selectedAgentId,
  () => {
    closeMention()
  },
)

watch(
  boundSkills,
  (list) => {
    const allowed = list.map((s) => s.name)
    selectedSkillNames.value = uniqueSkillNames(selectedSkillNames.value, allowed)
  },
  { immediate: true },
)

onMounted(() => {
  document.addEventListener('mousedown', onDocumentPointerDown)
})
onUnmounted(() => {
  document.removeEventListener('mousedown', onDocumentPointerDown)
})

const agentHasWeb = computed(
  () => selectedAgent.value?.tools?.some((t) => t.requires_web) ?? false,
)

const agentHasGraph = computed(
  () =>
    selectedAgent.value?.tools?.some(
      (t) => t.requires_graph || t.name === 'query_knowledge_graph',
    ) ?? false,
)

const selectedHasGraphKb = computed(() => selectedKbs.value.some((k) => Boolean(k.graph_enabled)))

const graphReady = computed(
  () => agentHasGraph.value && selectedHasGraphKb.value && props.graphAvailable,
)

const showWebToggle = computed(() => agentHasWeb.value)
const showGraphStatus = computed(() => agentHasGraph.value)

const inputPlaceholder = computed(() => {
  const hasKb = selectedKbCount.value > 0
  const web = showWebToggle.value && webSearchEnabled.value
  const skillHint = boundSkills.value.length ? '输入 @ 可点名技能。' : ''
  if (hasKb && web) return `${skillHint}可检索知识库，也可联网搜索。Enter 发送`
  if (hasKb) return `${skillHint}基于已选知识库提问。Enter 发送，Shift + Enter 换行`
  if (web) return `${skillHint}可联网搜索公开信息。Enter 发送，Shift + Enter 换行`
  return skillHint ? `${skillHint}Enter 发送，Shift + Enter 换行` : '请输入问题，Enter 发送，Shift + Enter 换行'
})

const webTooltip = computed(() => {
  if (!props.webSearchAvailable) return '管理员已关闭联网搜索'
  return webSearchEnabled.value ? '关闭联网搜索' : '开启联网搜索'
})

const graphTooltip = computed(() => {
  if (!props.graphAvailable) return '知识图谱未启用（需 NEO4J_ENABLE=true）'
  if (!selectedKbCount.value) return '选择已开启图谱的知识库后，关系型问题会查询实体关系'
  if (!selectedHasGraphKb.value) return '所选知识库未开启图谱，可在知识库设置中打开'
  return '知识图谱已就绪：关系型问题会查询实体关系'
})

function toggleWebSearch() {
  if (!props.webSearchAvailable || !showWebToggle.value) return
  webSearchEnabled.value = !webSearchEnabled.value
}

function onGraphClick() {
  if (graphReady.value) return
  MessagePlugin.info(graphTooltip.value)
}

const selectedKbs = computed(() => {
  // 按 selectedKbIds 顺序展示，避免列表顺序与勾选顺序不一致；并过滤已删库
  return props.selectedKbIds
    .map((id) => props.kbList.find((k) => k.id === id))
    .filter((k): k is KnowledgeBase => Boolean(k))
})

const imageAttachments = computed(() =>
  props.pendingAttachments.filter((a) => a.previewUrl || a.file.type.startsWith('image/')),
)

const fileAttachments = computed(() =>
  props.pendingAttachments.filter((a) => !a.previewUrl && !a.file.type.startsWith('image/')),
)

const hasSelectedTags = computed(
  () => selectedKbs.value.length > 0 || selectedSkillChips.value.length > 0,
)
const selectedKbCount = computed(() => selectedKbs.value.length)
const vlmReady = computed(() => hasUsableVlm(props.allModels))
const asrReady = computed(() => hasUsableAsr(props.allModels))
const audioUploadEnabled = computed(() => isAgentAudioUploadEnabled(selectedAgent.value))
const audioReady = computed(() => asrReady.value && audioUploadEnabled.value)
const imageUploadDisabled = computed(
  () => props.streaming || props.pendingAttachments.length >= CHAT_IMAGE_MAX_COUNT || !vlmReady.value,
)
const fileUploadDisabled = computed(
  () => props.streaming || props.pendingAttachments.length >= CHAT_IMAGE_MAX_COUNT,
)
const attachmentAccept = computed(() =>
  chatAttachmentAccept({ vlmReady: vlmReady.value, audioReady: audioReady.value }),
)
const imageUploadTooltip = computed(() => (vlmReady.value ? '上传图片' : NO_VLM_IMAGE_UPLOAD_HINT))
const fileUploadTooltip = computed(() => {
  if (!asrReady.value) return `${NO_ASR_AUDIO_UPLOAD_HINT}（文档仍可上传）`
  if (!audioUploadEnabled.value) return `${AGENT_AUDIO_DISABLED_HINT}（文档仍可上传）`
  return '上传文档或音频'
})

function attachmentStatusLabel(item: PendingChatAttachment): string {
  if (item.status === 'uploading') return '上传中…'
  if (item.status === 'uploaded' || item.status === 'processing') return '解析中…'
  if (item.status === 'ready') return '就绪'
  if (item.status === 'failed') return item.errorMessage || '失败'
  return ''
}

function openKbSelector() {
  showKbSelector.value = true
}

function removeKbTag(id: number) {
  selectedKbIds.value = props.selectedKbIds.filter((x) => x !== id)
}

function triggerImageUpload() {
  if (!vlmReady.value) {
    MessagePlugin.warning(NO_VLM_IMAGE_UPLOAD_HINT)
    return
  }
  if (imageUploadDisabled.value) return
  imageInputRef.value?.click()
}

function triggerFileUpload() {
  if (fileUploadDisabled.value) return
  fileInputRef.value?.click()
}

function onKeydown(_value: string, context: { e: KeyboardEvent }) {
  const e = context.e
  if (mentionOpen.value && filteredSkills.value.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      mentionIndex.value = (mentionIndex.value + 1) % filteredSkills.value.length
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      mentionIndex.value =
        (mentionIndex.value - 1 + filteredSkills.value.length) % filteredSkills.value.length
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const skill = filteredSkills.value[mentionIndex.value]
      if (skill) pickSkill(skill.name)
      return
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      closeMention()
      return
    }
  }
  if (e.key === 'Backspace' && !e.isComposing && !input.value && selectedSkillNames.value.length) {
    e.preventDefault()
    selectedSkillNames.value = selectedSkillNames.value.slice(0, -1)
    return
  }
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    emit('send')
  }
}

function onInput() {
  detectMention()
  autoResize()
}

function autoResize() {
  const el = textareaRef.value?.$el?.querySelector('textarea') as HTMLTextAreaElement | null
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}

defineExpose({
  autoResize,
  inputRef: textareaRef,
})
</script>

<template>
  <div class="answers-input">
    <input
      ref="imageInputRef"
      type="file"
      :accept="CHAT_IMAGE_ACCEPT"
      multiple
      hidden
      @change="emit('attachmentSelect', $event)"
    />
    <input
      ref="fileInputRef"
      type="file"
      :accept="attachmentAccept"
      multiple
      hidden
      @change="emit('attachmentSelect', $event)"
    />

    <div ref="rootRef" class="rich-input-container" :class="{ 'has-tags': hasSelectedTags }">
      <!-- 图片预览 -->
      <div v-if="imageAttachments.length" class="image-preview-bar">
        <div v-for="item in imageAttachments" :key="item.id" class="image-preview-item">
          <img :src="item.previewUrl" :alt="item.fileName" class="image-preview-thumb" />
          <span class="image-preview-remove" @click="emit('removeAttachment', item)">×</span>
        </div>
      </div>

      <!-- 文档附件预览 -->
      <div v-if="fileAttachments.length" class="attachment-preview-bar">
        <div v-for="item in fileAttachments" :key="item.id" class="attachment-preview-item">
          <div class="attachment-preview-icon">
            <svg viewBox="0 0 40 48" fill="none" xmlns="http://www.w3.org/2000/svg" width="32" height="38">
              <rect width="40" height="48" rx="4" fill="#4A90D9" />
              <path d="M8 6h16l8 8v28a2 2 0 01-2 2H8a2 2 0 01-2-2V8a2 2 0 012-2z" fill="#5BA3E8" />
              <path d="M24 6l8 8h-6a2 2 0 01-2-2V6z" fill="#3A7BC8" />
              <rect x="10" y="20" width="20" height="2" rx="1" fill="white" fill-opacity="0.9" />
              <rect x="10" y="26" width="20" height="2" rx="1" fill="white" fill-opacity="0.9" />
              <rect x="10" y="32" width="14" height="2" rx="1" fill="white" fill-opacity="0.9" />
            </svg>
          </div>
          <div class="attachment-preview-info">
            <div class="attachment-preview-name">{{ item.fileName }}</div>
            <div class="attachment-preview-meta">
              {{ getFileExt(item.fileName) }} · {{ formatFileSize(item.file.size) }}
            </div>
            <div
              v-if="item.status && item.status !== 'ready'"
              class="attachment-preview-status"
              :class="`is-${item.status}`"
            >
              {{ attachmentStatusLabel(item) }}
            </div>
          </div>
          <span class="attachment-preview-remove" @click="emit('removeAttachment', item)">×</span>
        </div>
      </div>

      <!-- 已选知识库 / 技能标签 -->
      <div v-if="hasSelectedTags" class="selected-tags-inline">
        <span
          v-for="skill in selectedSkillChips"
          :key="'sk-' + skill.name"
          class="mention-chip mention-chip--skill"
        >
          <span class="mention-chip__icon-wrap mention-chip__icon-wrap--skill">
            <span class="mention-chip__icon">
              <t-icon :name="SKILL_ICON" />
            </span>
          </span>
          <span class="mention-chip__name" :title="skill.description || skill.name">{{ skill.name }}</span>
          <span class="mention-chip__remove" @click.stop="removeSkillChip(skill.name)">×</span>
        </span>
        <span
          v-for="kb in selectedKbs"
          :key="kb.id"
          class="mention-chip mention-chip--kb"
        >
          <span class="mention-chip__icon-wrap">
            <span class="mention-chip__icon">
              <t-icon name="folder-open" />
            </span>
          </span>
          <span class="mention-chip__name" :title="kb.name">{{ kb.name }}</span>
          <t-tooltip v-if="kb.graph_enabled" content="已开启知识图谱" placement="top" theme="light">
            <t-icon name="relation" size="12px" class="mention-chip__graph" />
          </t-tooltip>
          <span class="mention-chip__remove" @click.stop="removeKbTag(kb.id)">×</span>
        </span>
      </div>

      <t-textarea
        ref="textareaRef"
        v-model="input"
        :placeholder="inputPlaceholder"
        :autosize="true"
        @keydown="onKeydown"
        @input="onInput"
      />

      <div
        v-if="mentionOpen && showSkillMention"
        class="skill-mention-menu"
        role="listbox"
      >
        <div class="skill-mention-menu__header">
          <t-icon :name="SKILL_ICON" size="16px" />
          <span>技能</span>
        </div>
        <p v-if="!filteredSkills.length" class="skill-mention-empty">
          {{ mentionQuery ? '没有匹配的技能' : '已添加当前智能体的全部技能' }}
        </p>
        <button
          v-for="(skill, i) in filteredSkills"
          :key="skill.name"
          type="button"
          class="skill-mention-item"
          :class="{ active: i === mentionIndex }"
          @mousedown.prevent="pickSkill(skill.name)"
        >
          <span class="skill-mention-item__icon">
            <t-icon :name="SKILL_ICON" size="16px" />
          </span>
          <span class="skill-mention-item__text">
            <span class="skill-mention-item__name">{{ skill.name }}</span>
            <span class="skill-mention-item__desc">{{ skill.description }}</span>
          </span>
        </button>
      </div>

      <div class="control-bar">
        <div class="control-left">
          <t-tooltip :content="imageUploadTooltip" placement="top" theme="light">
            <div
              class="control-btn image-upload-btn"
              :class="{
                active: imageAttachments.length > 0,
                disabled: imageUploadDisabled,
              }"
              @click.stop="triggerImageUpload"
            >
              <svg width="18" height="18" viewBox="0 0 1024 1024" fill="currentColor" class="control-icon">
                <path
                  d="M896 128H128c-35.3 0-64 28.7-64 64v640c0 35.3 28.7 64 64 64h768c35.3 0 64-28.7 64-64V192c0-35.3-28.7-64-64-64zM128 832V192h768l0.1 640H128z"
                />
                <path d="M352 448a96 96 0 1 0 0-192 96 96 0 0 0 0 192z" />
                <path d="M128 768l224-288 160 160 192-256L896 640v128H128z" />
              </svg>
              <span v-if="imageAttachments.length" class="image-count">{{ imageAttachments.length }}</span>
            </div>
          </t-tooltip>

          <t-tooltip :content="fileUploadTooltip" placement="top" theme="light">
            <div
              class="control-btn attachment-upload-btn"
              :class="{
                active: fileAttachments.length > 0,
                disabled: fileUploadDisabled,
              }"
              @click.stop="triggerFileUpload"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                class="control-icon"
              >
                <path
                  d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
                />
              </svg>
              <span v-if="fileAttachments.length" class="attachment-count">{{ fileAttachments.length }}</span>
            </div>
          </t-tooltip>

          <t-tooltip content="选择知识库（可多选）" placement="top" theme="light">
            <div
              ref="kbButtonRef"
              class="control-btn kb-btn"
              :class="{ active: selectedKbCount > 0 }"
              @click.stop="openKbSelector"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 20 20"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                class="control-icon at-icon"
              >
                <circle cx="10" cy="10" r="3.5" stroke="currentColor" stroke-width="1.8" />
                <path
                  d="M13.5 10V11.5C13.5 12.163 13.7634 12.7989 14.2322 13.2678C14.7011 13.7366 15.337 14 16 14C16.663 14 17.2989 13.7366 17.7678 13.2678C18.2366 12.7989 18.5 12.163 18.5 11.5V10C18.5 7.74566 17.6045 5.58365 16.0104 3.98959C14.4163 2.39553 12.2543 1.5 10 1.5C7.74566 1.5 5.58365 2.39553 3.98959 3.98959C2.39553 5.58365 1.5 7.74566 1.5 10C1.5 12.2543 2.39553 14.4163 3.98959 16.0104C5.58365 17.6045 7.74566 18.5 10 18.5H12"
                  stroke="currentColor"
                  stroke-width="1.8"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                />
              </svg>
              <span v-if="selectedKbCount > 0" class="kb-count">{{ selectedKbCount }}</span>
            </div>
          </t-tooltip>

          <t-tooltip
            v-if="showSkillMention"
            content="点名技能（也可输入 @）"
            placement="top"
            theme="light"
          >
            <div
              class="control-btn skill-btn"
              :class="{ active: selectedSkillChips.length > 0 || mentionOpen }"
              @click.stop="openMentionFromButton"
            >
              <t-icon :name="SKILL_ICON" size="16px" class="control-icon" />
              <span v-if="selectedSkillChips.length" class="skill-count">{{ selectedSkillChips.length }}</span>
            </div>
          </t-tooltip>

          <t-tooltip v-if="showWebToggle" :content="webTooltip" placement="top" theme="light">
            <div
              class="control-btn web-btn"
              :class="{
                active: webSearchEnabled,
                disabled: !webSearchAvailable,
              }"
              @click.stop="toggleWebSearch"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                stroke-linejoin="round"
                class="control-icon"
              >
                <circle cx="12" cy="12" r="9" />
                <path d="M3 12h18" />
                <path d="M12 3a14 14 0 0 1 0 18" />
                <path d="M12 3a14 14 0 0 0 0 18" />
              </svg>
            </div>
          </t-tooltip>

          <t-tooltip v-if="showGraphStatus" :content="graphTooltip" placement="top" theme="light">
            <div
              class="control-btn graph-btn"
              :class="{ active: graphReady, disabled: !graphReady }"
              @click.stop="onGraphClick"
            >
              <t-icon name="relation" size="16px" class="control-icon" />
            </div>
          </t-tooltip>

          <div class="control-selectors">
            <ChatInputAgentDropdown
              v-model:selected-agent-id="selectedAgentId"
              :agents="agents"
            />
            <ChatInputModelDropdown
              v-model:selected-model-id="selectedChatModelId"
              :all-models="allModels"
            />
          </div>
        </div>

        <div class="control-right">
          <t-tooltip v-if="streaming" content="停止生成" placement="top">
            <div class="control-btn stop-btn" @click="emit('stop')"></div>
          </t-tooltip>
          <div
            v-else
            class="control-btn send-btn"
            :class="{ disabled: !canSend }"
            title="发送"
            @click="canSend && emit('send')"
          >
            <img :src="sendIcon" alt="发送" />
          </div>
        </div>
      </div>
    </div>

    <ChatKnowledgeBaseSelector
      :visible="showKbSelector"
      :anchor-el="kbButtonRef"
      :kb-list="kbList"
      :selected-kb-ids="selectedKbIds"
      @close="showKbSelector = false"
      @update:selected-kb-ids="selectedKbIds = $event"
    />

    <p class="input-hint">内容由 AI 生成，请注意甄别准确性</p>
  </div>
</template>

<style scoped>
.answers-input {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.rich-input-container {
  position: relative;
  width: 100%;
  max-width: 960px;
  background: var(--td-bg-color-container);
  border-radius: 12px;
  border: 1px solid var(--td-component-stroke);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04), 0 8px 16px -4px rgba(0, 0, 0, 0.06);
}

.rich-input-container:focus-within {
  border-color: var(--td-brand-color);
}

.skill-mention-menu {
  position: absolute;
  left: 12px;
  width: 280px;
  max-width: calc(100% - 24px);
  bottom: calc(100% - 8px);
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 280px;
  overflow: auto;
  padding: 8px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 12px;
  background: var(--td-bg-color-container);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

.skill-mention-menu__header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
}

.skill-mention-menu__header .t-icon {
  color: #7c3aed;
}

.skill-mention-empty {
  margin: 0;
  padding: 10px 8px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.skill-mention-item {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  width: 100%;
  margin: 0;
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.skill-mention-item.active,
.skill-mention-item:hover {
  background: var(--td-bg-color-secondarycontainer);
}

.skill-mention-item__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: color-mix(in srgb, #7c3aed 12%, transparent);
  color: #7c3aed;
}

.skill-mention-item__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.skill-mention-item__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.skill-mention-item__desc {
  font-size: 12px;
  line-height: 1.4;
  color: var(--td-text-color-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.selected-tags-inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
  border-radius: 11px 11px 0 0;
}

.mention-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 26px;
  padding: 3px 7px 3px 6px;
  border-radius: 6px;
  border: 0.5px solid var(--td-component-border);
  background: var(--td-bg-color-secondarycontainer);
  box-sizing: border-box;
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
}

.mention-chip--kb .mention-chip__icon-wrap {
  color: var(--td-brand-color);
}

.mention-chip--skill .mention-chip__icon-wrap--skill {
  color: #7c3aed;
}

.mention-chip__icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.mention-chip__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
}

.mention-chip__graph {
  flex-shrink: 0;
  color: var(--td-brand-color);
}

.mention-chip__name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mention-chip__remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 0.5;
}

.mention-chip__remove:hover {
  opacity: 1;
  background: var(--td-bg-color-component);
}

.image-preview-bar {
  display: flex;
  gap: 8px;
  padding: 8px 12px 4px;
  flex-wrap: wrap;
}

.image-preview-item {
  position: relative;
  width: 60px;
  height: 60px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid var(--td-border-level-1-color, #e7e7e7);
}

.image-preview-thumb {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.image-preview-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  cursor: pointer;
  line-height: 1;
}

.attachment-preview-bar {
  display: flex;
  gap: 8px;
  padding: 8px 12px 4px;
  flex-wrap: wrap;
}

.attachment-preview-item {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  padding: 8px 32px 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--td-border-level-1-color, #e7e7e7);
  background: var(--td-bg-color-container);
  max-width: 240px;
  min-width: 140px;
}

.attachment-preview-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-preview-meta {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.attachment-preview-status {
  font-size: 11px;
  color: var(--td-text-color-secondary);
}

.attachment-preview-status.is-failed {
  color: var(--td-error-color);
}

.attachment-preview-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 16px;
  height: 16px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  cursor: pointer;
}

.rich-input-container :deep(.t-textarea) {
  border: none;
  box-shadow: none;
}

.rich-input-container :deep(.t-textarea__inner) {
  width: 100%;
  max-height: 200px !important;
  min-height: 120px !important;
  resize: none;
  color: var(--td-text-color-primary);
  font-size: 16px;
  line-height: 24px;
  padding: 12px 16px 56px;
  border: none;
  box-sizing: border-box;
  background: transparent;
  box-shadow: none;
  border-radius: 0 0 12px 12px;
}

.rich-input-container:not(.has-tags) :deep(.t-textarea__inner) {
  border-radius: 12px;
  padding-top: 16px;
}

.rich-input-container.has-tags :deep(.t-textarea__inner) {
  border-radius: 0 0 12px 12px;
}

.control-bar {
  position: absolute;
  bottom: 12px;
  left: 16px;
  right: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  max-height: 56px;
  z-index: 10;
  background: linear-gradient(
    to bottom,
    rgba(255, 255, 255, 0) 0%,
    var(--td-bg-color-container) 40%,
    var(--td-bg-color-container) 100%
  );
  pointer-events: auto;
  padding-top: 8px;
}

.control-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  flex-wrap: wrap;
  min-width: 0;
}

.control-selectors {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  min-width: 0;
}

.control-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.control-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 10px;
  border-radius: 6px;
  color: var(--td-text-color-secondary);
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  user-select: none;
  flex-shrink: 0;
}

.control-btn:hover:not(.disabled) {
  background: var(--td-bg-color-secondarycontainer-hover);
}

.control-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.control-icon {
  width: 18px;
  height: 18px;
}

.kb-btn,
.skill-btn,
.image-upload-btn,
.attachment-upload-btn,
.web-btn,
.graph-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  min-width: auto;
  position: relative;
}

.kb-btn.active,
.image-upload-btn.active,
.attachment-upload-btn.active,
.web-btn.active,
.graph-btn.active {
  background: rgba(16, 185, 129, 0.1);
  color: var(--td-brand-color);
}

.skill-btn.active {
  background: rgba(124, 58, 237, 0.1);
  color: #7c3aed;
}

.kb-count,
.image-count,
.attachment-count {
  position: absolute;
  top: -2px;
  right: -2px;
  background: var(--td-brand-color);
  color: #fff;
  font-size: 10px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

.skill-count {
  position: absolute;
  top: -2px;
  right: -2px;
  background: #7c3aed;
  color: #fff;
  font-size: 10px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}

.stop-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  background: rgba(16, 185, 129, 0.08);
  color: var(--td-brand-color);
  border: 1.5px solid rgba(16, 185, 129, 0.2);
}

.stop-btn:hover {
  background: rgba(16, 185, 129, 0.12);
  border-color: var(--td-brand-color);
}

.stop-btn::before {
  content: '';
  width: 12px;
  height: 12px;
  background: var(--td-brand-color);
  border-radius: 50%;
  display: block;
  animation: stopBtnPulse 1.5s ease-in-out infinite;
}

@keyframes stopBtnPulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(0.75);
    opacity: 0.6;
  }
}

.send-btn {
  width: 28px;
  height: 28px;
  padding: 0;
  background-color: var(--td-brand-color);
}

.send-btn:hover:not(.disabled) {
  background-color: var(--td-brand-color-active);
}

.send-btn.disabled {
  background-color: var(--td-success-color-light);
  cursor: not-allowed;
}

.send-btn img {
  width: 16px;
  height: 16px;
}

.input-hint {
  max-width: 960px;
  width: 100%;
  margin: 8px auto 0;
  text-align: center;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}
</style>
