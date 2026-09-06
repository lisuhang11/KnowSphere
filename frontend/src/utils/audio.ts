import type { AgentInfo } from '@/api/agents'

export const AUDIO_EXTS = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.aac'] as const

export const CHAT_AUDIO_ACCEPT =
  '.mp3,.wav,.m4a,.flac,.ogg,.aac,audio/mpeg,audio/wav,audio/mp4,audio/flac,audio/ogg,audio/aac,audio/*'

export const KB_AUDIO_ACCEPT = '.mp3,.wav,.m4a,.flac,.ogg,.aac'

export const KB_FILE_ACCEPT_BASE =
  '.pdf,.docx,.doc,.pptx,.xlsx,.md,.txt,.html,.htm,.jpg,.jpeg,.png,.gif,.bmp,.tiff,.webp'

export const NO_ASR_AUDIO_UPLOAD_HINT =
  '未配置语音识别（ASR）模型，无法上传音频。请先在模型管理中添加。'

export const AGENT_AUDIO_DISABLED_HINT = '当前智能体未开启音频上传'

export const KB_ASR_REQUIRED_HINT = '上传音频文件需要先开启知识库 ASR 并选择语音识别模型'

export function kbUploadAccept(asrEnabled: boolean): string {
  return asrEnabled ? `${KB_FILE_ACCEPT_BASE},${KB_AUDIO_ACCEPT}` : KB_FILE_ACCEPT_BASE
}

export function hasUsableAsr(models: { type: string; status?: string }[]): boolean {
  return models.some((m) => m.type === 'ASR' && m.status !== 'disabled')
}

export function isChatAudioFile(file: File): boolean {
  if ((file.type || '').toLowerCase().startsWith('audio/')) return true
  const name = file.name.toLowerCase()
  return AUDIO_EXTS.some((ext) => name.endsWith(ext))
}

export function isAgentAudioUploadEnabled(agent: AgentInfo | null | undefined): boolean {
  if (!agent) return true
  return Boolean(agent.audio_upload_enabled)
}
