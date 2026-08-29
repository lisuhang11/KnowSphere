<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useChatStore } from '@/stores/chat'
import { useSessionChat, type ChatMsg } from '@/composables/useSessionChat'
import { useChatPageResources } from '@/composables/useChatPageResources'
import { provideChatReferencesDrawer } from '@/composables/useChatReferencesDrawer'
import { renderHistoryContent, renderMarkdown } from '@/utils/markdown'
import ChatReferencesDrawer from '@/components/ChatReferencesDrawer.vue'
import ChatAttachmentPreviewDrawer from '@/components/ChatAttachmentPreviewDrawer.vue'
import { provideChatAttachmentPreviewDrawer } from '@/composables/useChatAttachmentPreviewDrawer'
import ChatEmptyState from './components/ChatEmptyState.vue'
import ChatHeader from './components/ChatHeader.vue'
import ChatScrollToBottom from './components/ChatScrollToBottom.vue'
import UserMsg from './components/UserMsg.vue'
import BotMsg from './components/BotMsg.vue'
import ChatInputField from './components/ChatInputField.vue'

provideChatAttachmentPreviewDrawer()
const referencesDrawer = provideChatReferencesDrawer()

const chatStore = useChatStore()
const {
  messages,
  streaming,
  streamingMsgId,
  userHasScrolledUp,
  historyLoading,
  showGlobalTypingIndicator,
  send,
  stop,
  scrollToBottom,
  onScroll,
  clearMessages,
} = useSessionChat()

const {
  kbList,
  allModels,
  selectedChatModelId,
  selectedVlmModelId,
  pendingAttachments,
  clearPendingAttachments,
  removePendingAttachment,
  handleAttachmentSelect,
  getReadyAttachmentPayload,
  loadResources,
} = useChatPageResources()

const input = ref('')
const scrollRef = ref<HTMLElement>()
const inputFieldRef = ref<InstanceType<typeof ChatInputField>>()

const currentSession = computed(() => chatStore.currentSession())
const hasReferencesPanel = computed(() => referencesDrawer.visible.value)

const canSend = computed(() => {
  if (streaming.value) return false
  const hasText = input.value.trim().length > 0
  const attachments = pendingAttachments.value
  const hasReadyAttachment = attachments.some((a) => a.attachmentId && a.status !== 'failed')
  if (!hasText && !hasReadyAttachment) return false
  if (attachments.some((a) => a.status === 'uploading')) return false
  return true
})

const selectedKbIds = computed<number[]>({
  get: () => chatStore.currentKbIds,
  set: (v) => {
    void chatStore.setKbIds(v)
  },
})

function renderMsgContent(msg: ChatMsg): string {
  if (!msg.citations?.length && /\[\[c\d{1,3}\]\]/.test(msg.content)) {
    return renderHistoryContent(msg.content)
  }
  return renderMarkdown(msg.content)
}

function handleScroll() {
  const el = scrollRef.value
  if (el) onScroll(el)
}

function scrollToBottomForce() {
  void scrollToBottom(scrollRef.value, true)
}

async function onHeaderCleared() {
  referencesDrawer.close()
  await clearMessages()
}

async function doSend(textOverride?: string) {
  const text = (textOverride ?? input.value).trim()
  const { readyIds, fallbackFiles, readyMetas } = getReadyAttachmentPayload()
  if ((!text && !readyIds.length && !fallbackFiles.length) || streaming.value) return
  input.value = ''
  clearPendingAttachments()
  await nextTick(() => inputFieldRef.value?.autoResize())
  const ok = await send(
    text,
    scrollRef.value,
    selectedChatModelId.value || null,
    readyIds,
    fallbackFiles,
    selectedVlmModelId.value || null,
    readyMetas,
  )
  if (!ok) return
}

function onSuggestedQuestion(question: string) {
  input.value = question
  void doSend(question)
}

onMounted(() => {
  void loadResources()
})
</script>

<template>
  <div class="chat" :class="{ 'has-references-panel': hasReferencesPanel }">
    <ChatHeader
      v-if="currentSession"
      :session="currentSession"
      :messages="messages"
      @cleared="onHeaderCleared"
    />

    <div ref="scrollRef" class="chat_scroll_box" @scroll="handleScroll">
      <div class="msg-list">
        <div v-if="historyLoading && messages.length === 0" class="msg-skeleton-list">
          <div class="msg-skeleton-user">
            <t-skeleton theme="paragraph" animation="gradient" :loading="true" :row-col="[{ width: '42%' }]" />
          </div>
          <div class="msg-skeleton-bot">
            <t-skeleton theme="paragraph" animation="gradient" :loading="true" :row-col="[{ width: '68%' }, { width: '52%' }]" />
          </div>
          <div class="msg-skeleton-user">
            <t-skeleton theme="paragraph" animation="gradient" :loading="true" :row-col="[{ width: '36%' }]" />
          </div>
        </div>

        <ChatEmptyState
          v-else-if="messages.length === 0 && !streaming"
          @select="onSuggestedQuestion"
        />

        <div v-for="msg in messages" :key="msg.id" class="msg-row" :class="msg.role">
          <UserMsg
            v-if="msg.role === 'user'"
            :session-id="chatStore.currentThreadId"
            :content="msg.content"
            :images="msg.images"
            :attachments="msg.attachments"
          />
          <BotMsg
            v-else
            :msg="msg"
            :streaming="streaming"
            :streaming-msg-id="streamingMsgId"
            :rendered-html="renderMsgContent(msg)"
          />
        </div>

        <div v-if="showGlobalTypingIndicator" class="chat-global-wait" role="status" aria-live="polite">
          <span class="chat-global-wait__spinner" aria-hidden="true"></span>
        </div>
      </div>
    </div>

    <Transition name="scroll-btn-fade">
      <ChatScrollToBottom v-if="userHasScrolledUp" @click="scrollToBottomForce" />
    </Transition>

    <div class="input-container">
      <ChatInputField
        ref="inputFieldRef"
        v-model:input="input"
        :streaming="streaming"
        :can-send="canSend"
        :kb-list="kbList"
        :all-models="allModels"
        v-model:selected-kb-ids="selectedKbIds"
        v-model:selected-chat-model-id="selectedChatModelId"
        v-model:selected-vlm-model-id="selectedVlmModelId"
        :pending-attachments="pendingAttachments"
        @send="doSend()"
        @stop="stop"
        @attachment-select="handleAttachmentSelect"
        @remove-attachment="removePendingAttachment"
      />
    </div>

    <ChatReferencesDrawer />
    <ChatAttachmentPreviewDrawer />
  </div>
</template>

<style scoped>
.chat {
  height: 100%;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: var(--td-bg-color-page);
  box-sizing: border-box;
}

@media (min-width: 960px) {
  .chat {
    transition: padding-right 0.3s cubic-bezier(0.22, 0.61, 0.36, 1);
  }

  .chat.has-references-panel {
    padding-right: 420px;
  }
}

.chat_scroll_box {
  flex: 1;
  min-height: 0;
  width: 100%;
  padding-top: 8px;
  box-sizing: border-box;
  overflow-y: auto;
}

.msg-list {
  max-width: 960px;
  margin: 0 auto;
  padding: 16px 16px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.msg-row.user {
  display: flex;
  justify-content: flex-end;
}

.msg-row.assistant {
  display: flex;
}

.msg-skeleton-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0;
}

.msg-skeleton-user {
  display: flex;
  justify-content: flex-end;
}

.msg-skeleton-bot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 4px;
}

.chat-global-wait {
  display: flex;
  align-items: center;
  min-height: 28px;
  padding-left: 4px;
}

.chat-global-wait__spinner {
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 1.5px solid var(--td-component-stroke);
  border-top-color: var(--td-text-color-secondary);
  border-radius: 50%;
  animation: chatGlobalWaitSpin 0.8s linear infinite;
}

@keyframes chatGlobalWaitSpin {
  to {
    transform: rotate(360deg);
  }
}

.input-container {
  flex-shrink: 0;
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: 0 16px 16px;
  box-sizing: border-box;
  min-height: 115px;
  position: relative;
}

.scroll-btn-fade-enter-active,
.scroll-btn-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.scroll-btn-fade-enter-from,
.scroll-btn-fade-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}

@media (prefers-reduced-motion: reduce) {
  .chat-global-wait__spinner {
    animation: none;
  }
}
</style>
