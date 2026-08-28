import request from './request'

export type EvalSuite = 'rag_bench' | 'rag_quality' | 'intent_bench'
export type PipelineProfile = 'rag_fixed' | 'rag_agent' | 'intent'
export type EvalStatus = 'pending' | 'running' | 'success' | 'failed'

export interface EvalDatasetInfo {
  id: string
  format: string
  description: string
  item_count?: number
  kind?: 'rag' | 'intent' | string
}

export interface EvalTask {
  id: string
  owner: string
  dataset_id: string
  suite: EvalSuite
  pipeline_profile: PipelineProfile
  status: EvalStatus
  config_snapshot: Record<string, unknown>
  metric_summary: Record<string, unknown> | null
  total: number
  finished: number
  err_msg: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface EvalSample {
  qid: number
  question: string
  reference: string | null
  response: string | null
  retrieval_ids: number[] | null
  retrieval_gt: number[] | null
  metrics: Record<string, unknown> | null
  latency_ms: number | null
  error: string | null
}

export interface CreateEvalPayload {
  dataset_id: string
  suite: EvalSuite
  pipeline_profile: PipelineProfile
  corpus_mode?: 'shared' | 'isolated'
  sample_limit?: number
  kb_template_id?: number
  workers?: number
  config_overrides?: Record<string, unknown>
}

export interface DatasetUploadPayload {
  id: string
  corpus_mode?: 'shared' | 'isolated'
  passages?: Array<{ pid: number; title?: string; text: string }>
  items: Array<{
    qid: number
    question: string
    pids?: number[]
    answer?: string
    intent_gt?: string
    meta?: Record<string, unknown>
  }>
}

function unwrap<T>(res: { data: { success?: boolean; data: T } }): T {
  return res.data.data
}

export async function listEvalTasks(limit = 50, offset = 0): Promise<{ items: EvalTask[]; total: number }> {
  const res = await request.get<{ success: boolean; data: { items: EvalTask[]; total: number } }>(
    '/evaluation/tasks',
    { params: { limit, offset } },
  )
  return unwrap(res)
}

export async function getEvalTask(taskId: string): Promise<EvalTask> {
  const res = await request.get<{ success: boolean; data: EvalTask }>('/evaluation', {
    params: { task_id: taskId },
  })
  return unwrap(res)
}

export async function createEvalTask(payload: CreateEvalPayload): Promise<EvalTask> {
  const res = await request.post<{ success: boolean; data: EvalTask }>('/evaluation', payload)
  return unwrap(res)
}

export async function listEvalDatasets(): Promise<EvalDatasetInfo[]> {
  const res = await request.get<{ success: boolean; data: EvalDatasetInfo[] }>('/evaluation/datasets')
  return unwrap(res)
}

export async function uploadEvalDataset(payload: DatasetUploadPayload): Promise<{ id: string }> {
  const res = await request.post<{ success: boolean; data: { id: string } }>('/evaluation/datasets', payload)
  return unwrap(res)
}

export async function listEvalSamples(
  taskId: string,
  limit = 100,
  offset = 0,
): Promise<{ items: EvalSample[]; total: number }> {
  const res = await request.get<{ success: boolean; data: { items: EvalSample[]; total: number } }>(
    `/evaluation/${encodeURIComponent(taskId)}/samples`,
    { params: { limit, offset } },
  )
  return unwrap(res)
}
