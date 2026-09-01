import request from './request'

/** 模型类型（VLLM 用于聊天图片/附件视觉理解） */
export type ModelType = 'KnowledgeQA' | 'Embedding' | 'Rerank' | 'VLLM' | 'ASR'
export type ModelSource = 'local' | 'remote'

export interface ModelProvider {
  id: string
  name: string
  description: string
  types: ModelType[]
  default_urls: Partial<Record<ModelType, string>>
  requires_auth: boolean
  kind: 'remote' | 'local'
  /** 兼容旧字段：与 id 相同 */
  source: string
  base_url: string | null
}

export interface ModelInfo {
  id: string
  name: string
  display_name: string
  type: ModelType
  source: ModelSource | string
  provider: string
  provider_name?: string
  description: string
  parameters: Record<string, unknown>
  is_default: boolean
  is_builtin: boolean
  status: 'active' | 'disabled'
  credentials: Record<string, boolean>
  created_at: string
  updated_at: string
}

export interface ModelCreatePayload {
  name: string
  display_name?: string
  type: ModelType
  source?: ModelSource
  provider?: string
  description?: string
  model?: string
  base_url?: string
  api_key?: string
  dimensions?: number
  temperature?: number
  supports_vision?: boolean
  is_default?: boolean
}

export interface ModelUpdatePayload {
  display_name?: string
  description?: string
  model?: string
  base_url?: string
  api_key?: string
  provider?: string
  dimensions?: number
  temperature?: number
  supports_vision?: boolean
  is_default?: boolean
  status?: 'active' | 'disabled'
}

export interface DebugResult {
  ok: boolean
  message: string
  latency_ms: number
}

export interface ModelDebugPayload {
  prompt?: string
  image_base64?: string
}

export interface OllamaStatus {
  ok: boolean
  host: string
  version?: string | null
  message: string
}

export interface OllamaModelItem {
  name: string
  size?: number
  modified_at?: string
}

export async function listProviders(type?: ModelType): Promise<ModelProvider[]> {
  const params: Record<string, string> = {}
  if (type) params.type = type
  const resp = await request.get<ModelProvider[]>('/models/providers', { params })
  return resp.data
}

export async function getOllamaStatus(): Promise<OllamaStatus> {
  const resp = await request.get<OllamaStatus>('/models/ollama/status')
  return resp.data
}

export async function listOllamaModels(): Promise<{ ok: boolean; models: OllamaModelItem[]; message: string }> {
  const resp = await request.get<{ ok: boolean; models: OllamaModelItem[]; message: string }>(
    '/models/ollama/models',
  )
  return resp.data
}

export async function listModels(type?: ModelType, source?: string): Promise<ModelInfo[]> {
  const params: Record<string, string> = {}
  if (type) params.type = type
  if (source) params.source = source
  const resp = await request.get<ModelInfo[]>('/models', { params })
  return resp.data
}

export async function getModel(modelId: string): Promise<ModelInfo> {
  const resp = await request.get<ModelInfo>(`/models/${modelId}`)
  return resp.data
}

export async function createModel(payload: ModelCreatePayload): Promise<ModelInfo> {
  const resp = await request.post<ModelInfo>('/models', payload)
  return resp.data
}

export async function updateModel(modelId: string, payload: ModelUpdatePayload): Promise<ModelInfo> {
  const resp = await request.put<ModelInfo>(`/models/${modelId}`, payload)
  return resp.data
}

export async function deleteModel(modelId: string): Promise<{ ok: boolean }> {
  const resp = await request.delete<{ ok: boolean }>(`/models/${modelId}`)
  return resp.data
}

export async function debugModel(modelId: string, payload?: ModelDebugPayload): Promise<DebugResult> {
  const resp = await request.post<DebugResult>(`/models/${modelId}/debug`, payload ?? {})
  return resp.data
}

export async function updateCredentials(modelId: string, apiKey: string): Promise<ModelInfo> {
  const resp = await request.put<ModelInfo>(`/models/${modelId}/credentials`, { api_key: apiKey })
  return resp.data
}

export async function clearCredentialField(modelId: string, field: string): Promise<ModelInfo> {
  const resp = await request.delete<ModelInfo>(`/models/${modelId}/credentials/${field}`)
  return resp.data
}
