<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useChatReferencesDrawer } from '@/composables/useChatReferencesDrawer'
import {
  buildReferenceSections,
  formatReferenceSnippet,
  openExternalUrl,
  resolveReferenceHighlightKey,
  type ReferenceListItem,
} from '@/utils/referenceSources'

const props = defineProps<{
  overlayBreakpoint?: number
}>()

const drawer = useChatReferencesDrawer()

const listElement = ref<HTMLElement | null>(null)
const itemElements = new Map<string, HTMLElement>()
const expandedKeys = reactive(new Set<string>())
const panelEntered = ref(false)

const visible = computed(() => drawer?.visible.value ?? false)
const references = computed(() => drawer?.references.value ?? [])
const highlight = computed(() => drawer?.highlight.value ?? null)

const useOverlay = computed(() => {
  if (typeof window === 'undefined') return false
  return window.innerWidth < (props.overlayBreakpoint ?? 960)
})

const sections = computed(() => buildReferenceSections(references.value))
const totalCount = computed(() => sections.value.reduce((sum, s) => sum + s.items.length, 0))

const activeHighlightKey = computed(() =>
  resolveReferenceHighlightKey(references.value, highlight.value),
)

function close() {
  drawer?.close()
}

function setItemRef(key: string, el: unknown) {
  const node = el as HTMLElement | null
  if (!node) {
    itemElements.delete(key)
    return
  }
  itemElements.set(key, node)
}

function hasMoreContent(item: ReferenceListItem) {
  const content = String(item.content || '').trim()
  const snippet = String(item.snippet || '').replace(/…$/, '').trim()
  if (!content) return false
  if (!snippet) return true
  return content.length > snippet.length + 8
}

function toggleDocumentSnippet(item: ReferenceListItem) {
  if (expandedKeys.has(item.key)) {
    expandedKeys.delete(item.key)
    return
  }
  expandedKeys.add(item.key)
}

function onOpenWeb(item: ReferenceListItem, event: MouseEvent) {
  openExternalUrl(item.url, event)
}

async function scrollToHighlight() {
  if (!panelEntered.value) return
  const key = activeHighlightKey.value
  if (!key) return
  await nextTick()
  const el = itemElements.get(key)
  const container = listElement.value
  if (!el || !container) return
  const itemRect = el.getBoundingClientRect()
  const containerRect = container.getBoundingClientRect()
  let nextTop: number | null = null
  if (itemRect.top < containerRect.top) {
    nextTop = container.scrollTop + itemRect.top - containerRect.top - 8
  } else if (itemRect.bottom > containerRect.bottom) {
    nextTop = container.scrollTop + itemRect.bottom - containerRect.bottom + 8
  }
  if (nextTop !== null) {
    container.scrollTo({ top: Math.max(0, nextTop), behavior: 'smooth' })
  }
}

function handlePanelAfterEnter() {
  panelEntered.value = true
  void scrollToHighlight()
}

watch(activeHighlightKey, () => {
  void scrollToHighlight()
})

watch(highlight, () => {
  void scrollToHighlight()
})

watch(visible, (open) => {
  if (!open) {
    panelEntered.value = false
    expandedKeys.clear()
  }
})
</script>

<template>
  <Teleport to="body">
    <Transition name="references-panel" @after-enter="handlePanelAfterEnter">
      <aside
        v-if="visible"
        class="chat-references-panel"
        :class="{ 'is-overlay': useOverlay }"
        role="complementary"
        aria-label="引用来源"
      >
        <header class="chat-references-panel__header">
          <h3 class="chat-references-panel__title">
            引用来源<span v-if="totalCount" class="chat-references-panel__count"> · {{ totalCount }}</span>
          </h3>
          <button type="button" class="chat-references-panel__close" aria-label="关闭" @click="close">
            <t-icon name="close" size="20px" />
          </button>
        </header>

        <div ref="listElement" class="chat-references-panel__body">
          <div v-if="sections.length === 0" class="chat-references-panel__empty">暂无引用</div>

          <section v-for="section in sections" :key="section.id" class="chat-references-panel__section">
            <article
              v-for="item in section.items"
              :key="item.key"
              :ref="(el) => setItemRef(item.key, el)"
              class="reference-item"
              :class="{
                'is-highlighted': item.key === activeHighlightKey,
                'is-web': Boolean(item.url),
              }"
            >
              <a
                v-if="item.url"
                class="reference-item__body reference-item__body--link"
                :href="item.url"
                target="_blank"
                rel="noopener noreferrer"
                :title="item.url"
                @click="onOpenWeb(item, $event)"
              >
                <div class="reference-item__document">
                  <t-icon name="link" class="reference-item__doc-icon" />
                  <div class="reference-item__document-main">
                    <h5 class="reference-item__title">{{ item.title }}</h5>
                    <p class="reference-item__host">
                      {{ item.host || item.url }}
                      <span class="reference-item__open">打开网页</span>
                    </p>
                    <p v-if="item.snippet" class="reference-item__snippet">
                      {{ formatReferenceSnippet(item.snippet) }}
                    </p>
                  </div>
                </div>
              </a>
              <div
                v-else
                class="reference-item__body"
                :class="{ 'is-expandable': hasMoreContent(item) }"
                role="button"
                tabindex="0"
                @click="hasMoreContent(item) && toggleDocumentSnippet(item)"
                @keydown.enter="hasMoreContent(item) && toggleDocumentSnippet(item)"
              >
                <div class="reference-item__document">
                  <t-icon name="file" class="reference-item__doc-icon" />
                  <div class="reference-item__document-main">
                    <h5 class="reference-item__title">{{ item.title }}</h5>
                    <p v-if="item.snippet && !expandedKeys.has(item.key)" class="reference-item__snippet">
                      {{ formatReferenceSnippet(item.snippet) }}
                    </p>
                    <div v-if="expandedKeys.has(item.key)" class="reference-item__content">
                      {{ formatReferenceSnippet(item.content) }}
                    </div>
                  </div>
                </div>
              </div>
            </article>
          </section>
        </div>
      </aside>
    </Transition>
  </Teleport>

  <Transition name="references-backdrop">
    <div v-if="visible && useOverlay" class="chat-references-panel__backdrop" @click="close" />
  </Transition>
</template>

<style scoped>
.chat-references-panel__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.28);
  z-index: 1200;
}

.chat-references-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(420px, 100vw);
  z-index: 1201;
  display: flex;
  flex-direction: column;
  background: var(--td-bg-color-container);
  border-left: 1px solid var(--td-component-stroke);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.06);
}

.chat-references-panel.is-overlay {
  box-shadow: -12px 0 32px rgba(0, 0, 0, 0.12);
}

.chat-references-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.chat-references-panel__title {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--td-text-color-secondary);
}

.chat-references-panel__count {
  color: var(--td-text-color-placeholder);
}

.chat-references-panel__close {
  border: 0;
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
  width: 36px;
  height: 36px;
  border-radius: 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.chat-references-panel__close:hover {
  background: color-mix(in srgb, var(--td-text-color-primary) 8%, var(--td-bg-color-secondarycontainer));
}

.chat-references-panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 12px 24px;
}

.chat-references-panel__empty {
  padding: 24px 8px;
  text-align: center;
  color: var(--td-text-color-placeholder);
  font-size: 13px;
}

.chat-references-panel__section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reference-item {
  border-radius: 12px;
  transition: background-color 0.15s ease;
}

.reference-item:hover:not(.is-highlighted) {
  background: color-mix(in srgb, var(--td-text-color-primary) 4%, transparent);
}

.reference-item.is-highlighted {
  background: var(--td-bg-color-secondarycontainer);
}

.reference-item__body {
  display: block;
  padding: 10px 12px;
  color: inherit;
  text-decoration: none;
}

.reference-item__body--link {
  cursor: pointer;
}

.reference-item__body--link:hover .reference-item__title {
  color: var(--td-brand-color);
}

.reference-item__body.is-expandable {
  cursor: pointer;
}

.reference-item__document {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}

.reference-item__doc-icon {
  flex-shrink: 0;
  margin-top: 3px;
  font-size: 16px;
  color: var(--td-text-color-primary);
}

.reference-item__document-main {
  flex: 1;
  min-width: 0;
}

.reference-item__title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--td-text-color-primary);
  word-break: break-word;
}

.reference-item__host {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--td-brand-color);
  word-break: break-all;
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.reference-item__open {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--td-brand-color);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.reference-item__snippet,
.reference-item__content {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--td-text-color-secondary);
  word-break: break-word;
}

.references-panel-enter-active,
.references-panel-leave-active {
  transition: transform 0.28s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.references-panel-enter-from,
.references-panel-leave-to {
  transform: translateX(100%);
}

.references-backdrop-enter-active,
.references-backdrop-leave-active {
  transition: opacity 0.2s ease;
}

.references-backdrop-enter-from,
.references-backdrop-leave-to {
  opacity: 0;
}
</style>
