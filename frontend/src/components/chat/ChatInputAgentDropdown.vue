<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import type { AgentInfo } from '@/api/agents'

const props = defineProps<{
  agents: AgentInfo[]
  selectedAgentId: string
}>()

const emit = defineEmits<{
  'update:selectedAgentId': [string]
}>()

const router = useRouter()
const triggerRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const dropdownStyle = ref<Record<string, string>>({})

const activeAgents = computed(() => props.agents.filter((a) => a.status !== 'disabled'))

const displayName = computed(() => {
  const a = activeAgents.value.find((x) => x.id === props.selectedAgentId)
  return a?.name || '选择智能体'
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

function selectAgent(id: string) {
  emit('update:selectedAgentId', id)
  close()
}

function goManage() {
  close()
  router.push('/agents')
}

watch(visible, async (open) => {
  if (!open) return
  updatePosition()
  await nextTick()
})
</script>

<template>
  <div class="agent-display">
    <div ref="triggerRef" class="agent-selector-trigger" @click.stop="toggle">
      <t-icon name="user" size="14px" class="agent-selector-icon" />
      <span class="agent-selector-name">{{ displayName }}</span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="currentColor"
        class="agent-dropdown-arrow"
        :class="{ rotate: visible }"
      >
        <path d="M2.5 4.5L6 8L9.5 4.5H2.5Z" />
      </svg>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="visible" class="agent-selector-overlay" @click="close">
      <div class="agent-selector-dropdown" :style="dropdownStyle" @click.stop>
        <div class="agent-selector-header">
          <span>智能体</span>
          <button type="button" class="agent-selector-add" @click="goManage">
            <span class="add-icon">+</span>
            <span class="add-text">管理</span>
          </button>
        </div>
        <div class="agent-selector-content">
          <div
            v-for="agent in activeAgents"
            :key="agent.id"
            class="agent-option"
            :class="{ selected: agent.id === selectedAgentId }"
            @click="selectAgent(agent.id)"
          >
            <div class="agent-option-left">
              <div class="agent-option-icon">
                <t-icon name="user" size="14px" />
              </div>
              <div class="agent-option-name-wrap">
                <span class="agent-option-name">{{ agent.name }}</span>
                <span v-if="agent.is_builtin" class="agent-option-tag">内置</span>
              </div>
            </div>
            <p v-if="agent.description" class="agent-option-desc">{{ agent.description }}</p>
          </div>
          <div v-if="activeAgents.length === 0" class="agent-option empty">暂无智能体</div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.agent-display {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.agent-selector-trigger {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  min-width: 88px;
  max-width: 148px;
  height: 22px;
  border-radius: 6px;
  border: 0.5px solid var(--td-component-border);
  transition: background 0.12s, border-color 0.12s;
  cursor: pointer;
}

.agent-selector-trigger:hover {
  background: var(--td-bg-color-secondarycontainer-hover);
}

.agent-selector-icon {
  color: var(--td-text-color-placeholder);
  flex-shrink: 0;
}

.agent-selector-name {
  flex: 1;
  font-size: 12px;
  font-weight: 500;
  color: var(--td-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-dropdown-arrow {
  width: 10px;
  height: 10px;
  color: var(--td-text-color-placeholder);
  flex-shrink: 0;
  transition: transform 0.12s;
}

.agent-dropdown-arrow.rotate {
  transform: rotate(180deg);
}

.agent-selector-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: transparent;
}

.agent-selector-dropdown {
  background: var(--td-bg-color-container);
  border: 0.5px solid var(--td-component-border);
  border-radius: 10px;
  box-shadow: var(--td-shadow-2);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  animation: agentSelectorFadeIn 0.15s ease-out;
}

@keyframes agentSelectorFadeIn {
  from {
    opacity: 0;
    transform: translateY(-100%) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(-100%) scale(1);
  }
}

.agent-selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-bottom: 0.5px solid var(--td-component-stroke);
  font-size: 12px;
  font-weight: 500;
  color: var(--td-text-color-secondary);
}

.agent-selector-add {
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

.agent-selector-add:hover {
  background: var(--td-bg-color-secondarycontainer);
}

.agent-selector-content {
  max-height: 280px;
  overflow-y: auto;
  padding: 6px 8px;
}

.agent-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px;
  cursor: pointer;
  border-radius: 6px;
  margin-bottom: 4px;
}

.agent-option:last-child {
  margin-bottom: 0;
}

.agent-option:hover,
.agent-option.selected {
  background: var(--td-bg-color-secondarycontainer);
}

.agent-option.empty {
  color: var(--td-text-color-placeholder);
  cursor: default;
  justify-content: center;
  padding: 20px 8px;
}

.agent-option-left {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
}

.agent-option-icon {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--td-text-color-secondary);
}

.agent-option-name-wrap {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.agent-option-name {
  font-size: 12px;
  color: var(--td-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-option-tag {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--td-brand-color);
}

.agent-option-desc {
  margin: 0 0 0 24px;
  font-size: 11px;
  line-height: 1.4;
  color: var(--td-text-color-placeholder);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
