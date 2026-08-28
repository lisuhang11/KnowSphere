/** 聊天页：知识库/模型选择与附件上传逻辑（对齐 WeKnora InputField 职责拆分）。 */

import { onUnmounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { listKnowledgeBases, type KnowledgeBase } from '@/api/knowledgeBases'
import { listModels, type ModelInfo } from '@/api/models'
import {
  attachmentPreviewUrl,
  deleteTemporaryAttachment,
  uploadTemporaryAttachment,
  waitTemporaryAttachmentReady,
} from '@/api/temporaryAttachments'
import { useChatStore } from '@/stores/chat'
import {
  CHAT_IMAGE_MAX_COUNT,
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
import { uid } from '@/utils/text'

export function useChatPageResources() {
  const chatStore = useChatStore()
  const kbList = ref<KnowledgeBase[]>([])
  const allModels = ref<ModelInfo[]>([])
  const selectedChatModelId = ref('')
  const selectedVlmModelId = ref('')
  const pendingAttachments = ref<PendingChatAttachment[]>([])

  watch(selectedChatModelId, (id) => writeLastChatModelId(id || null))
  watch(selectedVlmModelId, (id) => writeLastVlmModelId(id || null))

  watch(
    () => chatStore.currentKbIds[0],
    (kbId) => {
      if (!kbId) return
      const kb = kbList.value.find((k) => k.id === kbId)
      const sid = kb?.summary_model_id?.trim()
      if (sid && allModels.value.some((m) => m.id === sid && m.type === 'KnowledgeQA')) {
        selectedChatModelId.value = sid
      }
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

    let threadId = chatStore.currentThreadId
    if (!threadId) {
      try {
        await chatStore.createChat('新对话')
        threadId = chatStore.currentThreadId
      } catch (err) {
        MessagePlugin.error(`创建会话失败: ${(err as Error).message}`)
        return
      }
    }
    if (!threadId) return

    for (const file of selected.slice(0, room)) {
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
    } catch (e) {
      console.warn('加载知识库列表失败', e)
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
  }

  onUnmounted(clearPendingAttachments)

  return {
    kbList,
    allModels,
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
