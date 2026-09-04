import request from './request'

export type EvalSuite = 'rag_bench' | 'rag_quality' | 'intent_bench'
export type PipelineProfile = 'rag_fixed' | 'rag_agent' | 'intent'
export type EvalStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

export interface EvalDatasetInfo {
  id: string
  format: string
  description: string
  source?: string | null
  created_at?: string | null
  item_count?: number
  passage_count?: number
  kind?: 'rag' | 'intent' | string
  builtin?: boolean
  online?: boolean
}

export interface EvalDatasetPreview extends EvalDatasetInfo {
  view?: 'contexts' | string
  stats?: {
    item_count: number
    passage_count: number
    noans_count: number
    hasans_count: number
    intent_count: number
  }
  items?: Array<{
    qid: number
    question: string
    answer: string
    is_impossible?: boolean
    intent_gt?: string
  }>
  passages?: Array<{ pid: number; title: string; text: string }>
}

export interface EvalDatasetContextQa {
  id: string
  question: string
  answers: string[]
  answer: string
  is_impossible: boolean
  intent_gt?: string
}

export interface EvalDatasetContextBlock {
  index: number
  article_title: string
  context: string
  qas: EvalDatasetContextQa[]
  question_count: number
  hasans_count: number
  noans_count: number
}

export interface EvalDatasetContextsPage {
  dataset_id: string
  view: 'contexts'
  offset: number
  limit: number
  total_contexts: number
  total_questions: number
  title_filter?: string | null
  stats?: EvalDatasetPreview['stats']
  contexts: EvalDatasetContextBlock[]
}

export interface SquadV2Article {
  title: string
  context_count: number
  question_count: number
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
  eval_kb_id?: number | null
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
  sample_limit?: number
  kb_template_id?: number
  workers?: number
  config_overrides?: Record<string, unknown>
}

export interface DatasetUploadPayload {
  id?: string
  description?: string
  source?: string
  overwrite?: boolean
  title?: string
  paragraphs?: Array<{
    context: string
    qas: Array<{
      question: string
      id?: string
      answers?: Array<{ text: string; answer_start?: number }>
      is_impossible?: boolean
      plausible_answers?: Array<{ text: string; answer_start?: number }>
    }>
  }>
  passages?: Array<{ pid: number; title?: string; text: string }>
  items?: Array<{
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

export async function listEvalTasks(limit = 200, offset = 0): Promise<{ items: EvalTask[]; total: number }> {
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

export async function deleteEvalTask(taskId: string): Promise<{ id: string }> {
  const res = await request.delete<{ success: boolean; data: { id: string } }>(
    `/evaluation/${encodeURIComponent(taskId)}`,
  )
  return unwrap(res)
}

export async function cancelEvalTask(taskId: string): Promise<EvalTask> {
  const res = await request.post<{ success: boolean; data: EvalTask }>(
    `/evaluation/${encodeURIComponent(taskId)}/cancel`,
  )
  return unwrap(res)
}

export async function produceEvalResults(taskId: string): Promise<EvalTask> {
  const res = await request.post<{ success: boolean; data: EvalTask }>(
    `/evaluation/${encodeURIComponent(taskId)}/results`,
  )
  return unwrap(res)
}

export async function listEvalDatasets(): Promise<EvalDatasetInfo[]> {
  const res = await request.get<{ success: boolean; data: EvalDatasetInfo[] }>('/evaluation/datasets')
  return unwrap(res)
}

export async function getEvalDataset(datasetId: string): Promise<EvalDatasetPreview> {
  const res = await request.get<{ success: boolean; data: EvalDatasetPreview }>(
    `/evaluation/datasets/${encodeURIComponent(datasetId)}`,
  )
  return unwrap(res)
}

export async function listEvalDatasetContexts(
  datasetId: string,
  offset = 0,
  limit = 3,
  title?: string | null,
): Promise<EvalDatasetContextsPage> {
  const res = await request.get<{ success: boolean; data: EvalDatasetContextsPage }>(
    `/evaluation/datasets/${encodeURIComponent(datasetId)}/contexts`,
    {
      params: {
        offset: Math.max(0, Number(offset) || 0),
        limit: Math.min(50, Math.max(1, Number(limit) || 3)),
        title: title || undefined,
      },
    },
  )
  return unwrap(res)
}

export async function listSquadV2Articles(): Promise<SquadV2Article[]> {
  const res = await request.get<{ success: boolean; data: SquadV2Article[] }>(
    '/evaluation/datasets/squad_v2/articles',
  )
  return unwrap(res)
}

export async function syncSquadV2Dataset(force = false): Promise<{ id: string; item_count: number; passage_count: number }> {
  const res = await request.post<{ success: boolean; data: { id: string; item_count: number; passage_count: number } }>(
    '/evaluation/datasets/squad_v2/sync',
    null,
    { params: { force } },
  )
  return unwrap(res)
}

export async function uploadEvalDataset(payload: DatasetUploadPayload): Promise<EvalDatasetInfo> {
  const res = await request.post<{ success: boolean; data: EvalDatasetInfo }>('/evaluation/datasets', payload)
  return unwrap(res)
}

export async function patchEvalDataset(
  datasetId: string,
  payload: { description?: string; source?: string },
): Promise<EvalDatasetInfo> {
  const res = await request.patch<{ success: boolean; data: EvalDatasetInfo }>(
    `/evaluation/datasets/${encodeURIComponent(datasetId)}`,
    payload,
  )
  return unwrap(res)
}

export async function deleteEvalDataset(datasetId: string): Promise<{ id: string }> {
  const res = await request.delete<{ success: boolean; data: { id: string } }>(
    `/evaluation/datasets/${encodeURIComponent(datasetId)}`,
  )
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
