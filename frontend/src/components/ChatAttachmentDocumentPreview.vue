<script setup lang="ts">
import { computed } from 'vue'
import { attachmentPreviewUrl } from '@/api/temporaryAttachments'
import DocumentFilePreview from '@/components/DocumentFilePreview.vue'

const props = defineProps<{
  sessionId: string
  attachmentId: string
  fileType: string
  fileName: string
  active: boolean
  fillHeight?: boolean
}>()

const previewUrl = computed(() => attachmentPreviewUrl(props.sessionId, props.attachmentId))
</script>

<template>
  <DocumentFilePreview
    :file-url="previewUrl"
    :file-name="fileName"
    :active="active"
    :fill-height="fillHeight"
  />
</template>
