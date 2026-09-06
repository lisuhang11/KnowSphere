/** 聊天页：知识库/模型选择与附件上传逻辑（对齐 WeKnora InputField 职责拆分）。 */

import { onUnmounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { listKnowledgeBases, type KnowledgeBase } from '@/api/knowledgeBases'
import { listModels, type ModelInfo } from '@/api/models'
import {
  listAgents,
  readLastAgentId,
  selectInitialAgentId,
  writeLastAgentId,
  type AgentInfo,
} from '@/api/agents'
import {
  attachmentPreviewUrl,
  deleteTemporaryAttachment,
  uploadTemporaryAttachment,
  waitTemporaryAttachmentReady,
} from '@/api/temporaryAttachments'
import { useChatStore } from '@/stores/chat'
import {
  AGENT_AUDIO_DISABLED_HINT,
  NO_ASR_AUDIO_UPLOAD_HINT,
  hasUsableAsr,
  isAgentAudioUploadEnabled,
  isChatAudioFile,
} from '@/utils/audio'
import {
  CHAT_IMAGE_MAX_COUNT,
  NO_VLM_IMAGE_UPLOAD_HINT,
  hasUsableVlm,
  isChatImageFile,
  type PendingChatAttachment,
  validateChatAttachmentFile,
} from '@/utils/chatImages'
import {
  readLastChatModelId,
  readLastVlmModelId,
  selectInitialModelId,
  writeLastChatModelId,
  writeLastVlmModelId,
} from '@/utils/modelDefaults'
import { filterModelsByType } from '@/components/modelSelectorFilter'
import { getRuntimeConfig } from '@/api/runtimeConfig'

export function useChatPageResources() {
  const chatStore = useChatStore()
  const kbList = ref<KnowledgeBase[]>([])
  const kbListLoaded = ref(false)
  const allModels = ref<ModelInfo[]>([])
  const agents = ref<AgentInfo[]>([])
  const webSearchAvailable = ref(true)
  const graphAvailable = ref(false)
  const selectedChatModelId = ref('')
  const selectedVlmModelId = ref('')
  const pendingAttachments = ref<PendingChatAttachment[]>([])

  watch(selectedChatModelId, (id) => writeLastChatModelId(id || null))
  watch(selectedVlmModelId, (id) => writeLastVlmModelId(id || null))
  watch(
    () => chatStore.currentAgentId,
    (id) => writeLastAgentId(id || null),
  )

  watch(
    () => [...chatStore.currentKbIds],
    (ids) => {
      // 多选时：按选择顺序找第一个配置了摘要模型的库
      for (const kbId of ids) {
        const kb = kbList.value.find((k) => k.id === kbId)
        const sid = kb?.summary_model_id?.trim()
        if (sid && allModels.value.some((m) => m.id === sid && m.type === 'KnowledgeQA')) {
          selectedChatModelId.value = sid
          return
        }
      }
    },
  )

  watch(
    () => ({ loaded: kbListLoaded.value, ids: kbList.value.map((k) => k.id) }),
    ({ loaded, ids }) => {
      // 列表未加载成功前不要 prune，否则会把会话已选库清空
      if (!loaded) return
      chatStore.pruneKbIds(ids)
    },
  )

  function clearPendingAttachments() {
    for (const item of pendingAttachments.value) {
      if (item.previewUrl.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    }
    pendingAttachments.value = []
  }

  async function removePendingAttachment(item: PendingChatAttachment) {
    if (item.previewUrl.startsWith('blob:')) URL.revokeObjectURL(item.previewUrl)
    pendingAttachments.value = pendingAttachments.value.filter((p) => p.id !== item.id)
    if (item.attachmentId && chatStore.currentThreadId) {
      try {
        await deleteTemporaryAttachment(chatStore.currentThreadId, item.attachmentId)
      } catch {
        /* ignore */
      }
    }
  }

  async function uploadOneAttachment(sessionId: string, pending: PendingChatAttachment) {
    try {
      const res = await uploadTemporaryAttachment(sessionId, pending.file)
      pending.attachmentId = res.data.id
      pending.status = 'processing'
      if (pending.file.type.startsWith('image/')) {
        pending.previewUrl = attachmentPreviewUrl(sessionId, res.data.id)
      }
      const ready = await waitTemporaryAttachmentReady(sessionId, res.data.id)
      pending.status = ready.status === 'uploaded' ? 'processing' : ready.status
      if (ready.status === 'failed') {
        pending.errorMessage = ready.error_message || '解析失败'
        MessagePlugin.warning(`${pending.fileName} 解析失败`)
      }
    } catch (err) {
      pending.status = 'failed'
      pending.errorMessage = (err as Error).message
      MessagePlugin.warning(`${pending.fileName} 上传失败`)
    }
  }

  async function handleAttachmentSelect(e: Event) {
    const inputEl = e.target as HTMLInputElement
    const selected = Array.from(inputEl.files ?? [])
    inputEl.value = ''
    if (!selected.length) return
    const room = CHAT_IMAGE_MAX_COUNT - pendingAttachments.value.length
    if (room <= 0) {
      MessagePlugin.warning(`最多上传 ${CHAT_IMAGE_MAX_COUNT} 个附件`)
      return
    }

    const vlmReady = hasUsableVlm(allModels.value)
    const asrReady = hasUsableAsr(allModels.value)
    const currentAgent = agents.value.find((a) => a.id === chatStore.currentAgentId)
    const audioEnabled = isAgentAudioUploadEnabled(currentAgent)
    const files = selected.slice(0, room)
    const imageFiles = files.filter(isChatImageFile)
    const audioFiles = files.filter(isChatAudioFile)
    const otherFiles = files.filter((f) => !isChatImageFile(f) && !isChatAudioFile(f))
    if (imageFiles.length && !vlmReady) {
      MessagePlugin.warning(NO_VLM_IMAGE_UPLOAD_HINT)
      if (!otherFiles.length && !audioFiles.length) return
    }
    if (audioFiles.length && !asrReady) {
      MessagePlugin.warning(NO_ASR_AUDIO_UPLOAD_HINT)
      if (!otherFiles.length && !(vlmReady && imageFiles.length)) return
    } else if (audioFiles.length && !audioEnabled) {
      MessagePlugin.warning(AGENT_AUDIO_DISABLED_HINT)
      if (!otherFiles.length && !(vlmReady && imageFiles.length)) return
    }

    let threadId = chatStore.currentThreadId
    if (!threadId) {
      try {
        await chatStore.createChat('新对话')
        threadId = chatStore.currentThreadId
      } catch {
        // axios 拦截器已提示
        return
      }
    }
    if (!threadId) return

    const accepted = files.filter((f) => {
      if (isChatImageFile(f) && !vlmReady) return false
      if (isChatAudioFile(f) && (!asrReady || !audioEnabled)) return false
      return true
    })
    for (const file of accepted) {
      const err = validateChatAttachmentFile(file)
      if (err) {
        MessagePlugin.warning(err)
        continue
      }
      const pending: PendingChatAttachment = {
        id: uid(),
        file,
        previewUrl: file.type.startsWith('image/') ? URL.createObjectURL(file) : '',
        status: 'uploading',
        fileName: file.name,
      }
      pendingAttachments.value.push(pending)
      void uploadOneAttachment(threadId, pending)
    }
  }

  function getReadyAttachmentPayload() {
    const ready = pendingAttachments.value.filter((a) => a.attachmentId && a.status !== 'failed')
    const readyIds = ready.map((a) => a.attachmentId!)
    const readyMetas = ready.map((a) => ({
      id: a.attachmentId!,
      file_name: a.fileName,
      file_size: a.file.size,
      file_type: a.fileName.split('.').pop()?.toLowerCase(),
    }))
    const fallbackFiles = pendingAttachments.value
      .filter((a) => !a.attachmentId && a.status === 'failed')
      .map((a) => a.file)
    return { readyIds, fallbackFiles, readyMetas }
  }

  async function loadResources() {
    await chatStore.loadThreads()
    try {
      kbList.value = await listKnowledgeBases()
      kbListLoaded.value = true
      chatStore.pruneKbIds(kbList.value.map((k) => k.id))
    } catch (e) {
      console.warn('加载知识库列表失败', e)
      kbListLoaded.value = false
    }
    try {
      const models = await listModels()
      allModels.value = models
      const last = readLastChatModelId()
      if (last && models.some((m) => m.id === last && m.type === 'KnowledgeQA')) {
        selectedChatModelId.value = last
      } else {
        const def = selectInitialModelId(models, 'KnowledgeQA')
        if (def) selectedChatModelId.value = def
      }
      const lastVlm = readLastVlmModelId()
      const vlmCandidates = filterModelsByType(models, 'VLLM')
      if (lastVlm && vlmCandidates.some((m) => m.id === lastVlm)) {
        selectedVlmModelId.value = lastVlm
      } else {
        const vlmDef =
          vlmCandidates.find((m) => m.is_default)?.id?.trim() ??
          vlmCandidates[0]?.id?.trim() ??
          null
        if (vlmDef) selectedVlmModelId.value = vlmDef
      }
    } catch (e) {
      console.warn('加载模型列表失败', e)
    }
    try {
      const runtime = await getRuntimeConfig()
      webSearchAvailable.value = runtime.web_search_available !== false
      graphAvailable.value = Boolean(runtime.graph_available)
      if (!webSearchAvailable.value && chatStore.currentWebSearchEnabled) {
        chatStore.currentWebSearchEnabled = false
      }
    } catch (e) {
      console.warn('加载运行时配置失败', e)
    }
    try {
      const list = await listAgents()
      agents.value = list
      const current = chatStore.currentAgentId
      if (current && list.some((a) => a.id === current && a.status !== 'disabled')) {
        return
      }
      const last = readLastAgentId()
      if (last && list.some((a) => a.id === last && a.status !== 'disabled')) {
        chatStore.currentAgentId = last
      } else {
        const def = selectInitialAgentId(list)
        if (def) chatStore.currentAgentId = def
      }
    } catch (e) {
      console.warn('加载智能体列表失败', e)
    }
  }

  onUnmounted(clearPendingAttachments)

  return {
    kbList,
    allModels,
    agents,
    webSearchAvailable,
    graphAvailable,
    selectedChatModelId,
    selectedVlmModelId,
    pendingAttachments,
    clearPendingAttachments,
    removePendingAttachment,
    handleAttachmentSelect,
    getReadyAttachmentPayload,
    loadResources,
  }
}
