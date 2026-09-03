<script setup lang="ts">
import { computed, ref } from 'vue'
import PicturePreview from '@/components/PicturePreview.vue'
import { useChatAttachmentPreviewDrawer } from '@/composables/useChatAttachmentPreviewDrawer'
import type { ChatAttachmentMeta, ChatSkillMeta } from '@/composables/useSessionChat'
import {
  isPreviewableAttachment,
  resolveAttachmentFileType,
} from '@/utils/attachmentPreview'
import { formatFileSize, getFileExt } from '@/utils/fileFormat'
import { SKILL_ICON } from '@/utils/skillMention'

const props = defineProps<{
  sessionId?: string | null
  content?: string
  images?: Array<{ url: string }>
  attachments?: ChatAttachmentMeta[]
  skills?: ChatSkillMeta[]
}>()

const attachmentDrawer = useChatAttachmentPreviewDrawer()

const previewVisible = ref(false)
const previewUrl = ref('')

const hasImages = computed(() => Boolean(props.images?.length))
const hasAttachments = computed(() => Boolean(props.attachments?.length))
const hasSkills = computed(() => Boolean(props.skills?.length))
const showText = computed(() => {
  const text = (props.content || '').trim()
  if (!text) return false
  if (text === '（附件）' || text === '（图片）') return false
  return true
})

function previewImage(event: MouseEvent) {
  const src = (event.target as HTMLImageElement)?.src
  if (!src) return
  previewUrl.value = src
  previewVisible.value = true
}

function closePreview() {
  previewVisible.value = false
  previewUrl.value = ''
}

function openAttachmentPreview(att: ChatAttachmentMeta) {
  if (!props.sessionId || !att.id || !isPreviewableAttachment(att)) return
  attachmentDrawer?.open({
    sessionId: props.sessionId,
    attachmentId: att.id,
    fileName: att.file_name,
    fileType: resolveAttachmentFileType(att.file_name, att.file_type),
  })
}

function attachmentCardClass(att: ChatAttachmentMeta) {
  return {
    'is-previewable': isPreviewableAttachment(att),
  }
}
</script>

<template>
  <div class="user-msg-container">
    <div v-if="hasSkills" class="user-skills">
      <span v-for="skill in skills" :key="skill.name" class="user-skill-tag">
        <span class="user-skill-tag__icon">
          <t-icon :name="SKILL_ICON" />
        </span>
        <span class="user-skill-tag__name">{{ skill.name }}</span>
      </span>
    </div>
    <div v-if="hasImages" class="user-images">
      <img
        v-for="(img, i) in images"
        :key="i"
        :src="img.url"
        alt="上传图片"
        class="user-image-thumb"
        @click="previewImage"
      />
    </div>

    <div v-if="hasAttachments" class="user-attachments">
      <div
        v-for="(att, idx) in attachments"
        :key="att.id || idx"
        class="user-attachment-card"
        :class="attachmentCardClass(att)"
        @click="openAttachmentPreview(att)"
      >
        <div class="attachment-card-icon">
          <svg viewBox="0 0 40 48" fill="none" xmlns="http://www.w3.org/2000/svg" width="36" height="44">
            <rect width="40" height="48" rx="4" fill="#4A90D9" />
            <path d="M8 6h16l8 8v28a2 2 0 01-2 2H8a2 2 0 01-2-2V8a2 2 0 012-2z" fill="#5BA3E8" />
            <path d="M24 6l8 8h-6a2 2 0 01-2-2V6z" fill="#3A7BC8" />
            <rect x="10" y="20" width="20" height="2" rx="1" fill="white" fill-opacity="0.9" />
            <rect x="10" y="26" width="20" height="2" rx="1" fill="white" fill-opacity="0.9" />
            <rect x="10" y="32" width="14" height="2" rx="1" fill="white" fill-opacity="0.9" />
          </svg>
        </div>
        <div class="attachment-card-info">
          <div class="attachment-card-name">{{ att.file_name }}</div>
          <div class="attachment-card-meta">
            {{ getFileExt(att.file_name) }}<span v-if="att.file_size">&nbsp;·&nbsp;{{ formatFileSize(att.file_size) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showText" class="user-msg">{{ content }}</div>

    <PicturePreview :visible="previewVisible" :url="previewUrl" @close="closePreview" />
  </div>
</template>

<style scoped>
.user-msg-container {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  width: 100%;
}

.user-skills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  max-width: 100%;
}

.user-skill-tag {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 26px;
  max-width: 200px;
  padding: 3px 8px;
  border-radius: 6px;
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-secondarycontainer);
  box-sizing: border-box;
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
  color: var(--td-text-color-primary);
}

.user-skill-tag__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #7c3aed;
  font-size: 14px;
}

.user-skill-tag__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-msg {
  width: max-content;
  max-width: min(76%, 820px);
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--td-bg-color-secondarycontainer, #f0f0f0);
  color: var(--td-text-color-primary);
  font-size: 16px;
  line-height: 1.6;
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  box-sizing: border-box;
}

.user-images {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  max-width: 100%;
}

.user-image-thumb {
  width: 120px;
  height: 120px;
  object-fit: cover;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid var(--td-border-level-2-color, #e7e7e7);
  transition: opacity 0.2s;
}

.user-image-thumb:hover {
  opacity: 0.85;
}

.user-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
  max-width: 100%;
}

.user-attachment-card {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--td-border-level-1-color, #e7e7e7);
  background: var(--td-bg-color-container, #fff);
  max-width: 260px;
  min-width: 160px;
}

.user-attachment-card.is-previewable {
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.user-attachment-card.is-previewable:hover {
  border-color: var(--td-brand-color-light);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.attachment-card-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.attachment-card-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.attachment-card-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--td-text-color-primary, #333);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-card-meta {
  font-size: 11px;
  color: var(--td-text-color-secondary, #999);
  white-space: nowrap;
}
</style>
