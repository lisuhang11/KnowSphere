/**
 * 会话聊天逻辑
 */

import { computed, nextTick, onUnmounted, ref, watch, type Ref } from 'vue'
import { useStickyBottomOnResize } from '@/composables/useStickyBottomOnResize'
import { MessagePlugin } from 'tdesign-vue-next'
import { continueSessionRun, streamSessionRun } from '@/api/chatStream'
import {
  getSessionState,
  clearSessionMessages,
  stopSessionRun,
  type ActiveRun,
  type Citation,
  type LangMessage,
} from '@/api/sessions'
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
  outputs?: ChatAttachmentMeta[]
  thinking?: string
  thinkingDone?: boolean
  citations?: Citation[]
  sourceDocs?: Citation[]
}

function extractKwAttachments(
  m: LangMessage | Record<string, unknown>,
  key: 'ks_attachments' | 'ks_outputs',
): ChatAttachmentMeta[] {
  const kwargs =
    ('additional_kwargs' in m ? m.additional_kwargs : undefined) as Record<string, unknown> | undefined
  const raw = kwargs?.[key]
  if (!Array.isArray(raw)) return []
  const out: ChatAttachmentMeta[] = []
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue
    const row = item as Record<string, unknown>
    const id = typeof row.id === 'string' ? row.id : typeof row.attachment_id === 'string' ? row.attachment_id : ''
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

function extractMessageAttachments(m: LangMessage | Record<string, unknown>): ChatAttachmentMeta[] {
  return extractKwAttachments(m, 'ks_attachments')
}

function extractMessageOutputs(m: LangMessage | Record<string, unknown>): ChatAttachmentMeta[] {
  return extractKwAttachments(m, 'ks_outputs')
}

function pushOutput(ai: ChatMsg, item: ChatAttachmentMeta) {
  if (!item.id) return
  const cur = ai.outputs ? [...ai.outputs] : []
  if (cur.some((x) => x.id === item.id)) return
  cur.push(item)
  ai.outputs = cur
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

export function useSessionChat(scrollContainer?: Ref<HTMLElement | null | undefined>) {
  const chatStore = useChatStore()
  const messages = ref<ChatMsg[]>([])
  const streaming = ref(false)
  const streamingMsgId = ref<string | null>(null)
  const activeCitation = ref<{ msgId: string; citation: Citation } | null>(null)
  /** 发消息时懒创建会话：阻止 watch 清空进行中的消息列表 */
  const suppressHistorySync = ref(false)
  const historyLoading = ref(false)

  let abortController: AbortController | null = null
  let loadGen = 0
  let atBottom = true
  const userHasScrolledUp = ref(false)

  function disconnectStream() {
    abortController?.abort()
    abortController = null
  }

  function parseHistory(raw: LangMessage[]): ChatMsg[] {
    const list: ChatMsg[] = []
    for (const m of raw) {
      const text = extractText(m.content).trim()
      const images = extractMessageImages(m)
      const attachments = extractMessageAttachments(m)
      const outputs = extractMessageOutputs(m)
      const displayText =
        text.split('\n\n[用户上传图片内容]\n')[0]?.split('\n\n[会话附件内容]\n')[0]?.trim() || text
      if (!displayText && !images.length && !attachments.length && !outputs.length) continue
      if (m.type === 'human') {
        list.push({
          id: uid(),
          role: 'user',
          content: displayText,
          images: images.length ? images : undefined,
          attachments: attachments.length ? attachments : undefined,
        })
      } else if (m.type === 'ai') {
        list.push({
          id: uid(),
          role: 'assistant',
          content: displayText,
          outputs: outputs.length ? outputs : undefined,
        })
      }
    }
    return list
  }

  function applyActiveRunTail(list: ChatMsg[], active: ActiveRun): ChatMsg {
    if (list.length && list[list.length - 1]?.role === 'assistant') {
      list.pop()
    }
    if (!list.length || list[list.length - 1]?.role !== 'user') {
      const p = active.user_preview || {}
      const attachments: ChatAttachmentMeta[] = []
      for (const a of p.attachments || []) {
        if (!a.id) continue
        const entry: ChatAttachmentMeta = { id: a.id, file_name: a.file_name || '附件' }
        if (a.file_type) entry.file_type = a.file_type
        if (a.file_size && a.file_size > 0) entry.file_size = a.file_size
        attachments.push(entry)
      }
      const images = (p.images || []).filter((i) => i.url)
      list.push({
        id: uid(),
        role: 'user',
        content:
          (p.content || '').trim() ||
          (attachments.length ? '（附件）' : images.length ? '（图片）' : ''),
        images: images.length ? images : undefined,
        attachments: attachments.length ? attachments : undefined,
      })
    }
    const ai: ChatMsg = { id: uid(), role: 'assistant', content: '' }
    list.push(ai)
    return list[list.length - 1] as ChatMsg
  }

  async function pipeStream(
    start: () => Promise<void>,
    ensureAi: () => ChatMsg,
    scrollEl?: HTMLElement | null,
  ): Promise<'ok' | 'aborted' | 'missing'> {
    streaming.value = true
    abortController = new AbortController()
    let outcome: 'ok' | 'aborted' | 'missing' = 'ok'
    try {
      await start()
    } catch (e) {
      const err = e as Error & { status?: number }
      if (err.name === 'AbortError') outcome = 'aborted'
      else if (err.status === 404 || String(err.message || '').includes('没有可续接')) {
        outcome = 'missing'
      } else {
        MessagePlugin.error(err.message)
      }
    } finally {
      streaming.value = false
      streamingMsgId.value = null
      abortController = null
      if (outcome === 'ok') {
        const ai = ensureAi()
        if (ai.thinking) ai.thinkingDone = true
        if (!ai.content) ai.content = '_（未生成回答，请重试）_'
        void chatStore.loadThreads()
        await scrollToBottom(scrollEl)
      }
    }
    return outcome
  }

  async function attachContinueStream(threadId: string, gen: number, scrollEl?: HTMLElement | null) {
    const last = messages.value[messages.value.length - 1]
    if (!last || last.role !== 'assistant') return
    streamingMsgId.value = last.id
    const outcome = await pipeStream(
      () =>
        continueSessionRun(
          threadId,
          (event, data) => {
            if (gen !== loadGen) return
            handleStreamEvent(last, event, data)
            void scrollToBottom(scrollEl)
          },
          abortController?.signal,
        ),
      () => last,
      scrollEl,
    )
    if (outcome === 'missing' && gen === loadGen && chatStore.currentThreadId === threadId) {
      await loadHistory(threadId)
    }
  }

  async function loadHistory(threadId: string) {
    const gen = ++loadGen
    disconnectStream()
    historyLoading.value = true
    try {
      const state = await getSessionState(threadId)
      if (gen !== loadGen || chatStore.currentThreadId !== threadId) return
      const list = parseHistory(state.values?.messages ?? [])
      const active = state.active_run
      if (active) applyActiveRunTail(list, active)
      messages.value = list
      historyLoading.value = false
      atBottom = true
      userHasScrolledUp.value = false
      await scrollToBottom(undefined, true)
      requestAnimationFrame(() => {
        if (gen !== loadGen) return
        void scrollToBottom(undefined, true)
      })
      if (active && gen === loadGen && chatStore.currentThreadId === threadId) {
        await attachContinueStream(threadId, gen)
      }
    } catch (e) {
      if (gen !== loadGen) return
      MessagePlugin.error(`加载会话失败: ${(e as Error).message}`)
      historyLoading.value = false
    }
  }

  watch(
    () => chatStore.currentThreadId,
    (id, prev) => {
      if (suppressHistorySync.value) return
      if (id === prev) return
      disconnectStream()
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
        disconnectStream()
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
    if (!force && userHasScrolledUp.value) return
    await nextTick()
    const el = scrollEl ?? scrollContainer?.value ?? null
    if (!el) return
    if (!force && userHasScrolledUp.value) return
    el.scrollTop = el.scrollHeight
    if (force) {
      atBottom = true
      userHasScrolledUp.value = false
    }
  }

  useStickyBottomOnResize(scrollContainer ?? ref(null), userHasScrolledUp, () => {
    void scrollToBottom()
  })

  function onScroll(scrollEl: HTMLElement) {
    const gap = scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight
    atBottom = gap < 80
    userHasScrolledUp.value = gap > 120
  }

  async function clearMessages() {
    const id = chatStore.currentThreadId
    if (!id) return
    disconnectStream()
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
        const hint =
          name === 'attachment_parsing'
            ? '正在解析附件…'
            : name === 'doc_retrieval'
              ? '正在检索知识库…'
              : name === 'query_knowledge_graph'
                ? '正在查询知识图谱…'
                : name === 'web_search'
                  ? '正在联网搜索…'
                  : name === 'web_fetch'
                    ? '正在读取网页…'
                    : name === 'write_plan'
                      ? '正在规划步骤…'
                      : name === 'generate_pptx'
                        ? '正在生成幻灯片…'
                        : name
                          ? `正在调用 ${name}…`
                          : ''
        const extra = extractText(data.content)
        if (hint) appendThinking(ai, extra && extra !== hint ? `${hint}\n${extra}` : hint)
      } else if (t === 'tool_result') {
        const name = String(data.tool_name || '')
        const body = extractText(data.content)
        if (name === 'attachment_parsing') {
          appendThinking(ai, body || '附件解析完成')
        } else if (body) {
          appendThinking(ai, body)
        }
      } else if (t === 'answer') {
        const delta = extractText(data.content)
        if (delta) {
          ai.content += delta
          ai.thinkingDone = true
        }
      } else if (t === 'stop') {
        ai.thinkingDone = true
      } else if (t === 'citation_meta') {
        const incoming = (data.citations as unknown as Citation[]) || []
        ai.citations = mergeCitationMeta(ai.citations, incoming)
        ai.sourceDocs = uniqueCitationSources(ai.citations)
      } else if (t === 'file_artifact') {
        const id = typeof data.id === 'string' ? data.id : ''
        if (id) {
          const entry: ChatAttachmentMeta = {
            id,
            file_name: typeof data.file_name === 'string' ? data.file_name : '生成文件',
          }
          if (typeof data.file_type === 'string' && data.file_type.trim()) {
            entry.file_type = data.file_type.trim()
          }
          if (typeof data.file_size === 'number' && data.file_size > 0) {
            entry.file_size = data.file_size
          }
          pushOutput(ai, entry)
        }
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
    agentId?: string | null,
    webSearchEnabled?: boolean,
  ) {
    const query = text.trim()
    const hasAttachments = attachmentIds.length > 0
    if ((!query && !hasAttachments && !fallbackImageFiles.length) || streaming.value) return false

    atBottom = true
    userHasScrolledUp.value = false

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

    await pipeStream(
      () =>
        streamSessionRun(
          threadId,
          query || '请分析这张图片',
          (event, data) => {
            handleStreamEvent(ensureAi(), event, data)
            void scrollToBottom(scrollEl)
          },
          abortController?.signal,
          chatStore.currentKbIds,
          chatModelId,
          imagePayload,
          attachmentIds.length ? attachmentIds : undefined,
          vlmModelId,
          agentId,
          webSearchEnabled,
        ),
      () => ai ?? ensureAi(),
      scrollEl,
    )
    return true
  }

  function stop() {
    const id = chatStore.currentThreadId
    disconnectStream()
    streaming.value = false
    streamingMsgId.value = null
    const last = messages.value[messages.value.length - 1]
    if (last?.role === 'assistant') {
      last.thinkingDone = true
      if (!last.content) last.content = '_（已停止生成）_'
    }
    if (id) {
      void stopSessionRun(id).catch((e) => {
        console.warn('停止生成失败', e)
      })
    }
  }

  function resetForThreadSwitch() {
    disconnectStream()
    streaming.value = false
    streamingMsgId.value = null
    activeCitation.value = null
  }

  onUnmounted(() => {
    disconnectStream()
  })

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
