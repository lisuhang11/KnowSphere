/**
 * 会话聊天逻辑
 */

import { computed, nextTick, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { streamSessionRun } from '@/api/chatStream'
import { getSessionState, clearSessionMessages, type Citation, type LangMessage } from '@/api/sessions'
import { useChatStore } from '@/stores/chat'
import {
  type ChatImageMeta,
} from '@/utils/chatImages'
import { mergeCitationMeta, uniqueCitationSources } from '@/utils/citationSources'
import { extractText, uid } from '@/utils/text'

export interface ChatAttachmentMeta {
  id: string
  file_name: string
  file_type?: string
  file_size?: number
}

export interface ChatMsg {
  id: string
  role: 'user' | 'assistant'
  content: string
  images?: ChatImageMeta[]
  attachments?: ChatAttachmentMeta[]
  thinking?: string
  thinkingDone?: boolean
  citations?: Citation[]
  sourceDocs?: Citation[]
}

function extractMessageAttachments(m: LangMessage | Record<string, unknown>): ChatAttachmentMeta[] {
  const kwargs =
    ('additional_kwargs' in m ? m.additional_kwargs : undefined) as Record<string, unknown> | undefined
  const raw = kwargs?.ks_attachments
  if (!Array.isArray(raw)) return []
  const out: ChatAttachmentMeta[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const id = typeof row.id === 'string' ? row.id : ''
    if (!id) continue
    const entry: ChatAttachmentMeta = {
      id,
      file_name: typeof row.file_name === 'string' ? row.file_name : '附件',
    }
    if (typeof row.file_type === 'string' && row.file_type.trim()) {
      entry.file_type = row.file_type.trim()
    }
    if (typeof row.file_size === 'number' && row.file_size > 0) {
      entry.file_size = row.file_size
    }
    out.push(entry)
  }
  return out
}

function appendThinking(ai: ChatMsg, chunk: string) {
  const text = chunk.trim()
  if (!text) return
  const prev = (ai.thinking || '').replace(/\n+$/, '')
  ai.thinking = prev ? `${prev}\n\n${text}` : text
  ai.thinkingDone = false
}

function extractMessageImages(m: LangMessage | Record<string, unknown>): ChatImageMeta[] {
  const kwargs =
    ('additional_kwargs' in m ? m.additional_kwargs : undefined) as Record<string, unknown> | undefined
  const raw = kwargs?.ks_images
  if (!Array.isArray(raw)) return []
  const out: ChatImageMeta[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const url = typeof row.url === 'string' ? row.url : ''
    if (!url) continue
    const entry: ChatImageMeta = { url }
    if (typeof row.caption === 'string' && row.caption.trim()) {
      entry.caption = row.caption.trim()
    }
    out.push(entry)
  }
  return out
}

export function useSessionChat() {
  const chatStore = useChatStore()
  const messages = ref<ChatMsg[]>([])
  const streaming = ref(false)
  const streamingMsgId = ref<string | null>(null)
  const activeCitation = ref<{ msgId: string; citation: Citation } | null>(null)
  /** 发消息时懒创建会话：阻止 watch 清空进行中的消息列表 */
  const suppressHistorySync = ref(false)
  const historyLoading = ref(false)

  let abortController: AbortController | null = null
  let atBottom = true
  const userHasScrolledUp = ref(false)

  async function loadHistory(threadId: string) {
    historyLoading.value = true
    try {
      const state = await getSessionState(threadId)
      const list: ChatMsg[] = []
      for (const m of state.values?.messages ?? []) {
        const text = extractText(m.content).trim()
        const images = extractMessageImages(m)
        const attachments = extractMessageAttachments(m)
        const displayText =
          text.split('\n\n[用户上传图片内容]\n')[0]?.split('\n\n[会话附件内容]\n')[0]?.trim() || text
        if (!displayText && !images.length && !attachments.length) continue
        if (m.type === 'human') {
          list.push({
            id: uid(),
            role: 'user',
            content: displayText,
            images: images.length ? images : undefined,
            attachments: attachments.length ? attachments : undefined,
          })
        } else if (m.type === 'ai') {
          list.push({ id: uid(), role: 'assistant', content: displayText })
        }
      }
      messages.value = list
    } catch (e) {
      MessagePlugin.error(`加载会话失败: ${(e as Error).message}`)
    } finally {
      historyLoading.value = false
    }
    atBottom = true
    userHasScrolledUp.value = false
    await scrollToBottom()
  }

  watch(
    () => chatStore.currentThreadId,
    (id, prev) => {
      if (suppressHistorySync.value) return
      if (id === prev) return
      abortController?.abort()
      streaming.value = false
      streamingMsgId.value = null
      activeCitation.value = null
      if (id) void loadHistory(id)
      else {
        messages.value = []
        historyLoading.value = false
      }
    },
    { immediate: true },
  )

  watch(
    () => chatStore.messagesClearedAt,
    (payload) => {
      if (!payload) return
      if (payload.id === chatStore.currentThreadId) {
        abortController?.abort()
        streaming.value = false
        streamingMsgId.value = null
        activeCitation.value = null
        messages.value = []
      }
    },
  )

  const showGlobalTypingIndicator = computed(() => {
    if (!streaming.value) return false
    const last = messages.value[messages.value.length - 1]
    return !last || last.role === 'user'
  })

  async function scrollToBottom(scrollEl?: HTMLElement | null, force = false) {
    await nextTick()
    const el = scrollEl
    if (el && (atBottom || force)) {
      el.scrollTop = el.scrollHeight
      if (force) {
        atBottom = true
        userHasScrolledUp.value = false
      }
    }
  }

  function onScroll(scrollEl: HTMLElement) {
    const gap = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight
    atBottom = gap < 80
    userHasScrolledUp.value = gap > 120
  }

  async function clearMessages() {
    const id = chatStore.currentThreadId
    if (!id) return
    abortController?.abort()
    streaming.value = false
    streamingMsgId.value = null
    activeCitation.value = null
    try {
      await clearSessionMessages(id)
      messages.value = []
      MessagePlugin.success('消息已清空')
    } catch (e) {
      MessagePlugin.error(`清空消息失败: ${(e as Error).message}`)
    }
  }

  async function ensureThreadId(titleSeed?: string): Promise<string> {
    if (chatStore.currentThreadId) return chatStore.currentThreadId
    suppressHistorySync.value = true
    try {
      await chatStore.createChat(titleSeed)
      const id = chatStore.currentThreadId
      if (!id) throw new Error('未获得会话 ID')
      return id
    } finally {
      suppressHistorySync.value = false
    }
  }

  function handleStreamEvent(ai: ChatMsg, event: string, data: Record<string, unknown>) {
    if (event === 'error') {
      const msg = String(data.message || '对话失败')
      MessagePlugin.error(msg)
      if (!ai.content) {
        ai.content = msg
        ai.thinkingDone = true
      }
      return
    }

    if (
      typeof data === 'object' &&
      data !== null &&
      !Array.isArray(data) &&
      typeof data.type === 'string'
    ) {
      const t = data.type
      if (t === 'thinking') {
        const delta = extractText(data.content)
        if (delta) appendThinking(ai, delta)
      } else if (t === 'tool_call') {
        const name = String(data.tool_name || '')
        if (name === 'attachment_parsing') {
          appendThinking(ai, '正在解析附件…')
        }
      } else if (t === 'tool_result') {
        const name = String(data.tool_name || '')
        if (name === 'attachment_parsing') {
          appendThinking(ai, extractText(data.content) || '附件解析完成')
        }
      } else if (t === 'answer') {
        const delta = extractText(data.content)
        if (delta) {
          ai.content += delta
          ai.thinkingDone = true
        }
      } else if (t === 'citation_meta') {
        const incoming = (data.citations as unknown as Citation[]) || []
        ai.citations = mergeCitationMeta(ai.citations, incoming)
        ai.sourceDocs = uniqueCitationSources(ai.citations)
      }
      return
    }

    const chunk = Array.isArray(data) ? (data[0] as Record<string, unknown>) : null
    if (!chunk) return
    const type = typeof chunk.type === 'string' ? chunk.type : ''
    if (type.includes('tool')) return
    const delta = extractText(chunk.content)
    if (delta) ai.content += delta
  }

  async function send(
    text: string,
    scrollEl?: HTMLElement | null,
    chatModelId?: string | null,
    attachmentIds: string[] = [],
    fallbackImageFiles: File[] = [],
    vlmModelId?: string | null,
    attachmentMetas: ChatAttachmentMeta[] = [],
  ) {
    const query = text.trim()
    const hasAttachments = attachmentIds.length > 0
    if ((!query && !hasAttachments && !fallbackImageFiles.length) || streaming.value) return false

    atBottom = true

    let threadId: string
    try {
      threadId = await ensureThreadId((query || '附件').slice(0, 20))
    } catch {
      // axios 拦截器已提示
      return false
    }

    let userImages: ChatImageMeta[] | undefined
    let imagePayload: { data: string }[] | undefined
    if (!hasAttachments && fallbackImageFiles.length) {
      try {
        const { fileToBase64 } = await import('@/utils/fileToBase64')
        const dataUris = await Promise.all(fallbackImageFiles.map((f) => fileToBase64(f)))
        imagePayload = dataUris.map((data) => ({ data }))
        userImages = dataUris.map((url) => ({ url }))
      } catch (e) {
        MessagePlugin.error(`读取图片失败: ${(e as Error).message}`)
        return false
      }
    }

    messages.value.push({
      id: uid(),
      role: 'user',
      content: query || (hasAttachments ? '（附件）' : '（图片）'),
      images: userImages,
      attachments: attachmentMetas.length ? attachmentMetas : undefined,
    })
    await scrollToBottom(scrollEl)

    streaming.value = true
    streamingMsgId.value = null
    abortController = new AbortController()

    let ai: ChatMsg | null = null
    const ensureAi = (): ChatMsg => {
      if (!ai) {
        messages.value.push({ id: uid(), role: 'assistant', content: '' })
        // 必须拿数组里的响应式代理：对 push 前的原对象赋值 Vue 不会重渲染，
        // 表现为「1/5 查询理解」出来后回答一直转圈。
        const created = messages.value[messages.value.length - 1]
        if (!created) throw new Error('无法创建助手消息')
        ai = created
        streamingMsgId.value = created.id
      }
      return ai
    }

    try {
      await streamSessionRun(
        threadId,
        query || '请分析这张图片',
        (event, data) => {
          const target = ensureAi()
          handleStreamEvent(target, event, data)
          void scrollToBottom(scrollEl)
        },
        abortController.signal,
        chatStore.currentKbIds,
        chatModelId,
        imagePayload,
        attachmentIds.length ? attachmentIds : undefined,
        vlmModelId,
      )
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        MessagePlugin.error((e as Error).message)
      }
    } finally {
      streaming.value = false
      streamingMsgId.value = null
      abortController = null
      const finalAi = ai ?? ensureAi()
      if (finalAi.thinking) finalAi.thinkingDone = true
      if (!finalAi.content) finalAi.content = '_（未生成回答，请重试）_'
      void chatStore.loadThreads()
      await scrollToBottom(scrollEl)
    }
    return true
  }

  function stop() {
    abortController?.abort()
  }

  function resetForThreadSwitch() {
    abortController?.abort()
    streaming.value = false
    streamingMsgId.value = null
    activeCitation.value = null
  }

  return {
    messages,
    streaming,
    streamingMsgId,
    activeCitation,
    userHasScrolledUp,
    historyLoading,
    showGlobalTypingIndicator,
    send,
    stop,
    scrollToBottom,
    onScroll,
    clearMessages,
    resetForThreadSwitch,
  }
}
