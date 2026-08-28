<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  clearCredentialField,
  createModel,
  debugModel,
  updateCredentials,
  type ModelCreatePayload,
  type ModelInfo,
  type ModelType,
  updateModel,
} from '@/api/models'
import { typeLabel, typeDescription, MODEL_MAIN_TYPES } from '@/utils/modelTypes'

const props = defineProps<{
  visible: boolean
  editing: ModelInfo | null
  providers: { source: string; name: string }[]
  defaultType: ModelType
}>()

const emit = defineEmits<{
  'update:visible': [boolean]
  saved: []
}>()

const OTHER_TYPES: ModelType[] = ['ASR']

const form = ref<ModelCreatePayload & { supports_vision?: boolean }>({
  name: '',
  display_name: '',
  type: 'Embedding',
  source: 'siliconflow',
  description: '',
  model: '',
  base_url: '',
  api_key: '',
  dimensions: undefined,
  temperature: undefined,
  is_default: false,
  supports_vision: false,
})
const saving = ref(false)

const isEditing = computed(() => !!props.editing)
const isBuiltin = computed(() => !!props.editing?.is_builtin)
const hasKey = computed(() => !!props.editing?.credentials?.api_key)

watch(
  () => [props.visible, props.editing, props.defaultType] as const,
  ([open, edit, defType]) => {
    if (!open) return
    if (edit) {
      const p = (edit.parameters ?? {}) as Record<string, unknown>
      form.value = {
        name: edit.name,
        display_name: edit.display_name,
        type: edit.type,
        source: edit.source,
        description: edit.description,
        model: (p.model as string) ?? '',
        base_url: (p.base_url as string) ?? '',
        api_key: '',
        dimensions: (p.dimensions as number) ?? undefined,
        temperature: (p.temperature as number) ?? undefined,
        is_default: edit.is_default,
        supports_vision: p.supports_vision === true,
      }
    } else {
      form.value = {
        name: '',
        display_name: '',
        type: defType,
        source: 'siliconflow',
        description: '',
        model: '',
        base_url: 'https://api.siliconflow.cn/v1',
        api_key: '',
        dimensions: undefined,
        temperature: undefined,
        is_default: false,
        supports_vision: defType === 'VLLM',
      }
    }
  },
  { immediate: true },
)

watch(
  () => form.value.type,
  (t) => {
    if (t === 'VLLM') form.value.supports_vision = true
  },
)

function close() {
  emit('update:visible', false)
}

function buildPayload(): ModelCreatePayload {
  const payload: ModelCreatePayload = {
    name: form.value.name.trim(),
    display_name: form.value.display_name?.trim() || undefined,
    type: form.value.type,
    source: form.value.source,
    description: form.value.description?.trim() || undefined,
    model: form.value.model?.trim() || undefined,
    base_url: form.value.base_url?.trim() || undefined,
    is_default: form.value.is_default,
  }
  if (form.value.api_key?.trim()) payload.api_key = form.value.api_key.trim()
  if (form.value.dimensions) payload.dimensions = form.value.dimensions
  if (form.value.temperature !== undefined && form.value.type !== 'Embedding') {
    payload.temperature = form.value.temperature
  }
  if (form.value.type === 'KnowledgeQA' && form.value.supports_vision) {
    payload.supports_vision = true
  }
  if (form.value.type === 'VLLM') {
    payload.supports_vision = true
  }
  return payload
}

function buildUpdatePayload(): Omit<ModelCreatePayload, 'api_key'> {
  const payload = buildPayload()
  const { api_key: _ignored, ...rest } = payload as ModelCreatePayload & { api_key?: string }
  return rest
}

async function save() {
  if (!form.value.name.trim()) {
    MessagePlugin.warning('请输入模型名')
    return
  }
  if (form.value.type === 'Embedding' && !form.value.model?.trim()) {
    MessagePlugin.warning('Embedding 模型请输入实际模型名（如 BAAI/bge-m3）')
    return
  }
  saving.value = true
  try {
    if (props.editing) {
      await updateModel(props.editing.id, buildUpdatePayload())
      if (form.value.api_key?.trim()) {
        await updateCredentials(props.editing.id, form.value.api_key.trim())
      }
      MessagePlugin.success('模型已更新')
    } else {
      await createModel(buildPayload())
      MessagePlugin.success('模型已创建')
    }
    emit('saved')
    close()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  if (!props.editing) return
  MessagePlugin.info(`正在测试「${props.editing.display_name}」...`)
  try {
    const r = await debugModel(props.editing.id)
    if (r.ok) MessagePlugin.success(r.message)
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

async function clearKey() {
  if (!props.editing) return
  try {
    await clearCredentialField(props.editing.id, 'api_key')
    MessagePlugin.success('已清除 API Key')
    emit('saved')
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}
</script>

<template>
  <t-drawer
    :visible="visible"
    :header="isEditing ? `编辑模型：${editing?.display_name}` : '新增模型'"
    size="520px"
    :footer="false"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <t-form label-align="top" class="editor-form">
      <section v-if="!isEditing" class="form-section">
        <h4 class="section-title">模型类型</h4>
        <t-select v-model="form.type" placeholder="选择类型">
          <t-option
            v-for="t in [...MODEL_MAIN_TYPES, ...OTHER_TYPES]"
            :key="t"
            :value="t"
            :label="typeLabel(t)"
          />
        </t-select>
        <p v-if="form.type" class="type-hint">{{ typeDescription(form.type) }}</p>
      </section>

      <t-alert
        v-if="form.type === 'VLLM'"
        theme="info"
        :message="'VLLM 用于聊天图片上传、临时附件 OCR 与 query_understand 多模态理解。请设为默认或在对话页选择。'"
        class="vllm-alert"
      />

      <section class="form-section">
        <h4 class="section-title">连接配置</h4>
        <t-form-item label="Provider">
          <t-select v-model="form.source" :disabled="isBuiltin" placeholder="选择 Provider">
            <t-option
              v-for="p in providers"
              :key="p.source"
              :value="p.source"
              :label="`${p.name}（${p.source}）`"
            />
          </t-select>
        </t-form-item>
        <t-form-item label="模型名（如 Qwen/Qwen3.5-35B-A3B）" required-mark>
          <t-input v-model="form.name" :disabled="isBuiltin" placeholder="模型名" />
        </t-form-item>
        <t-form-item label="展示名称">
          <t-input v-model="form.display_name" placeholder="留空则用模型名" />
        </t-form-item>
        <t-form-item label="实际调用模型名">
          <t-input v-model="form.model" placeholder="留空则用模型名" />
        </t-form-item>
        <t-form-item label="Base URL">
          <t-input v-model="form.base_url" placeholder="如 https://api.siliconflow.cn/v1" />
        </t-form-item>
        <t-form-item label="API Key">
          <t-input
            v-model="form.api_key"
            type="password"
            :placeholder="isEditing ? (hasKey ? '已配置（留空保持不变）' : '未配置') : 'SiliconFlow 需填写'"
          />
        </t-form-item>
        <t-button
          v-if="isEditing && hasKey"
          theme="danger"
          variant="text"
          size="small"
          @click="clearKey"
        >
          清除已保存的 API Key
        </t-button>
      </section>

      <section class="form-section">
        <h4 class="section-title">高级</h4>
        <t-form-item v-if="form.type === 'Embedding'" label="输出维度（留空则创建时实测）">
          <t-input-number v-model="form.dimensions" :min="1" :max="10000" placeholder="如 1024" />
        </t-form-item>
        <t-form-item v-if="form.type === 'KnowledgeQA' || form.type === 'VLLM'" label="Temperature">
          <t-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
        </t-form-item>
        <t-form-item v-if="form.type === 'KnowledgeQA'" label="支持视觉输入（supports_vision）">
          <t-switch v-model="form.supports_vision" />
          <p class="field-hint">开启后该问答模型也可在「视觉模型」选择器中出现。</p>
        </t-form-item>
        <t-form-item label="描述">
          <t-textarea v-model="form.description" :autosize="{ minRows: 2, maxRows: 4 }" placeholder="可选" />
        </t-form-item>
        <t-form-item label="设为该类型默认模型">
          <t-switch v-model="form.is_default" :disabled="!!editing?.is_default" />
        </t-form-item>
      </section>
    </t-form>

    <div class="drawer-footer">
      <t-button v-if="isEditing" variant="outline" @click="testConnection">测试连接</t-button>
      <div class="drawer-footer__right">
        <t-button variant="outline" @click="close">取消</t-button>
        <t-button theme="primary" :loading="saving" @click="save">保存</t-button>
      </div>
    </div>
  </t-drawer>
</template>

<style scoped>
.editor-form {
  padding-bottom: 8px;
}
.form-section {
  margin-bottom: 20px;
}
.section-title {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}
.type-hint,
.field-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.5;
}
.vllm-alert {
  margin-bottom: 16px;
}
.drawer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--td-border-level-1-color);
}
.drawer-footer__right {
  display: flex;
  gap: 8px;
}
</style>
