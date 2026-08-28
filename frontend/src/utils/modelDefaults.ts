import type { ModelInfo } from '@/api/models'

export interface ModelDefaultCandidate {
  id?: string
  type: string
  status?: string
  is_default?: boolean
}

/** 创建时默认模型：优先 is_default，否则第一个 active 模型 */
export function selectInitialModelId(
  models: readonly ModelDefaultCandidate[],
  modelType: string,
): string | null {
  const active = models.filter(
    (m) =>
      Boolean(m.id?.trim()) &&
      m.type === modelType &&
      (!m.status || m.status === 'active'),
  )
  return active.find((m) => m.is_default)?.id?.trim() ?? active[0]?.id?.trim() ?? null
}

export function modelDisplayName(m: Pick<ModelInfo, 'display_name' | 'name'>): string {
  const d = m.display_name?.trim()
  return d || m.name
}

export const LAST_CHAT_MODEL_KEY = 'knowsphere_last_chat_model_id'
export const LAST_VLM_MODEL_KEY = 'knowsphere_last_vlm_model_id'

export function readLastChatModelId(): string | null {
  try {
    const v = localStorage.getItem(LAST_CHAT_MODEL_KEY)
    return v?.trim() || null
  } catch {
    return null
  }
}

export function writeLastChatModelId(id: string | null) {
  try {
    if (id?.trim()) localStorage.setItem(LAST_CHAT_MODEL_KEY, id.trim())
    else localStorage.removeItem(LAST_CHAT_MODEL_KEY)
  } catch {
    /* ignore */
  }
}

export function readLastVlmModelId(): string | null {
  try {
    const v = localStorage.getItem(LAST_VLM_MODEL_KEY)
    return v?.trim() || null
  } catch {
    return null
  }
}

export function writeLastVlmModelId(id: string | null) {
  try {
    if (id?.trim()) localStorage.setItem(LAST_VLM_MODEL_KEY, id.trim())
    else localStorage.removeItem(LAST_VLM_MODEL_KEY)
  } catch {
    /* ignore */
  }
}
