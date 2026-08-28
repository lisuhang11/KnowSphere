<script setup lang="ts">
/**
 * 父子分块配置字段。
 * 与单级 chunk_size/overlap 配合使用：开启后子块用于检索、父块用于上下文。
 */
import type { ChunkingFormState } from '@/utils/chunkingConfig'

const form = defineModel<ChunkingFormState>({ required: true })

const props = defineProps<{
  /** 紧凑布局（上传弹窗） */
  compact?: boolean
}>()
</script>

<template>
  <div class="pc-fields" :class="{ compact: props.compact }">
    <div class="pc-toggle-row">
      <div class="pc-toggle-text">
        <div class="pc-toggle-label">父子分块</div>
        <div class="pc-toggle-desc">小子块向量检索，大父块提供上下文</div>
      </div>
      <t-switch v-model="form.enableParentChild" />
    </div>

    <template v-if="form.enableParentChild">
      <div class="pc-size-row">
        <t-form-item label="父块大小（字）" help="存入 DB 供上下文回捞，默认 4096">
          <t-input-number
            v-model="form.parentChunkSize"
            :min="512"
            :max="8192"
            :step="64"
            theme="column"
          />
        </t-form-item>
        <t-form-item label="子块大小（字）" help="唯一被 embedding 的粒度，默认 384">
          <t-input-number
            v-model="form.childChunkSize"
            :min="64"
            :max="2048"
            :step="32"
            theme="column"
          />
        </t-form-item>
      </div>
    </template>

    <template v-else>
      <div class="pc-size-row">
        <t-form-item label="分块大小（字）">
          <t-input-number
            v-model="form.chunkSize"
            :min="64"
            :max="4096"
            :step="50"
            theme="column"
          />
        </t-form-item>
        <t-form-item label="分块重叠（字）" help="需小于分块大小">
          <t-input-number
            v-model="form.chunkOverlap"
            :min="0"
            :max="1024"
            :step="10"
            theme="column"
          />
        </t-form-item>
      </div>
    </template>

    <t-form-item v-if="form.enableParentChild" label="分块重叠（字）" help="父块 overlap；子块固定为子块大小/5">
      <t-input-number
        v-model="form.chunkOverlap"
        :min="0"
        :max="1024"
        :step="10"
        theme="column"
        style="max-width: 200px"
      />
    </t-form-item>
  </div>
</template>

<style scoped>
.pc-fields {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pc-toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--td-component-border);
  border-radius: 6px;
  background: var(--td-bg-color-secondarycontainer);
}

.pc-toggle-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.pc-toggle-desc {
  margin-top: 2px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.5;
}

.pc-size-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.pc-fields.compact .pc-size-row {
  gap: 8px;
}
</style>
