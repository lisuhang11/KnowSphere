import request from './request'

export type ToolCategory = 'planning' | 'knowledge' | 'web' | 'creation'

export interface ToolSpec {
  name: string
  display_name: string
  description: string
  category: ToolCategory | string
  category_label: string
  requires_kb: boolean
  requires_web: boolean
  requires_graph?: boolean
  produces?: 'text' | 'citations' | 'file' | string
}

export interface AgentInfo {
  id: string
  name: string
  description: string
  system_prompt: string
  tool_names: string[]
  tools: ToolSpec[]
  max_iterations: number
  is_builtin: boolean
  is_default: boolean
  status: 'active' | 'disabled' | string
  created_at?: string | null
  updated_at?: string | null
}

export interface AgentPayload {
  name: string
  description?: string
  system_prompt?: string
  tool_names?: string[]
  max_iterations?: number
  is_default?: boolean
  status?: string
}

export const LAST_AGENT_KEY = 'knowsphere_last_agent_id'
export const BUILTIN_AGENT_ID = 'agent-smart-reasoning'

export async function listTools(): Promise<ToolSpec[]> {
  const resp = await request.get<ToolSpec[]>('/tools')
  return resp.data
}

export async function listAgents(): Promise<AgentInfo[]> {
  const resp = await request.get<AgentInfo[]>('/agents')
  return resp.data
}

export async function createAgent(payload: AgentPayload): Promise<AgentInfo> {
  const resp = await request.post<AgentInfo>('/agents', payload)
  return resp.data
}

export async function updateAgent(id: string, payload: AgentPayload): Promise<AgentInfo> {
  const resp = await request.put<AgentInfo>(`/agents/${id}`, payload)
  return resp.data
}

export async function deleteAgent(id: string): Promise<void> {
  await request.delete(`/agents/${id}`)
}

export function readLastAgentId(): string | null {
  try {
    return localStorage.getItem(LAST_AGENT_KEY)?.trim() || null
  } catch {
    return null
  }
}

export function writeLastAgentId(id: string | null) {
  try {
    if (id?.trim()) localStorage.setItem(LAST_AGENT_KEY, id.trim())
    else localStorage.removeItem(LAST_AGENT_KEY)
  } catch {
    /* ignore */
  }
}

export function selectInitialAgentId(agents: AgentInfo[]): string | null {
  const active = agents.filter((a) => a.id && a.status !== 'disabled')
  return active.find((a) => a.is_default)?.id ?? active[0]?.id ?? null
}
