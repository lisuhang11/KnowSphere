<script setup lang="ts">
import { computed, ref } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import type { ChatMsg } from '@/composables/useSessionChat'
import { useChatCitationPopover } from '@/composables/useChatCitationPopover'
import { copyToClipboard } from '@/utils/clipboard'
import ChatCitationFloat from '@/components/ChatCitationFloat.vue'
import DocInfo from './DocInfo.vue'

const props = defineProps<{
  msg: ChatMsg
  streaming: boolean
  streamingMsgId: string | null
  renderedHtml: string
}>()

const contentRef = ref<HTMLElement | null>(null)

const { float, cancelClose, scheduleClose } = useChatCitationPopover(contentRef, {
  getCitations: () => props.msg.citations,
  messageId: () => props.msg.id,
})

const showToolbar = computed(() => {
  if (!props.msg.content) return false
  if (props.streaming && props.streamingMsgId === props.msg.id) return false
  return true
})

const citationList = computed(() => props.msg.sourceDocs || props.msg.citations || [])

async function copyAnswer() {
  const ok = await copyToClipboard(props.msg.content)
  if (ok) MessagePlugin.success('已复制回答')
  else MessagePlugin.error('复制失败')
}
</script>

<template>
  <div class="bot_msg">
    <div class="bot_msg_stack">
      <DocInfo
        v-if="citationList.length"
        :citations="citationList"
        :message-id="msg.id"
      />

      <details v-if="msg.thinking" class="thinking-block" :open="!msg.thinkingDone">
        <summary>
          <span class="thinking-title">{{ msg.thinkingDone ? '思考过程' : '思考中…' }}</span>
        </summary>
        <div class="thinking-content">{{ msg.thinking }}</div>
      </details>

      <div v-if="msg.content" ref="contentRef" class="content-wrapper">
        <div class="markdown-content" v-html="renderedHtml"></div>
      </div>
      <div v-else-if="streaming && streamingMsgId === msg.id" class="typing">
        <span></span><span></span><span></span>
      </div>

      <div v-if="showToolbar" class="answer-toolbar">
        <t-button size="small" variant="outline" shape="round" title="复制回答" @click.stop="copyAnswer">
          <t-icon name="copy" />
        </t-button>
      </div>
    </div>

    <ChatCitationFloat :float="float" :on-enter="cancelClose" :on-leave="scheduleClose" />
  </div>
</template>

<style scoped>
.bot_msg {
  border-radius: 4px;
  color: var(--td-text-color-primary);
  font-size: 16px;
  margin-right: auto;
  max-width: 100%;
  box-sizing: border-box;
}

.bot_msg_stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.content-wrapper {
  padding: 2px 0;
}

.markdown-content {
  font-size: 16px;
  line-height: 1.7;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.markdown-content :deep(p) {
  margin: 0 0 0.75em;
}

.markdown-content :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(pre) {
  margin: 0.75em 0;
  border-radius: 8px;
  overflow-x: auto;
}

.markdown-content :deep(.cite) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 16px;
  padding: 0 5px;
  margin: 0 2px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  font-style: normal;
  line-height: 1;
  vertical-align: super;
  color: var(--td-brand-color);
  background: color-mix(in srgb, var(--td-brand-color) 10%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--td-brand-color) 18%, transparent);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.markdown-content :deep(.cite:hover) {
  background: var(--td-brand-color);
  color: #fff;
}

.markdown-content :deep(.cite.static) {
  cursor: default;
  opacity: 0.7;
}

.thinking-block {
  margin-bottom: 4px;
  border: 1px solid var(--td-border-level-1-color);
  border-radius: 8px;
  background: var(--td-bg-color-secondarycontainer, #f6f6f6);
  overflow: hidden;
}

.thinking-block summary {
  cursor: pointer;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  user-select: none;
  list-style: none;
  display: flex;
  align-items: center;
}

.thinking-block summary::-webkit-details-marker {
  display: none;
}

.thinking-block summary::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 8px;
  border-right: 1.5px solid var(--td-text-color-placeholder);
  border-bottom: 1.5px solid var(--td-text-color-placeholder);
  transform: rotate(-45deg);
  transition: transform 0.15s ease;
}

.thinking-block[open] summary::before {
  transform: rotate(45deg);
}

.thinking-content {
  padding: 4px 12px 10px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--td-text-color-secondary);
  border-top: 1px dashed var(--td-border-level-1-color);
  max-height: 240px;
  overflow-y: auto;
  white-space: pre-wrap;
}

.typing {
  display: inline-flex;
  gap: 4px;
  padding: 8px 0;
}

.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--td-text-color-placeholder);
  animation: typing 1.2s infinite ease-in-out;
}

.typing span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typing {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.5;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
}

.answer-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  opacity: 0;
  transition: opacity 0.15s ease;
}

.bot_msg:hover .answer-toolbar {
  opacity: 1;
}
</style>
