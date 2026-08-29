<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import type { KnowledgeBase } from '@/api/knowledgeBases'

const DROPDOWN_WIDTH = 300
const OFFSET_Y = 8
const PREFERRED_HEIGHT = 280
const MIN_HEIGHT = 200
const TOP_MARGIN = 20

const props = defineProps<{
  visible: boolean
  anchorEl?: HTMLElement | null
  kbList: KnowledgeBase[]
  selectedKbIds: number[]
}>()

const emit = defineEmits<{
  close: []
  'update:selectedKbIds': [ids: number[]]
}>()

const searchQuery = ref('')
const highlightedIndex = ref(0)
const searchInputRef = ref<HTMLInputElement | null>(null)
const dropdownStyle = ref<Record<string, string>>({})

let resizeHandler: (() => void) | null = null
let scrollHandler: (() => void) | null = null

const selectedSet = computed(() => new Set(props.selectedKbIds))

const filtered = computed(() => {
  const list = props.kbList
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return list
  return list.filter((k) => k.name.toLowerCase().includes(q))
})

function resolveAnchorEl(): HTMLElement | null {
  const anchor = props.anchorEl
  if (!anchor) return null
  if (typeof anchor === 'object' && 'value' in anchor) {
    return (anchor as { value: HTMLElement | null }).value
  }
  return anchor
}

function updateDropdownPosition() {
  const anchor = resolveAnchorEl()
  const vw = window.innerWidth
  const vh = window.innerHeight

  const applyFallback = () => {
    const top = Math.max(80, vh / 2 - 160)
    dropdownStyle.value = {
      position: 'fixed',
      width: `${DROPDOWN_WIDTH}px`,
      left: `${Math.round((vw - DROPDOWN_WIDTH) / 2)}px`,
      top: `${Math.round(top)}px`,
      maxHeight: `${PREFERRED_HEIGHT}px`,
      transform: 'none',
      margin: '0',
      padding: '0',
    }
  }

  if (!anchor) {
    applyFallback()
    return
  }

  const rect = anchor.getBoundingClientRect()
  if (!rect.width || !rect.height) {
    applyFallback()
    return
  }

  let left = Math.floor(rect.left)
  const minLeft = 16
  const maxLeft = Math.max(16, vw - DROPDOWN_WIDTH - 16)
  left = Math.max(minLeft, Math.min(maxLeft, left))

  const spaceBelow = vh - rect.bottom
  const spaceAbove = rect.top

  let actualHeight: number
  let openBelow: boolean

  if (spaceBelow >= MIN_HEIGHT + OFFSET_Y) {
    actualHeight = Math.min(PREFERRED_HEIGHT, spaceBelow - OFFSET_Y - 16)
    openBelow = true
  } else {
    const availableHeight = spaceAbove - OFFSET_Y - TOP_MARGIN
    actualHeight =
      availableHeight >= PREFERRED_HEIGHT
        ? PREFERRED_HEIGHT
        : Math.max(MIN_HEIGHT, availableHeight)
    openBelow = false
  }

  if (openBelow) {
    dropdownStyle.value = {
      position: 'fixed',
      width: `${DROPDOWN_WIDTH}px`,
      left: `${left}px`,
      top: `${Math.floor(rect.bottom + OFFSET_Y)}px`,
      maxHeight: `${actualHeight}px`,
      transform: 'none',
      margin: '0',
      padding: '0',
    }
  } else {
    dropdownStyle.value = {
      position: 'fixed',
      width: `${DROPDOWN_WIDTH}px`,
      left: `${left}px`,
      bottom: `${Math.floor(vh - rect.top + OFFSET_Y)}px`,
      maxHeight: `${actualHeight}px`,
      transform: 'none',
      margin: '0',
      padding: '0',
    }
  }
}

function bindPositionListeners() {
  resizeHandler = () => updateDropdownPosition()
  scrollHandler = () => updateDropdownPosition()
  window.addEventListener('resize', resizeHandler, { passive: true })
  window.addEventListener('scroll', scrollHandler, { passive: true, capture: true })
}

function unbindPositionListeners() {
  if (resizeHandler) {
    window.removeEventListener('resize', resizeHandler)
    resizeHandler = null
  }
  if (scrollHandler) {
    window.removeEventListener('scroll', scrollHandler, { capture: true })
    scrollHandler = null
  }
}

function close() {
  emit('close')
}

function isSelected(id: number): boolean {
  return selectedSet.value.has(id)
}

function toggleKb(id: number) {
  const next = isSelected(id)
    ? props.selectedKbIds.filter((x) => x !== id)
    : [...props.selectedKbIds, id]
  emit('update:selectedKbIds', next)
}

function clearAll() {
  emit('update:selectedKbIds', [])
  close()
}

function moveSelection(delta: number) {
  if (!filtered.value.length) return
  highlightedIndex.value =
    (highlightedIndex.value + delta + filtered.value.length) % filtered.value.length
}

function confirmSelection() {
  const kb = filtered.value[highlightedIndex.value]
  if (kb) toggleKb(kb.id)
}

watch(
  () => props.visible,
  async (open) => {
    if (!open) {
      searchQuery.value = ''
      highlightedIndex.value = 0
      unbindPositionListeners()
      return
    }

    await nextTick()
    requestAnimationFrame(() => {
      updateDropdownPosition()
      requestAnimationFrame(() => {
        updateDropdownPosition()
        window.setTimeout(updateDropdownPosition, 50)
      })
    })
    bindPositionListeners()
    await nextTick()
    searchInputRef.value?.focus()
  },
)

watch(filtered, () => {
  highlightedIndex.value = 0
})

onUnmounted(unbindPositionListeners)
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="kb-overlay" @click="close">
      <div class="kb-dropdown" :style="dropdownStyle" @click.stop>
        <div class="kb-search">
          <input
            ref="searchInputRef"
            v-model="searchQuery"
            type="text"
            placeholder="搜索知识库…"
            class="kb-search-input"
            @keydown.down.prevent="moveSelection(1)"
            @keydown.up.prevent="moveSelection(-1)"
            @keydown.enter.prevent="confirmSelection"
            @keydown.esc="close"
          />
        </div>
        <div class="kb-list">
          <div
            v-for="(kb, index) in filtered"
            :key="kb.id"
            class="kb-item"
            :class="{
              selected: isSelected(kb.id),
              highlighted: highlightedIndex === index,
            }"
            @click="toggleKb(kb.id)"
            @mouseenter="highlightedIndex = index"
          >
            <div class="kb-item-left">
              <div class="checkbox" :class="{ checked: isSelected(kb.id) }">
                <svg v-if="isSelected(kb.id)" width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path
                    d="M10 3L4.5 8.5L2 6"
                    stroke="#fff"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                </svg>
              </div>
              <t-icon name="folder-open" class="kb-icon" />
              <div class="kb-name-wrap">
                <span class="kb-name">{{ kb.name }}</span>
              </div>
            </div>
          </div>
          <div v-if="filtered.length === 0" class="kb-empty">
            {{ searchQuery ? '无匹配知识库' : '暂无知识库' }}
          </div>
        </div>
        <div class="kb-actions">
          <button type="button" class="kb-btn" @click="clearAll">清除选择</button>
          <button type="button" class="kb-btn kb-btn--primary" @click="close">完成</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.kb-overlay,
.kb-overlay *,
.kb-overlay *::before,
.kb-overlay *::after {
  box-sizing: border-box;
}

.kb-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  background: transparent;
  touch-action: none;
}

.kb-dropdown {
  position: fixed !important;
  background: var(--td-bg-color-container);
  border: 0.5px solid var(--td-component-border);
  border-radius: 10px;
  box-shadow: var(--td-shadow-2, 0 6px 28px rgba(15, 23, 42, 0.08));
  overflow: hidden;
  display: flex;
  flex-direction: column;
  z-index: 10000;
  animation: kbFadeIn 0.15s ease-out;
}

@keyframes kbFadeIn {
  from {
    opacity: 0;
    transform: scale(0.98);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.kb-search {
  flex-shrink: 0;
  padding: 8px 10px;
  border-bottom: 0.5px solid var(--td-component-stroke);
}

.kb-search-input {
  width: 100%;
  padding: 6px 10px;
  font-size: 12px;
  border: 0.5px solid var(--td-component-stroke);
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
  outline: none;
}

.kb-search-input:focus {
  border-color: var(--td-brand-color);
  background: var(--td-bg-color-container);
}

.kb-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 6px 8px;
  overscroll-behavior: contain;
}

.kb-item {
  display: flex;
  align-items: center;
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}

.kb-item:last-child {
  margin-bottom: 0;
}

.kb-item:hover,
.kb-item.highlighted {
  background: var(--td-bg-color-secondarycontainer);
}

.kb-item.selected {
  background: var(--td-brand-color-light);
}

.kb-item-left {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-width: 0;
}

.checkbox {
  width: 16px;
  height: 16px;
  border-radius: 3px;
  border: 1.5px solid var(--td-component-border);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.checkbox.checked {
  background: var(--td-brand-color);
  border-color: var(--td-brand-color);
}

.kb-icon {
  flex-shrink: 0;
  color: var(--td-brand-color);
  font-size: 14px;
}

.kb-name {
  font-size: 13px;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kb-empty {
  padding: 20px 8px;
  text-align: center;
  font-size: 13px;
  color: var(--td-text-color-placeholder);
}

.kb-actions {
  flex-shrink: 0;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-top: 0.5px solid var(--td-component-stroke);
  background: var(--td-bg-color-secondarycontainer);
}

.kb-btn {
  border: none;
  background: transparent;
  color: var(--td-text-color-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 4px;
}

.kb-btn:hover {
  background: var(--td-bg-color-container-hover);
  color: var(--td-text-color-primary);
}

.kb-btn--primary {
  color: var(--td-brand-color);
  font-weight: 500;
}

.kb-btn--primary:hover {
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
}
</style>
