<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  clearCredentialField,
  createModel,
  debugModel,
  getOllamaStatus,
  listOllamaModels,
  updateCredentials,
  type ModelCreatePayload,
  type ModelInfo,
  type ModelProvider,
  type ModelSource,
  type ModelType,
  type OllamaModelItem,
  updateModel,
} from '@/api/models'
import { typeLabel, typeDescription, MODEL_MAIN_TYPES } from '@/utils/modelTypes'

const props = defineProps<{
  visible: boolean
  editing: ModelInfo | null
  providers: ModelProvider[]
  defaultType: ModelType
}>()

const emit = defineEmits<{
  'update:visible': [boolean]
  saved: []
}>()

const OTHER_TYPES: ModelType[] = ['ASR']
const OLLAMA_DEFAULT_URL = 'http://127.0.0.1:11434/v1'

const form = ref<ModelCreatePayload & { supports_vision?: boolean }>({
  name: '',
  display_name: '',
  type: 'Embedding',
  source: 'remote',
  provider: 'siliconflow',
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
const ollamaOk = ref<boolean | null>(null)
const ollamaMessage = ref('')
const ollamaModels = ref<OllamaModelItem[]>([])
const loadingOllama = ref(false)

const isEditing = computed(() => !!props.editing)
const isBuiltin = computed(() => !!props.editing?.is_builtin)
const hasKey = computed(() => !!props.editing?.credentials?.api_key)
const isLocal = computed(() => form.value.source === 'local')
const localDisabled = computed(() => form.value.type === 'Rerank')

const filteredProviders = computed(() =>
  props.providers.filter((p) => p.types?.includes(form.value.type)),
)

const selectedProvider = computed(() =>
  filteredProviders.value.find((p) => p.id === form.value.provider || p.source === form.value.provider),
)

function defaultUrlFor(providerId: string | undefined, type: ModelType): string {
  const p = props.providers.find((x) => x.id === providerId || x.source === providerId)
  return p?.default_urls?.[type] || p?.base_url || ''
}

function emptyForm(type: ModelType): ModelCreatePayload & { supports_vision?: boolean } {
  const provider = 'siliconflow'
  return {
    name: '',
    display_name: '',
    type,
    source: 'remote',
    provider,
    description: '',
    model: '',
    base_url: defaultUrlFor(provider, type) || 'https://api.siliconflow.cn/v1',
    api_key: '',
    dimensions: undefined,
    temperature: undefined,
    is_default: false,
    supports_vision: type === 'VLLM',
  }
}

watch(
  () => [props.visible, props.editing, props.defaultType] as const,
  ([open, edit, defType]) => {
    if (!open) return
    if (edit) {
      const p = (edit.parameters ?? {}) as Record<string, unknown>
      const source: ModelSource = edit.source === 'local' ? 'local' : 'remote'
      form.value = {
        name: edit.name,
        display_name: edit.display_name,
        type: edit.type,
        source,
        provider: edit.provider || (p.provider as string) || 'siliconflow',
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
      form.value = emptyForm(defType)
    }
    if (form.value.source === 'local') void refreshOllama()
  },
  { immediate: true },
)

watch(
  () => form.value.type,
  (t, prev) => {
    if (t === 'VLLM') form.value.supports_vision = true
    if (t === 'Rerank' && form.value.source === 'local') {
      form.value.source = 'remote'
    }
    const stillOk = filteredProviders.value.some(
      (p) => p.id === form.value.provider || p.source === form.value.provider,
    )
    if (form.value.source === 'remote' && !stillOk) {
      const next = filteredProviders.value[0]
      form.value.provider = next?.id || 'generic'
    }
    if (!isEditing.value && form.value.source === 'remote') {
      const nextUrl = defaultUrlFor(form.value.provider, t)
      const prevUrl = prev ? defaultUrlFor(form.value.provider, prev) : ''
      if (!form.value.base_url || form.value.base_url === prevUrl) {
        form.value.base_url = nextUrl
      }
    }
  },
)

watch(
  () => form.value.provider,
  (pid, prev) => {
    if (isEditing.value || form.value.source !== 'remote' || !pid) return
    const nextUrl = defaultUrlFor(pid, form.value.type)
    const prevUrl = prev ? defaultUrlFor(prev, form.value.type) : ''
    if (!form.value.base_url || form.value.base_url === prevUrl) {
      form.value.base_url = nextUrl
    }
  },
)

watch(
  () => form.value.name,
  (n) => {
    if (form.value.source === 'local' && n) form.value.model = n
  },
)

watch(
  () => form.value.source,
  (source) => {
    if (!props.visible) return
    if (source === 'local') {
      form.value.provider = 'ollama'
      if (!form.value.base_url || form.value.base_url.includes('api.siliconflow')) {
        form.value.base_url = OLLAMA_DEFAULT_URL
      }
      void refreshOllama()
    } else if (!form.value.provider || form.value.provider === 'ollama') {
      form.value.provider = filteredProviders.value[0]?.id || 'siliconflow'
      if (!form.value.base_url || form.value.base_url === OLLAMA_DEFAULT_URL) {
        form.value.base_url = defaultUrlFor(form.value.provider, form.value.type)
      }
    }
  },
)

async function refreshOllama() {
  loadingOllama.value = true
  try {
    const [status, listed] = await Promise.all([getOllamaStatus(), listOllamaModels()])
    ollamaOk.value = status.ok
    ollamaMessage.value = status.ok ? status.message : status.message
    ollamaModels.value = listed.models || []
    if (!listed.ok && listed.message) ollamaMessage.value = listed.message
  } catch (e) {
    ollamaOk.value = false
    ollamaMessage.value = (e as Error).message
    ollamaModels.value = []
  } finally {
    loadingOllama.value = false
  }
}

function setSource(source: ModelSource) {
  if (source === 'local' && localDisabled.value) return
  form.value.source = source
}

function close() {
  emit('update:visible', false)
}

function buildPayload(): ModelCreatePayload {
  const source = form.value.source === 'local' ? 'local' : 'remote'
  const payload: ModelCreatePayload = {
    name: form.value.name.trim(),
    display_name: form.value.display_name?.trim() || undefined,
    type: form.value.type,
    source,
    provider: source === 'local' ? 'ollama' : form.value.provider,
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
    MessagePlugin.warning(isLocal.value ? '请输入或选择 Ollama 模型名' : '请输入模型名')
    return
  }
  if (form.value.type === 'Embedding' && !form.value.model?.trim() && !isLocal.value) {
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
        <h4 class="section-title">模型来源</h4>
        <div class="source-options" role="radiogroup" aria-label="模型来源">
          <button
            type="button"
            class="source-option"
            :class="{ 'is-active': form.source === 'remote' }"
            :disabled="isBuiltin"
            @click="setSource('remote')"
          >
            <t-icon name="cloud" />
            <span>远程 API</span>
          </button>
          <button
            type="button"
            class="source-option"
            :class="{ 'is-active': form.source === 'local', 'is-disabled': localDisabled }"
            :disabled="isBuiltin || localDisabled"
            @click="setSource('local')"
          >
            <t-icon name="server" />
            <span>本地 Ollama</span>
          </button>
        </div>
        <p v-if="localDisabled" class="type-hint">Rerank 不支持 Ollama，请使用远程厂商（Jina / 硅基流动 / 阿里云等）。</p>
        <p v-else-if="isLocal && ollamaOk === false" class="type-hint">
          {{ ollamaMessage || '未检测到 Ollama，仍可手动填写模型名；请确认本机已启动 ollama serve。' }}
        </p>
        <p v-else-if="isLocal && ollamaOk" class="type-hint">{{ ollamaMessage }}</p>
      </section>

      <section v-if="isLocal" class="form-section">
        <h4 class="section-title">连接配置</h4>
        <t-form-item label="Ollama 模型" required-mark>
          <div class="ollama-row">
            <t-select
              v-model="form.name"
              filterable
              creatable
              :loading="loadingOllama"
              placeholder="选择已拉取的模型，或输入 llama3.2"
            >
              <t-option
                v-for="m in ollamaModels"
                :key="m.name"
                :value="m.name"
                :label="m.name"
              />
            </t-select>
            <t-button variant="outline" :loading="loadingOllama" @click="refreshOllama">刷新</t-button>
          </div>
        </t-form-item>
        <t-form-item label="展示名称">
          <t-input v-model="form.display_name" placeholder="留空则用模型名" />
        </t-form-item>
        <t-form-item label="Base URL（OpenAI 兼容 /v1）">
          <t-input v-model="form.base_url" :placeholder="OLLAMA_DEFAULT_URL" />
        </t-form-item>
      </section>

      <section v-else class="form-section">
        <h4 class="section-title">连接配置</h4>
        <t-form-item label="厂商">
          <t-select v-model="form.provider" :disabled="isBuiltin" placeholder="选择厂商">
            <t-option
              v-for="p in filteredProviders"
              :key="p.id"
              :value="p.id"
              :label="p.name"
            />
          </t-select>
          <p v-if="selectedProvider?.description" class="type-hint">{{ selectedProvider.description }}</p>
        </t-form-item>
        <t-form-item label="模型名（如 qwen-plus、gpt-4o-mini）" required-mark>
          <t-input v-model="form.name" :disabled="isBuiltin" placeholder="模型名" />
        </t-form-item>
        <t-form-item label="展示名称">
          <t-input v-model="form.display_name" placeholder="留空则用模型名" />
        </t-form-item>
        <t-form-item label="实际调用模型名">
          <t-input v-model="form.model" placeholder="留空则用模型名" />
        </t-form-item>
        <t-form-item label="Base URL">
          <t-input
            v-model="form.base_url"
            :placeholder="selectedProvider?.default_urls?.[form.type] || '如 https://api.openai.com/v1'"
          />
        </t-form-item>
        <t-form-item v-if="selectedProvider?.requires_auth !== false" label="API Key">
          <t-input
            v-model="form.api_key"
            type="password"
            :placeholder="isEditing ? (hasKey ? '已配置（留空保持不变）' : '未配置') : '远程厂商通常需要填写'"
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
.source-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.source-option {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 40px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-container);
  color: var(--td-text-color-secondary);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.source-option.is-active {
  border-color: var(--td-brand-color);
  color: var(--td-brand-color);
  background: color-mix(in srgb, var(--td-brand-color) 8%, transparent);
}
.source-option.is-disabled,
.source-option:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.ollama-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.ollama-row :deep(.t-select) {
  flex: 1;
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
