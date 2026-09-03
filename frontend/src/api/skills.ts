import request from './request'

export interface SkillSpec {
  name: string
  description: string
  file_count?: number
}

export interface SkillFileEntry {
  path: string
}

export interface SkillDetail extends SkillSpec {
  files: string[]
}

export type SkillFileEncoding = 'utf-8' | 'base64' | 'binary'

export interface SkillFileContent {
  path: string
  encoding: SkillFileEncoding
  media_type?: string | null
  content: string | null
  truncated: boolean
}

export async function listSkills(): Promise<SkillSpec[]> {
  const resp = await request.get<SkillSpec[]>('/skills')
  return resp.data
}

export async function getSkill(name: string): Promise<SkillDetail> {
  const resp = await request.get<SkillDetail>(`/skills/${encodeURIComponent(name)}`)
  return resp.data
}

export async function listSkillFiles(name: string): Promise<SkillFileEntry[]> {
  const resp = await request.get<{ files: SkillFileEntry[] }>(
    `/skills/${encodeURIComponent(name)}/files`,
  )
  return resp.data.files || []
}

export async function getSkillFile(name: string, path: string): Promise<SkillFileContent> {
  const resp = await request.get<SkillFileContent>(
    `/skills/${encodeURIComponent(name)}/files/content`,
    { params: { path } },
  )
  return resp.data
}
