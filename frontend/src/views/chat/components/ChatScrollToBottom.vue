<script setup lang="ts">
defineEmits<{
  click: []
}>()
</script>

<template>
  <button type="button" class="scroll-to-bottom-btn" aria-label="滚动到底部" @click="$emit('click')">
    <t-icon name="chevron-down" size="20px" />
  </button>
</template>

<style scoped>
.scroll-to-bottom-btn {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 140px;
  z-index: 10;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--td-bg-color-container);
  border: 1px solid var(--td-component-stroke);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--td-text-color-secondary);
  transition: all 0.2s ease;
}

.scroll-to-bottom-btn:hover {
  background: var(--td-bg-color-container-hover);
  color: var(--td-text-color-primary);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.scroll-to-bottom-btn:active {
  transform: translateX(-50%) scale(0.92);
}
</style>
