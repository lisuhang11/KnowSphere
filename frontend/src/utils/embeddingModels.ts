/** 可选 embedding 模型（与后端 models/dimensions.py 注册表保持一致） */
export interface EmbeddingModelOption {
  label: string
  value: string
}

export const EMBEDDING_MODEL_OPTIONS: EmbeddingModelOption[] = [
  { label: 'BAAI/bge-m3（默认，1024 维）', value: 'BAAI/bge-m3' },
  { label: 'BAAI/bge-large-zh-v1.5（1024 维）', value: 'BAAI/bge-large-zh-v1.5' },
  { label: 'BAAI/bge-large-en-v1.5（1024 维）', value: 'BAAI/bge-large-en-v1.5' },
  { label: 'BAAI/bge-base-zh-v1.5（768 维）', value: 'BAAI/bge-base-zh-v1.5' },
  { label: 'BAAI/bge-base-en-v1.5（768 维）', value: 'BAAI/bge-base-en-v1.5' },
  { label: 'BAAI/bge-small-zh-v1.5（512 维）', value: 'BAAI/bge-small-zh-v1.5' },
  { label: 'BAAI/bge-small-en-v1.5（384 维）', value: 'BAAI/bge-small-en-v1.5' },
  { label: 'Qwen/Qwen3-Embedding-0.6B（1024 维）', value: 'Qwen/Qwen3-Embedding-0.6B' },
  { label: 'Qwen/Qwen3-Embedding-4B（2560 维）', value: 'Qwen/Qwen3-Embedding-4B' },
  { label: 'Pro/BAAI/bge-m3（1024 维）', value: 'Pro/BAAI/bge-m3' },
]

export function embeddingModelLabel(modelId: string): string {
  const hit = EMBEDDING_MODEL_OPTIONS.find((o) => o.value === modelId)
  return hit ? hit.value : modelId
}
