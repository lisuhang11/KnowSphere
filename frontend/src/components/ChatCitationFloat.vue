<script setup lang="ts">
import type { CitationFloatState } from '@/composables/useChatCitationPopover'

defineProps<{
  float: CitationFloatState
  onEnter?: () => void
  onLeave?: () => void
}>()
</script>

<template>
  <Teleport to="body">
    <div
      v-if="float.visible"
      class="chat-citation-float"
      :style="{ top: `${float.top}px`, left: `${float.left}px` }"
      @mouseenter="onEnter?.()"
      @mouseleave="onLeave?.()"
    >
      <template v-if="float.type === 'web'">
        <div class="chat-citation-float__title">{{ float.title || float.url }}</div>
        <a
          v-if="float.url"
          class="chat-citation-float__link"
          :href="float.url"
          target="_blank"
          rel="noopener noreferrer"
        >
          {{ float.url }}
        </a>
      </template>
      <template v-else>
        <div class="chat-citation-float__title">{{ float.title }}</div>
        <div v-if="float.loading" class="chat-citation-float__muted">加载中…</div>
        <div v-else-if="float.error" class="chat-citation-float__error">{{ float.error }}</div>
        <div v-else class="chat-citation-float__body">{{ float.content }}</div>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.chat-citation-float {
  position: absolute;
  z-index: 1300;
  width: min(320px, calc(100vw - 24px));
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  pointer-events: auto;
}

.chat-citation-float__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  line-height: 1.4;
  margin-bottom: 6px;
  word-break: break-word;
}

.chat-citation-float__body {
  font-size: 13px;
  line-height: 1.6;
  color: var(--td-text-color-secondary);
  max-height: 180px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.chat-citation-float__muted {
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.chat-citation-float__error {
  font-size: 12px;
  color: var(--td-error-color);
}

.chat-citation-float__link {
  display: block;
  font-size: 12px;
  color: var(--td-brand-color);
  word-break: break-all;
  text-decoration: none;
}

.chat-citation-float__link:hover {
  text-decoration: underline;
}
</style>
