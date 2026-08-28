import type { ChunkingProcessConfig } from '@/api/documents'
import type { KnowledgeBase } from '@/api/knowledgeBases'
import { CHUNK_DEFAULTS } from '@/constants/chunking'

/** 切块表单状态（KB 配置 / 上传覆盖 / 重新解析共用） */
export interface ChunkingFormState {
  strategy: string
  chunkSize: number
  chunkOverlap: number
  enableParentChild: boolean
  parentChunkSize: number
  childChunkSize: number
}

export function kbToChunkingForm(kb: KnowledgeBase | null | undefined): ChunkingFormState {
  return {
    strategy: kb?.chunk_strategy || 'auto',
    chunkSize: kb?.chunk_size ?? CHUNK_DEFAULTS.chunkSize,
    chunkOverlap: kb?.chunk_overlap ?? CHUNK_DEFAULTS.chunkOverlap,
    enableParentChild: kb?.enable_parent_child ?? CHUNK_DEFAULTS.enableParentChild,
    parentChunkSize: kb?.parent_chunk_size ?? CHUNK_DEFAULTS.parentChunkSize,
    childChunkSize: kb?.child_chunk_size ?? CHUNK_DEFAULTS.childChunkSize,
  }
}

/** 从文档 process_config + KB 默认合并出表单初始值 */
export function docToChunkingForm(
  pc: ChunkingProcessConfig['chunking_config'] | undefined,
  kb: KnowledgeBase | null | undefined,
): ChunkingFormState {
  const base = kbToChunkingForm(kb)
  if (!pc) return base
  return {
    strategy: pc.strategy ?? base.strategy,
    chunkSize: pc.chunk_size ?? base.chunkSize,
    chunkOverlap: pc.chunk_overlap ?? base.chunkOverlap,
    enableParentChild: pc.enable_parent_child ?? base.enableParentChild,
    parentChunkSize: pc.parent_chunk_size ?? base.parentChunkSize,
    childChunkSize: pc.child_chunk_size ?? base.childChunkSize,
  }
}

/** 只含与 KB 默认不同的字段（omitempty 语义） */
export function buildProcessConfig(
  form: ChunkingFormState,
  kb: KnowledgeBase,
): ChunkingProcessConfig | null {
  const cfg: NonNullable<ChunkingProcessConfig['chunking_config']> = {}
  if (form.strategy !== (kb.chunk_strategy || 'auto')) cfg.strategy = form.strategy
  if (form.chunkSize !== kb.chunk_size) cfg.chunk_size = form.chunkSize
  if (form.chunkOverlap !== kb.chunk_overlap) cfg.chunk_overlap = form.chunkOverlap
  if (form.enableParentChild !== (kb.enable_parent_child ?? false)) {
    cfg.enable_parent_child = form.enableParentChild
  }
  if (form.parentChunkSize !== (kb.parent_chunk_size ?? CHUNK_DEFAULTS.parentChunkSize)) {
    cfg.parent_chunk_size = form.parentChunkSize
  }
  if (form.childChunkSize !== (kb.child_chunk_size ?? CHUNK_DEFAULTS.childChunkSize)) {
    cfg.child_chunk_size = form.childChunkSize
  }
  if (Object.keys(cfg).length === 0) return null
  return { chunking_config: cfg }
}

export function validateChunkingForm(form: ChunkingFormState): string | null {
  if (form.enableParentChild) {
    if (form.parentChunkSize < 64 || form.childChunkSize < 64) {
      return '父块/子块大小不能小于 64'
    }
    return null
  }
  if (form.chunkOverlap >= form.chunkSize) {
    return '分块重叠必须小于分块大小'
  }
  return null
}
