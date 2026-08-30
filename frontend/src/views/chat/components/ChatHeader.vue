<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { sessionId, type Session } from '@/api/sessions'
import type { ChatMsg } from '@/composables/useSessionChat'
import { useChatStore } from '@/stores/chat'
import { copyToClipboard } from '@/utils/clipboard'
import { buildSessionMarkdown } from '@/utils/sessionMarkdown'

const SESSION_TITLE_MAX_LENGTH = 80

type MenuMode = 'menu' | 'clear' | 'delete'

const props = defineProps<{
  session: Session | null
  messages?: ChatMsg[]
  hasReferencesPanel?: boolean
}>()

const emit = defineEmits<{
  cleared: []
  deleted: []
}>()

const chatStore = useChatStore()

const busyAction = ref('')
const menuVisible = ref(false)
const menuMode = ref<MenuMode>('menu')
const titleEditing = ref(false)
const titleDraft = ref('')
const titleInputRef = ref<HTMLInputElement | null>(null)

const displayTitle = computed(() => {
  if (!props.session) return '新对话'
  return chatStore.titleOf(props.session)
})

const isPinned = computed(() => Boolean(props.session?.is_pinned))

function onMenuVisibleChange(visible: boolean): void {
  if (!visible) menuMode.value = 'menu'
}

function enterConfirmMode(mode: 'clear' | 'delete'): void {
  menuMode.value = mode
}

function backToMenu(): void {
  if (busyAction.value) return
  menuMode.value = 'menu'
}

function startTitleEdit(): void {
  if (!props.session || busyAction.value) return
  menuVisible.value = false
  titleDraft.value = props.session.title || ''
  titleEditing.value = true
  nextTick(() => {
    titleInputRef.value?.focus()
    titleInputRef.value?.select()
  })
}

function cancelTitleEdit(): void {
  titleEditing.value = false
  titleDraft.value = ''
}

async function submitTitleEdit(): Promise<void> {
  if (!titleEditing.value || busyAction.value) return
  const session = props.session
  if (!session) {
    cancelTitleEdit()
    return
  }

  const title = titleDraft.value.trim().slice(0, SESSION_TITLE_MAX_LENGTH)
  const currentTitle = (session.title || '').trim()
  titleEditing.value = false
  titleDraft.value = ''
  if (!title || title === currentTitle) return

  busyAction.value = 'rename'
  try {
    await chatStore.renameThread(sessionId(session), title)
    MessagePlugin.success('会话已重命名')
  } catch {
    /* axios 拦截器已提示 */
  } finally {
    busyAction.value = ''
  }
}

async function copyText(text: string, successMsg: string): Promise<void> {
  const ok = await copyToClipboard(text)
  if (ok) MessagePlugin.success(successMsg)
  else MessagePlugin.error('复制失败')
}

async function togglePin(): Promise<void> {
  const session = props.session
  if (!session || busyAction.value) return
  busyAction.value = 'pin'
  menuVisible.value = false
  try {
    const wasPinned = session.is_pinned
    await chatStore.togglePin(sessionId(session))
    MessagePlugin.success(wasPinned ? '已取消置顶' : '已置顶')
  } catch {
    /* axios 拦截器已提示 */
  } finally {
    busyAction.value = ''
  }
}

async function copySessionId(): Promise<void> {
  if (!props.session) return
  menuVisible.value = false
  await copyText(sessionId(props.session), '会话 ID 已复制')
}

async function copyLink(): Promise<void> {
  menuVisible.value = false
  const url = new URL(window.location.href)
  url.search = ''
  url.hash = ''
  await copyText(url.toString(), '链接已复制')
}

async function copyMarkdown(): Promise<void> {
  const session = props.session
  if (!session || busyAction.value) return
  busyAction.value = 'markdown'
  menuVisible.value = false
  try {
    const markdown = buildSessionMarkdown({
      sessionId: sessionId(session),
      title: chatStore.titleOf(session),
      messages: props.messages ?? [],
      labels: {
        sessionId: '会话 ID',
        exportedAt: '导出时间',
        user: '用户',
        assistant: '助手',
        attachments: '附件',
        references: '引用',
      },
    })
    await copyText(markdown, 'Markdown 已复制')
  } catch {
    MessagePlugin.error('导出失败')
  } finally {
    busyAction.value = ''
  }
}

function openNewWindow(): void {
  menuVisible.value = false
  window.open(window.location.href, '_blank', 'noopener,noreferrer')
}

async function submitClearMessages(): Promise<void> {
  const session = props.session
  if (!session || busyAction.value) return
  busyAction.value = 'clear'
  try {
    menuVisible.value = false
    menuMode.value = 'menu'
    emit('cleared')
  } finally {
    busyAction.value = ''
  }
}

async function submitDeleteSession(): Promise<void> {
  const session = props.session
  if (!session || busyAction.value) return
  busyAction.value = 'delete'
  try {
    const id = sessionId(session)
    await chatStore.removeThread(id)
    menuVisible.value = false
    menuMode.value = 'menu'
    emit('deleted')
    MessagePlugin.success('会话已删除')
  } catch {
    /* axios 拦截器已提示 */
  } finally {
    busyAction.value = ''
  }
}

function onMenuAction(action: string): void {
  if (action === 'rename') {
    menuVisible.value = false
    startTitleEdit()
    return
  }
  if (action === 'pin') {
    void togglePin()
    return
  }
  menuVisible.value = false
  if (action === 'copyId') void copySessionId()
  else if (action === 'copyLink') void copyLink()
  else if (action === 'copyMarkdown') void copyMarkdown()
  else if (action === 'openNewWindow') openNewWindow()
}
</script>

<template>
  <header class="chat-header" :class="{ 'is-editing': titleEditing, 'is-docked': hasReferencesPanel }">
    <form
      v-if="titleEditing"
      class="chat-header__edit"
      @submit.prevent="submitTitleEdit"
      @click.stop
    >
      <input
        ref="titleInputRef"
        v-model="titleDraft"
        class="chat-header__edit-input"
        :maxlength="SESSION_TITLE_MAX_LENGTH"
        :disabled="busyAction === 'rename'"
        placeholder="输入会话标题"
        @keydown.esc.prevent="cancelTitleEdit"
        @blur="submitTitleEdit"
      />
    </form>
    <h1 v-else class="chat-header__title" :title="displayTitle" @dblclick="startTitleEdit">
      <t-icon v-if="isPinned" name="pin-filled" size="14px" class="chat-header__pin-icon" />
      <span class="chat-header__title-text">{{ displayTitle }}</span>
    </h1>
    <t-popup
      v-if="!titleEditing"
      v-model:visible="menuVisible"
      overlay-class-name="chat-header-menu-popup"
      trigger="click"
      destroy-on-close
      placement="bottom-left"
      :disabled="!session || Boolean(busyAction)"
      @visible-change="onMenuVisibleChange"
    >
      <button
        type="button"
        class="chat-header__menu-btn"
        :class="{ 'is-loading': Boolean(busyAction) }"
        :disabled="!session || Boolean(busyAction)"
        aria-label="更多操作"
        @click.stop
      >
        <t-icon v-if="busyAction" name="loading" size="14px" class="chat-header__menu-loading" />
        <t-icon v-else name="ellipsis" size="16px" />
      </button>
      <template #content>
        <div class="chat-header-menu" @click.stop>
          <template v-if="menuMode === 'menu'">
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('rename')">
              <t-icon class="chat-header-menu__icon" name="edit-1" />
              <span>重命名</span>
            </button>
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('pin')">
              <t-icon class="chat-header-menu__icon" :name="isPinned ? 'pin-filled' : 'pin'" />
              <span>{{ isPinned ? '取消置顶' : '置顶' }}</span>
            </button>
            <div class="chat-header-menu__divider" />
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('copyId')">
              <t-icon class="chat-header-menu__icon" name="copy" />
              <span>复制会话 ID</span>
            </button>
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('copyLink')">
              <t-icon class="chat-header-menu__icon" name="link" />
              <span>复制链接</span>
            </button>
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('copyMarkdown')">
              <t-icon class="chat-header-menu__icon" name="file-copy" />
              <span>复制 Markdown</span>
            </button>
            <button type="button" class="chat-header-menu__item" @click="onMenuAction('openNewWindow')">
              <t-icon class="chat-header-menu__icon" name="browse" />
              <span>新窗口打开</span>
            </button>
            <div class="chat-header-menu__divider" />
            <button type="button" class="chat-header-menu__item" @click="enterConfirmMode('clear')">
              <t-icon class="chat-header-menu__icon" name="clear" />
              <span>清空消息</span>
            </button>
            <button type="button" class="chat-header-menu__item is-danger" @click="enterConfirmMode('delete')">
              <t-icon class="chat-header-menu__icon" name="delete" />
              <span>删除会话</span>
            </button>
          </template>

          <div v-else class="chat-header-confirm">
            <div class="chat-header-confirm__title">
              {{ menuMode === 'clear' ? '清空消息？' : '删除会话？' }}
            </div>
            <div class="chat-header-confirm__body">
              {{
                menuMode === 'clear'
                  ? '将删除当前会话中的所有消息，会话本身会保留。'
                  : '删除后无法恢复，该会话的所有消息将一并移除。'
              }}
            </div>
            <div class="chat-header-confirm__footer">
              <button type="button" class="chat-header-confirm__btn" :disabled="Boolean(busyAction)" @click="backToMenu">
                取消
              </button>
              <button
                type="button"
                class="chat-header-confirm__btn is-danger"
                :disabled="Boolean(busyAction)"
                @click="menuMode === 'clear' ? submitClearMessages() : submitDeleteSession()"
              >
                {{ menuMode === 'clear' ? '清空' : '删除' }}
              </button>
            </div>
          </div>
        </div>
      </template>
    </t-popup>
  </header>
</template>

<style scoped lang="less">
.chat-header {
  position: absolute;
  top: 10px;
  left: 12px;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  max-width: min(280px, calc(100% - 24px));
  min-width: 0;
  padding: 2px 2px 2px 8px;
  border-radius: 8px;
  box-sizing: border-box;
  background: color-mix(in srgb, var(--td-bg-color-container) 88%, transparent);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  pointer-events: auto;

  &.is-editing {
    max-width: min(360px, calc(100% - 24px));
    padding: 2px;
  }

  @media (min-width: 960px) {
    &.is-docked {
      position: relative;
      top: auto;
      left: auto;
      align-self: stretch;
      z-index: 5;
      flex-shrink: 0;
      width: 100%;
      max-width: none;
      margin: 0;
      padding: 10px 12px;
      border-radius: 0;
      border-bottom: 1px solid var(--td-component-stroke);
      background: var(--td-bg-color-container);
      backdrop-filter: none;
      -webkit-backdrop-filter: none;
      box-sizing: border-box;

      &.is-editing {
        max-width: none;
        padding: 8px 12px;
      }
    }
  }
}

.chat-header__edit {
  flex: 1 1 auto;
  min-width: 0;
}

.chat-header__edit-input {
  width: 100%;
  max-width: 360px;
  height: 28px;
  padding: 0 8px;
  border: 1px solid var(--td-brand-color);
  border-radius: 5px;
  color: var(--td-text-color-primary);
  background: var(--td-bg-color-container);
  font-size: 14px;
  line-height: 26px;
  outline: none;
  box-sizing: border-box;
  box-shadow: 0 0 0 2px var(--td-brand-color-light);

  &:disabled {
    opacity: 0.7;
  }
}

.chat-header__title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  margin: 0;
  padding: 0;
  color: var(--td-text-color-secondary);
  font-size: 14px;
  font-weight: 500;
  line-height: 20px;
  cursor: default;
}

.chat-header__pin-icon {
  flex-shrink: 0;
  color: var(--td-brand-color);
}

.chat-header__title-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-header__menu-btn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  color: var(--td-text-color-placeholder);
  background: transparent;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;

  &:hover:not(:disabled) {
    color: var(--td-text-color-primary);
    background: var(--td-bg-color-container-hover);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
  }

  &.is-loading {
    cursor: wait;
  }
}

.chat-header__menu-loading {
  animation: chat-header-spin 0.8s linear infinite;
}

@keyframes chat-header-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>

<style lang="less">
.chat-header-menu-popup {
  z-index: 99 !important;

  .t-popup__content {
    padding: 4px !important;
    margin-top: 2px !important;
    min-width: 168px !important;
    width: max-content !important;
    border-radius: 8px !important;
    background: var(--td-bg-color-container) !important;
    border: 0.5px solid var(--td-component-stroke) !important;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08) !important;
    overflow: hidden;
  }
}

.chat-header-menu {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 160px;
}

.chat-header-confirm {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 236px;
}

.chat-header-confirm__title {
  margin: 0;
  color: var(--td-text-color-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
}

.chat-header-confirm__body {
  color: var(--td-text-color-secondary);
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}

.chat-header-confirm__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 2px;
}

.chat-header-confirm__btn {
  min-width: 60px;
  height: 30px;
  padding: 0 12px;
  border: 0.5px solid var(--td-component-stroke);
  border-radius: 6px;
  color: var(--td-text-color-primary);
  background: var(--td-bg-color-container);
  font-size: 14px;
  line-height: 28px;
  cursor: pointer;

  &:hover:not(:disabled) {
    background: var(--td-bg-color-container-hover);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.55;
  }

  &.is-danger {
    border-color: transparent;
    color: #fff;
    background: var(--td-error-color-6);

    &:hover:not(:disabled) {
      background: var(--td-error-color-5);
    }
  }
}

.chat-header-menu__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 0 12px;
  border: 0;
  border-radius: 5px;
  color: var(--td-text-color-primary);
  background: transparent;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  white-space: nowrap;
  box-sizing: border-box;
  cursor: pointer;

  &:hover {
    background: var(--td-bg-color-container-hover);
  }

  &.is-danger {
    color: var(--td-error-color-6);

    .chat-header-menu__icon {
      color: var(--td-error-color-6);
    }

    &:hover {
      background: var(--td-error-color-1);
    }
  }
}

.chat-header-menu__icon {
  flex: 0 0 auto;
  font-size: 16px;
  color: var(--td-text-color-secondary);
}

.chat-header-menu__divider {
  height: 1px;
  margin: 2px 6px;
  background: var(--td-component-stroke);
}
</style>
