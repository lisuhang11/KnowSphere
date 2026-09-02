/** 会话 store：侧边栏会话管理 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  clearSessionMessages,
  createSession,
  deleteSession,
  listSessions,
  pinSession,
  sessionId,
  unpinSession,
  updateSession,
  type Session,
} from '@/api/sessions'

function sortThreads(list: Session[]): Session[] {
  return [...list].sort((a, b) => {
    const ap = a.is_pinned ? 1 : 0
    const bp = b.is_pinned ? 1 : 0
    if (ap !== bp) return bp - ap
    if (a.is_pinned && b.is_pinned) {
      const pinnedCmp = (b.pinned_at || '').localeCompare(a.pinned_at || '')
      if (pinnedCmp !== 0) return pinnedCmp
    }
    return (b.updated_at || b.created_at || '').localeCompare(a.updated_at || a.created_at || '')
  })
}

function sessionTitle(s: Session): string {
  const title = s.title || s.metadata?.title
  return typeof title === 'string' && title.trim() ? title : '新对话'
}

function normalizeKbIds(raw: unknown): number[] {
  if (!Array.isArray(raw)) return []
  const out: number[] = []
  const seen = new Set<number>()
  for (const v of raw) {
    const n = typeof v === 'number' ? v : typeof v === 'string' && /^\d+$/.test(v) ? Number(v) : NaN
    if (!Number.isFinite(n) || seen.has(n)) continue
    seen.add(n)
    out.push(n)
  }
  return out
}

function sessionKbIds(s: Session | null | undefined): number[] {
  // kb_ids 即使是 [] 也是权威值，不能因 length=0 回落到旧 metadata
  if (Array.isArray(s?.kb_ids)) return normalizeKbIds(s.kb_ids)
  return normalizeKbIds(s?.metadata?.kb_ids)
}

function sessionAgentId(s: Session | null | undefined): string | null {
  if (typeof s?.agent_id === 'string' && s.agent_id.trim()) return s.agent_id.trim()
  const meta = s?.metadata?.agent_id
  return typeof meta === 'string' && meta.trim() ? meta.trim() : null
}

const LAST_WEB_SEARCH_KEY = 'knowsphere_web_search_enabled'

function readLastWebSearch(): boolean {
  try {
    const v = localStorage.getItem(LAST_WEB_SEARCH_KEY)
    if (v === '0' || v === 'false') return false
    return true
  } catch {
    return true
  }
}

function writeLastWebSearch(on: boolean) {
  try {
    localStorage.setItem(LAST_WEB_SEARCH_KEY, on ? '1' : '0')
  } catch {
    /* ignore */
  }
}

function sessionWebSearchEnabled(s: Session | null | undefined): boolean | null {
  if (typeof s?.web_search_enabled === 'boolean') return s.web_search_enabled
  const meta = s?.metadata?.web_search_enabled
  if (typeof meta === 'boolean') return meta
  return null
}

function applyKbIdsToThread(t: Session | undefined, ids: number[]) {
  if (!t) return
  t.kb_ids = ids
  if (ids.length) t.metadata = { ...t.metadata, kb_ids: ids }
  else if (t.metadata) {
    const { kb_ids: _drop, ...rest } = t.metadata
    t.metadata = rest
  }
}

function applyAgentIdToThread(t: Session | undefined, agentId: string | null) {
  if (!t) return
  t.agent_id = agentId
  if (agentId) t.metadata = { ...t.metadata, agent_id: agentId }
  else if (t.metadata) {
    const { agent_id: _drop, ...rest } = t.metadata
    t.metadata = rest
  }
}

export const useChatStore = defineStore('chat', () => {
  const threads = ref<Session[]>([])
  const currentThreadId = ref<string | null>(null)
  const currentKbIds = ref<number[]>([])
  const currentAgentId = ref<string | null>(null)
  const currentWebSearchEnabled = ref(readLastWebSearch())
  const messagesClearedAt = ref<{ id: string; at: number } | null>(null)
  /** 防止快速多选时 updateSession 乱序回写覆盖最新选择 */
  let kbSaveSeq = 0
  let agentSaveSeq = 0
  let webSaveSeq = 0

  async function loadThreads() {
    const list = await listSessions()
    threads.value = sortThreads(list)
  }

  function selectThread(id: string | null) {
    currentThreadId.value = id
    const t = id ? threads.value.find((x) => sessionId(x) === id) : null
    currentKbIds.value = sessionKbIds(t)
    const aid = sessionAgentId(t)
    if (aid) currentAgentId.value = aid
    const web = sessionWebSearchEnabled(t)
    if (web !== null) currentWebSearchEnabled.value = web
  }

  async function setKbIds(ids: number[]) {
    const next = normalizeKbIds(ids)
    currentKbIds.value = next
    const tid = currentThreadId.value
    if (!tid) return
    const seq = ++kbSaveSeq
    try {
      await updateSession(tid, { kb_ids: next })
      if (seq !== kbSaveSeq || currentThreadId.value !== tid) return
      applyKbIdsToThread(
        threads.value.find((x) => sessionId(x) === tid),
        next,
      )
    } catch (e) {
      if (seq === kbSaveSeq) console.warn('保存知识库选择失败', e)
    }
  }

  /** 去掉已删除/不可见的知识库 ID，保持选择与列表一致 */
  function pruneKbIds(validIds: Iterable<number>) {
    const allow = new Set(validIds)
    const next = currentKbIds.value.filter((id) => allow.has(id))
    if (next.length === currentKbIds.value.length) return
    void setKbIds(next)
  }

  async function setAgentId(agentId: string | null) {
    const next = agentId?.trim() || null
    currentAgentId.value = next
    const tid = currentThreadId.value
    if (!tid || !next) return
    const seq = ++agentSaveSeq
    try {
      await updateSession(tid, { agent_id: next })
      if (seq !== agentSaveSeq || currentThreadId.value !== tid) return
      applyAgentIdToThread(
        threads.value.find((x) => sessionId(x) === tid),
        next,
      )
    } catch (e) {
      if (seq === agentSaveSeq) console.warn('保存智能体选择失败', e)
    }
  }

  async function setWebSearchEnabled(on: boolean) {
    currentWebSearchEnabled.value = on
    writeLastWebSearch(on)
    const tid = currentThreadId.value
    if (!tid) return
    const seq = ++webSaveSeq
    try {
      await updateSession(tid, { web_search_enabled: on })
      if (seq !== webSaveSeq || currentThreadId.value !== tid) return
      const t = threads.value.find((x) => sessionId(x) === tid)
      if (t) {
        t.web_search_enabled = on
        t.metadata = { ...t.metadata, web_search_enabled: on }
      }
    } catch (e) {
      if (seq === webSaveSeq) console.warn('保存联网开关失败', e)
    }
  }

  async function setKbId(id: number | null | undefined) {
    await setKbIds(id == null ? [] : [id])
  }

  /** 进入空白对话：不调后端。会话在首条消息 / 上传附件时由 createChat 创建。 */
  function startDraftChat() {
    currentThreadId.value = null
  }

  async function createChat(title?: string): Promise<Session> {
    const t = await createSession(
      title,
      currentKbIds.value,
      currentAgentId.value || undefined,
      currentWebSearchEnabled.value,
    )
    currentThreadId.value = sessionId(t)
    await loadThreads()
    const created = threads.value.find((x) => sessionId(x) === currentThreadId.value)
    if (created) {
      currentKbIds.value = sessionKbIds(created)
      const aid = sessionAgentId(created)
      if (aid) currentAgentId.value = aid
      const web = sessionWebSearchEnabled(created)
      if (web !== null) currentWebSearchEnabled.value = web
    }
    return t
  }

  async function removeThread(id: string) {
    await deleteSession(id)
    if (currentThreadId.value === id) currentThreadId.value = null
    await loadThreads()
  }

  async function renameThread(id: string, title: string) {
    const trimmed = title.trim()
    if (!trimmed) return
    await updateSession(id, { title: trimmed })
    const t = threads.value.find((x) => sessionId(x) === id)
    if (t) t.title = trimmed
  }

  async function clearThreadMessages(id: string) {
    await clearSessionMessages(id)
    messagesClearedAt.value = { id, at: Date.now() }
  }

  async function togglePin(id: string) {
    const t = threads.value.find((x) => sessionId(x) === id)
    const next = t?.is_pinned ? await unpinSession(id) : await pinSession(id)
    const idx = threads.value.findIndex((x) => sessionId(x) === id)
    if (idx >= 0) {
      threads.value[idx] = { ...threads.value[idx], ...next }
    }
    threads.value = sortThreads(threads.value)
  }

  function currentSession(): Session | null {
    const id = currentThreadId.value
    if (!id) return null
    return threads.value.find((x) => sessionId(x) === id) ?? null
  }

  return {
    threads,
    currentThreadId,
    currentKbIds,
    currentAgentId,
    currentWebSearchEnabled,
    loadThreads,
    selectThread,
    setKbIds,
    setKbId,
    setAgentId,
    setWebSearchEnabled,
    pruneKbIds,
    startDraftChat,
    createChat,
    removeThread,
    renameThread,
    clearThreadMessages,
    togglePin,
    currentSession,
    messagesClearedAt,
    titleOf: sessionTitle,
  }
})
