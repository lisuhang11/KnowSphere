import request from './request'

export interface RuntimeConfig {
  web_search_available: boolean
  graph_available: boolean
}

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  const resp = await request.get<RuntimeConfig>('/runtime-config')
  return resp.data
}
