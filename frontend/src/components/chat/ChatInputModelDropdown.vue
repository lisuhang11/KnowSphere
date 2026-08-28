<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { ModelInfo } from '@/api/models'
import { modelDisplayName } from '@/utils/modelDefaults'
import { filterModelsByType } from '@/components/modelSelectorFilter'

const props = defineProps<{
  allModels: ModelInfo[]
  selectedModelId: string
}>()

const emit = defineEmits<{
  'update:selectedModelId': [string]
}>()

const router = useRouter()
const triggerRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const dropdownStyle = ref<Record<string, string>>({})

const models = computed(() => filterModelsByType(props.allModels, 'KnowledgeQA'))

const displayName = computed(() => {
  const m = models.value.find((x) => x.id === props.selectedModelId)
  return m ? modelDisplayName(m) : '选择模型'
})

function updatePosition() {
  const anchor = triggerRef.value
  if (!anchor) return
  const rect = anchor.getBoundingClientRect()
  const width = 280
  let left = rect.left
  if (left + width > window.innerWidth - 12) {
    left = rect.left + rect.width - width
  }
  dropdownStyle.value = {
    position: 'fixed',
    top: `${rect.top - 8}px`,
    left: `${Math.max(12, left)}px`,
    width: `${width}px`,
    transform: 'translateY(-100%)',
    zIndex: '10000',
  }
}

function toggle() {
  visible.value = !visible.value
}

function close() {
  visible.value = false
}

function selectModel(id: string) {
  emit('update:selectedModelId', id)
  close()
}

function goAddModel() {
  close()
  router.push('/models')
}

watch(visible, async (open) => {
  if (!open) return
  updatePosition()
  await nextTick()
})

defineExpose({ triggerRef, close })
</script>

<template>
  <div class="model-display">
    <div ref="triggerRef" class="model-selector-trigger" @click.stop="toggle">
      <span class="model-selector-name">{{ displayName }}</span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="currentColor"
        class="model-dropdown-arrow"
        :class="{ rotate: visible }"
      >
        <path d="M2.5 4.5L6 8L9.5 4.5H2.5Z" />
      </svg>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="visible" class="model-selector-overlay" @click="close">
      <div class="model-selector-dropdown" :style="dropdownStyle" @click.stop>
        <div class="model-selector-header">
          <span>对话模型</span>
          <button type="button" class="model-selector-add" @click="goAddModel">
            <span class="add-icon">+</span>
            <span class="add-text">添加模型</span>
          </button>
        </div>
        <div class="model-selector-content">
          <div
            v-for="model in models"
            :key="model.id"
            class="model-option"
            :class="{ selected: model.id === selectedModelId }"
            @click="selectModel(model.id || '')"
          >
            <div class="model-option-left">
              <div class="model-option-icon">
                <t-icon name="chat" size="14px" />
              </div>
              <div class="model-option-name-wrap">
                <span class="model-option-name">{{ modelDisplayName(model) }}</span>
                <span v-if="model.display_name" class="model-option-raw-name">{{ model.name }}</span>
              </div>
            </div>
          </div>
          <div v-if="models.length === 0" class="model-option empty">暂无可用模型</div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.model-display {
  display: flex;
  align-items: center;
  margin-left: auto;
  flex-shrink: 0;
}

.model-selector-trigger {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  min-width: 100px;
  height: 22px;
  border-radius: 6px;
  border: 0.5px solid var(--td-component-border);
  transition: background 0.12s, border-color 0.12s;
  cursor: pointer;
}

.model-selector-trigger:hover {
  background: var(--td-bg-color-secondarycontainer-hover);
}

.model-selector-name {
  flex: 1;
  font-size: 12px;
  font-weight: 500;
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-dropdown-arrow {
  width: 10px;
  height: 10px;
  color: var(--td-text-color-placeholder);
  flex-shrink: 0;
  transition: transform 0.12s;
}

.model-dropdown-arrow.rotate {
  transform: rotate(180deg);
}

.model-selector-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: transparent;
}

.model-selector-dropdown {
  background: var(--td-bg-color-container);
  border: 0.5px solid var(--td-component-border);
  border-radius: 10px;
  box-shadow: var(--td-shadow-2);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  animation: modelSelectorFadeIn 0.15s ease-out;
}

@keyframes modelSelectorFadeIn {
  from {
    opacity: 0;
    transform: translateY(-100%) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(-100%) scale(1);
  }
}

.model-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-bottom: 0.5px solid var(--td-component-stroke);
  font-size: 12px;
  font-weight: 500;
  color: var(--td-text-color-secondary);
}

.model-selector-add {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: var(--td-brand-color);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
}

.model-selector-add:hover {
  background: var(--td-bg-color-secondarycontainer);
}

.model-selector-content {
  max-height: 260px;
  overflow-y: auto;
  padding: 6px 8px;
}

.model-option {
  display: flex;
  align-items: center;
  padding: 6px 8px;
  cursor: pointer;
  border-radius: 6px;
  margin-bottom: 4px;
}

.model-option:last-child {
  margin-bottom: 0;
}

.model-option:hover,
.model-option.selected {
  background: var(--td-bg-color-secondarycontainer);
}

.model-option.empty {
  color: var(--td-text-color-placeholder);
  cursor: default;
  justify-content: center;
  padding: 20px 8px;
}

.model-option-left {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
}

.model-option-icon {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--td-text-color-secondary);
}

.model-option-name-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

.model-option-name {
  font-size: 12px;
  color: var(--td-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-option-raw-name {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
  flex-shrink: 0;
}
</style>
