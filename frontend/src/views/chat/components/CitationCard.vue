<script setup lang="ts">
import type { Citation } from '@/api/sessions'

defineProps<{ citation: Citation }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="citation-card" @click.stop>
    <div class="cc-head">
      <span class="cc-badge">{{ citation.index }}</span>
      <span class="cc-file">
        {{ citation.file_name }}<template v-if="citation.chunk_index >= 0">
          · 第 {{ citation.chunk_index + 1 }} 段
        </template>
      </span>
      <t-icon name="close" size="16px" class="cc-close" @click="emit('close')" />
    </div>
    <p v-if="citation.snippet" class="cc-snippet">{{ citation.snippet }}</p>
  </div>
</template>

<style scoped>
.citation-card {
  margin-top: 8px;
  padding: 10px 12px;
  border: 1px solid var(--td-brand-color, #0052d9);
  border-radius: 8px;
  background: var(--td-brand-color-light, #f2f6ff);
}

.cc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.cc-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 9px;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
  background: var(--td-brand-color, #0052d9);
}

.cc-file {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cc-close {
  flex-shrink: 0;
  cursor: pointer;
  color: var(--td-text-color-placeholder);
}

.cc-close:hover {
  color: var(--td-text-color-primary);
}

.cc-snippet {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--td-text-color-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
</style>
