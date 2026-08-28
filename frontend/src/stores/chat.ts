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

function sessionKbIds(s: Session | null | undefined): number[] {
  if (s?.kb_ids?.length) return s.kb_ids
  const raw = s?.metadata?.kb_ids
  if (!Array.isArray(raw)) return []
  return raw.filter((v): v is number => typeof v === 'number')
}

export const useChatStore = defineStore('chat', () => {
  const threads = ref<Session[]>([])
  const currentThreadId = ref<string | null>(null)
  const currentKbIds = ref<number[]>([])
  const messagesClearedAt = ref<{ id: string; at: number } | null>(null)

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
    currentKbIds.value = ids
    if (currentThreadId.value) {
      try {
        await updateSession(currentThreadId.value, { kb_ids: ids })
        const t = threads.value.find((x) => sessionId(x) === currentThreadId.value)
        if (t) {
          t.kb_ids = ids
          if (ids.length) t.metadata = { ...t.metadata, kb_ids: ids }
          else if (t.metadata) delete t.metadata.kb_ids
        }
      } catch (e) {
        console.warn('保存知识库选择失败', e)
      }
    }
  }

  async function setKbId(id: number | null | undefined) {
    await setKbIds(id == null ? [] : [id])
  }

  async function createChat(title?: string): Promise<Session> {
    const t = await createSession(title, currentKbIds.value)
    currentThreadId.value = sessionId(t)
    await loadThreads()
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
