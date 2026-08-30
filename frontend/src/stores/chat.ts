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

function applyKbIdsToThread(t: Session | undefined, ids: number[]) {
  if (!t) return
  t.kb_ids = ids
  if (ids.length) t.metadata = { ...t.metadata, kb_ids: ids }
  else if (t.metadata) {
    const { kb_ids: _drop, ...rest } = t.metadata
    t.metadata = rest
  }
}

export const useChatStore = defineStore('chat', () => {
  const threads = ref<Session[]>([])
  const currentThreadId = ref<string | null>(null)
  const currentKbIds = ref<number[]>([])
  const messagesClearedAt = ref<{ id: string; at: number } | null>(null)
  /** 防止快速多选时 updateSession 乱序回写覆盖最新选择 */
  let kbSaveSeq = 0

  async function loadThreads() {
    const list = await listSessions()
    threads.value = sortThreads(list)
  }

  function selectThread(id: string | null) {
    currentThreadId.value = id
    const t = id ? threads.value.find((x) => sessionId(x) === id) : null
    currentKbIds.value = sessionKbIds(t)
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

  async function setKbId(id: number | null | undefined) {
    await setKbIds(id == null ? [] : [id])
  }

  async function createChat(title?: string): Promise<Session> {
    const t = await createSession(title, currentKbIds.value)
    currentThreadId.value = sessionId(t)
    await loadThreads()
    // loadThreads 后重新对齐当前会话的 kb（create 已带上 currentKbIds）
    const created = threads.value.find((x) => sessionId(x) === currentThreadId.value)
    if (created) currentKbIds.value = sessionKbIds(created)
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
    loadThreads,
    selectThread,
    setKbIds,
    setKbId,
    pruneKbIds,
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
