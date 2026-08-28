import type { ModelType, ModelInfo } from '@/api/models'

export type ModelSelectorType = ModelType

function supportsVision(m: ModelInfo): boolean {
  return m.parameters?.supports_vision === true
}

/** VLLM 选择器包含 VLLM 类型 + 带 supports_vision 的 KnowledgeQA */
export function filterModelsByType(allModels: ModelInfo[], modelType: ModelSelectorType): ModelInfo[] {
  return allModels.filter((m) => {
    if (m.status === 'disabled') return false
    if (modelType === 'VLLM') {
      return m.type === 'VLLM' || (m.type === 'KnowledgeQA' && supportsVision(m))
    }
    return m.type === modelType
  })
}
