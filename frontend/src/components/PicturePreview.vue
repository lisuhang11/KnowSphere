/** 图片预览（对齐 WeKnora picture-preview.vue） */

<script setup lang="ts">
defineProps<{
  visible: boolean
  url: string
}>()

const emit = defineEmits<{
  close: []
}>()
</script>

<template>
  <t-image-viewer
    :visible="visible"
    close-on-overlay
    close-on-esc-keydown
    :images="url ? [url] : []"
    @close="emit('close')"
  />
</template>
