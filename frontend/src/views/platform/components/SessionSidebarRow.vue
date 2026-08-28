<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'

const SESSION_TITLE_MAX_LENGTH = 80

type MenuMode = 'menu' | 'clear' | 'delete'

const props = defineProps<{
  item: { id: string; title: string; is_pinned?: boolean }
  active: boolean
}>()

const emit = defineEmits<{
  navigate: []
  rename: [title: string]
  clear: []
  delete: []
  pin: []
}>()

const menuOpen = ref(false)
const menuMode = ref<MenuMode>('menu')
const titleEditing = ref(false)
const titleDraft = ref('')
const titleInputRef = ref<HTMLInputElement | null>(null)

const menuOverlayClass = computed(() =>
  menuMode.value === 'menu' ? 'session-action-menu-popup' : 'session-action-menu-popup is-confirm',
)

function onMenuVisibleChange(visible: boolean): void {
  if (!visible) menuMode.value = 'menu'
}

function backToMenu(): void {
  menuMode.value = 'menu'
}

function startTitleEdit(): void {
  menuOpen.value = false
  menuMode.value = 'menu'
  titleDraft.value = props.item.title || ''
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

function submitTitleEdit(): void {
  if (!titleEditing.value) return
  const nextTitle = titleDraft.value.trim().slice(0, SESSION_TITLE_MAX_LENGTH)
  const currentTitle = (props.item.title || '').trim()
  titleEditing.value = false
  titleDraft.value = ''
  if (!nextTitle || nextTitle === currentTitle) return
  emit('rename', nextTitle)
}

function handleMenuAction(action: string): void {
  if (action === 'rename') {
    startTitleEdit()
    return
  }
  if (action === 'pin') {
    menuOpen.value = false
    menuMode.value = 'menu'
    emit('pin')
    return
  }
  if (action === 'clear') {
    menuMode.value = 'clear'
    return
  }
  if (action === 'delete') {
    menuMode.value = 'delete'
    return
  }
}

function submitClear(): void {
  menuOpen.value = false
  menuMode.value = 'menu'
  emit('clear')
}

function submitDelete(): void {
  menuOpen.value = false
  menuMode.value = 'menu'
  emit('delete')
}
</script>

<template>
  <div
    class="session-row"
    :class="{ active }"
    @click="!titleEditing && emit('navigate')"
  >
    <form
      v-if="titleEditing"
      class="session-title-edit"
      @submit.prevent="submitTitleEdit"
      @click.stop
    >
      <input
        ref="titleInputRef"
        v-model="titleDraft"
        class="session-title-edit__input"
        :maxlength="SESSION_TITLE_MAX_LENGTH"
        @keydown.esc.prevent="cancelTitleEdit"
        @blur="submitTitleEdit"
      />
    </form>
    <template v-else>
      <t-icon
        :name="item.is_pinned ? 'pin-filled' : 'chat'"
        size="16px"
        class="session-icon"
        :class="{ 'is-pinned': item.is_pinned }"
      />
      <span class="session-title" :title="item.title">{{ item.title }}</span>
    </template>

    <div v-if="!titleEditing" class="session-row-menu-wrap" @click.stop>
      <t-popup
        v-model:visible="menuOpen"
        :overlay-class-name="menuOverlayClass"
        trigger="click"
        destroy-on-close
        placement="bottom-right"
        @visible-change="onMenuVisibleChange"
      >
        <button type="button" class="menu-more-wrap" aria-label="会话操作" @click.stop>
          <t-icon name="ellipsis" class="menu-more" />
        </button>
        <template #content>
          <div class="session-action-menu" @click.stop>
            <template v-if="menuMode === 'menu'">
              <button type="button" class="session-action-menu__item" @click="handleMenuAction('rename')">
                <t-icon class="session-action-menu__icon" name="edit-1" />
                <span>重命名</span>
              </button>
              <button type="button" class="session-action-menu__item" @click="handleMenuAction('pin')">
                <t-icon class="session-action-menu__icon" :name="item.is_pinned ? 'pin-filled' : 'pin'" />
                <span>{{ item.is_pinned ? '取消置顶' : '置顶' }}</span>
              </button>
              <div class="session-action-menu__divider" />
              <button type="button" class="session-action-menu__item" @click="handleMenuAction('clear')">
                <t-icon class="session-action-menu__icon" name="clear" />
                <span>清空消息</span>
              </button>
              <button type="button" class="session-action-menu__item is-danger" @click="handleMenuAction('delete')">
                <t-icon class="session-action-menu__icon" name="delete" />
                <span>删除会话</span>
              </button>
            </template>

            <div v-else class="session-action-confirm">
              <div class="session-action-confirm__title">
                {{ menuMode === 'clear' ? '清空消息？' : '删除会话？' }}
              </div>
              <div class="session-action-confirm__body">
                {{
                  menuMode === 'clear'
                    ? '将删除该会话中的所有消息，会话本身会保留。'
                    : '删除后无法恢复，该会话的所有消息将一并移除。'
                }}
              </div>
              <div class="session-action-confirm__footer">
                <button type="button" class="session-action-confirm__btn" @click="backToMenu">取消</button>
                <button
                  type="button"
                  class="session-action-confirm__btn is-danger"
                  @click="menuMode === 'clear' ? submitClear() : submitDelete()"
                >
                  {{ menuMode === 'clear' ? '清空' : '删除' }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </t-popup>
    </div>
  </div>
</template>

<style scoped>
.session-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--td-text-color-primary);
  transition: background-color 0.2s ease;
}

.session-row:hover {
  background: var(--td-gray-bg-color, #f5f5f5);
}

.session-row.active {
  background: var(--td-brand-color-light);
  color: var(--td-brand-color);
}

.session-row.active .session-icon {
  color: var(--td-brand-color);
}

.session-icon {
  flex-shrink: 0;
  color: var(--td-text-color-placeholder);
}

.session-icon.is-pinned {
  color: var(--td-brand-color);
}

.session-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-row-menu-wrap {
  flex: 0 0 auto;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.session-row:hover .session-row-menu-wrap,
.session-row.active .session-row-menu-wrap {
  opacity: 1;
}

.session-title-edit {
  flex: 1 1 auto;
  min-width: 0;
}

.session-title-edit__input {
  width: 100%;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--td-brand-color);
  border-radius: 5px;
  color: var(--td-text-color-primary);
  background: var(--td-bg-color-container);
  font-size: 13px;
  line-height: 24px;
  outline: none;
  box-shadow: 0 0 0 2px var(--td-brand-color-light);
}

.menu-more-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  color: inherit;
  background: transparent;
  cursor: pointer;
}

.menu-more-wrap:hover {
  background: var(--td-bg-color-container-hover);
}
</style>

<style lang="less">
.session-action-menu-popup {
  z-index: 3000 !important;

  .t-popup__content {
    padding: 4px !important;
    margin-top: 2px !important;
    min-width: 160px !important;
    width: max-content !important;
    border-radius: 8px !important;
    background: var(--td-bg-color-container) !important;
    border: 0.5px solid var(--td-component-stroke) !important;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08) !important;
    overflow: hidden;
  }

  &.is-confirm .t-popup__content {
    padding: 12px !important;
    width: 260px !important;
    min-width: 260px !important;
  }
}

.session-action-menu {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 152px;
}

.session-action-menu__item {
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
  cursor: pointer;

  &:hover {
    background: var(--td-bg-color-container-hover);
  }

  &.is-danger {
    color: var(--td-error-color-6);

    .session-action-menu__icon {
      color: var(--td-error-color-6);
    }

    &:hover {
      background: var(--td-error-color-1);
    }
  }
}

.session-action-menu__icon {
  flex: 0 0 auto;
  font-size: 16px;
  color: var(--td-text-color-secondary);
}

.session-action-menu__divider {
  height: 1px;
  margin: 2px 6px;
  background: var(--td-component-stroke);
}

.session-action-confirm {
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 236px;
}

.session-action-confirm__title {
  margin: 0;
  color: var(--td-text-color-primary);
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
}

.session-action-confirm__body {
  color: var(--td-text-color-secondary);
  font-size: 14px;
  line-height: 1.5;
}

.session-action-confirm__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.session-action-confirm__btn {
  min-width: 60px;
  height: 30px;
  padding: 0 12px;
  border: 0.5px solid var(--td-component-stroke);
  border-radius: 6px;
  color: var(--td-text-color-primary);
  background: var(--td-bg-color-container);
  font-size: 14px;
  cursor: pointer;

  &:hover {
    background: var(--td-bg-color-container-hover);
  }

  &.is-danger {
    border-color: transparent;
    color: #fff;
    background: var(--td-error-color-6);
  }
}
</style>
