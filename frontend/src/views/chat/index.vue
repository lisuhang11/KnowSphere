<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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
const route = useRoute()
const router = useRouter()
const scrollRef = ref<HTMLElement>()

function routeSessionId(): string | null {
  const raw = route.params.sessionId
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
}

watch(
  () => [route.name, route.params.sessionId, chatStore.threads.length] as const,
  () => {
    if (route.name !== 'chat' && route.name !== 'chat-session') return
    const id = routeSessionId()
    if (id) chatStore.selectThread(id)
    else chatStore.startDraftChat()
  },
  { immediate: true },
)

watch(
  () => chatStore.currentThreadId,
  (id) => {
    if (route.name !== 'chat' && route.name !== 'chat-session') return
    if (id === routeSessionId()) return
    if (id) void router.replace({ name: 'chat-session', params: { sessionId: id } })
    else void router.replace({ name: 'chat' })
  },
)

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
} = useSessionChat(scrollRef)

const {
  kbList,
  allModels,
  agents,
  webSearchAvailable,
  graphAvailable,
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
const selectedSkillNames = ref<string[]>([])
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

const selectedAgentId = computed<string>({
  get: () => chatStore.currentAgentId || '',
  set: (v) => {
    void chatStore.setAgentId(v || null)
  },
})

const webSearchEnabled = computed<boolean>({
  get: () => chatStore.currentWebSearchEnabled,
  set: (v) => {
    void chatStore.setWebSearchEnabled(v)
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

function onSessionDeleted() {
  chatStore.startDraftChat()
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
    chatStore.currentAgentId,
    chatStore.currentWebSearchEnabled,
    selectedSkillNames.value,
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
      :session="currentSession"
      :messages="messages"
      :has-references-panel="hasReferencesPanel"
      @cleared="onHeaderCleared"
      @deleted="onSessionDeleted"
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
            :skills="msg.skills"
          />
          <BotMsg
            v-else
            :msg="msg"
            :session-id="chatStore.currentThreadId"
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
        :agents="agents"
        v-model:selected-kb-ids="selectedKbIds"
        v-model:selected-chat-model-id="selectedChatModelId"
        v-model:selected-vlm-model-id="selectedVlmModelId"
        v-model:selected-agent-id="selectedAgentId"
        v-model:selected-skill-names="selectedSkillNames"
        v-model:web-search-enabled="webSearchEnabled"
        :web-search-available="webSearchAvailable"
        :graph-available="graphAvailable"
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
  font-size: 20px;
  padding: 0 0 20px 20px;
  box-sizing: border-box;
  flex: 1;
  height: 100%;
  min-height: 0;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 100%;
  min-width: 400px;
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
  scrollbar-width: auto;
  scrollbar-color: auto;
}

.msg-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 960px;
  flex: 1;
  margin: 0 auto;
  width: 100%;
  padding: 16px 16px 24px;
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
  min-height: 115px;
  flex-shrink: 0;
  margin: 0 auto;
  width: 100%;
  max-width: 960px;
  box-sizing: border-box;
  position: relative;
  padding: 0 16px 0;
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
