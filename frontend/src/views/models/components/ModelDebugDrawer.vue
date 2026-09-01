<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { debugModel, type ModelInfo } from '@/api/models'
import { MODEL_TYPE_ORDER, TYPE_LABELS, typeLabel } from '@/utils/modelTypes'

const props = defineProps<{
  visible: boolean
  models: ModelInfo[]
}>()

const emit = defineEmits<{
  'update:visible': [boolean]
}>()

const selectedId = ref<string>('')
const running = ref(false)
const resultText = ref('')
const prompt = ref('请简要描述这张图片的内容。')
const imageDataUri = ref('')

const grouped = computed(() =>
  MODEL_TYPE_ORDER.map((type) => ({
    type,
    label: TYPE_LABELS[type],
    list: props.models.filter((m) => m.type === type),
  })).filter((g) => g.list.length > 0),
)

watch(
  () => props.visible,
  (open) => {
    if (!open) {
      resultText.value = ''
      imageDataUri.value = ''
      return
    }
    const first = props.models.find((m) => m.status !== 'disabled')
    selectedId.value = first?.id ?? ''
    resultText.value = ''
    prompt.value = '请简要描述这张图片的内容。'
    imageDataUri.value = ''
  },
)

const selected = computed(() => props.models.find((m) => m.id === selectedId.value))
const isVllm = computed(() => selected.value?.type === 'VLLM')

async function onImagePick(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) {
    MessagePlugin.warning('请选择图片文件')
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    imageDataUri.value = typeof reader.result === 'string' ? reader.result : ''
  }
  reader.readAsDataURL(file)
  input.value = ''
}

async function run() {
  if (!selectedId.value) {
    MessagePlugin.warning('请选择要测试的模型')
    return
  }
  running.value = true
  resultText.value = ''
  try {
    const payload =
      isVllm.value
        ? {
            prompt: prompt.value.trim() || '请简要描述这张图片的内容。',
            image_base64: imageDataUri.value || undefined,
          }
        : { prompt: prompt.value.trim() || 'ping' }
    const r = await debugModel(selectedId.value, payload)
    resultText.value = r.ok
      ? `成功（${r.latency_ms}ms）\n${r.message}`
      : `失败\n${r.message}`
    if (r.ok) MessagePlugin.success('连接正常')
  } catch (e) {
    resultText.value = (e as Error).message
    MessagePlugin.error((e as Error).message)
  } finally {
    running.value = false
  }
}

function close() {
  emit('update:visible', false)
}
</script>

<template>
  <t-drawer
    :visible="visible"
    header="模型调试"
    size="520px"
    :footer="false"
    @update:visible="(v: boolean) => emit('update:visible', v)"
  >
    <p class="hint">
      选择已配置的模型并测试 API 连通性。VLLM 支持上传测试图片。
    </p>

    <t-form label-align="top">
      <t-form-item label="模型">
        <t-select v-model="selectedId" filterable placeholder="选择模型">
          <t-option-group v-for="g in grouped" :key="g.type" :label="g.label">
            <t-option
              v-for="m in g.list"
              :key="m.id"
              :value="m.id"
              :label="m.display_name || m.name"
            />
          </t-option-group>
        </t-select>
      </t-form-item>
      <div v-if="selected" class="meta">
        <span>{{ typeLabel(selected.type) }}</span>
        <span class="sep">·</span>
        <span>{{ selected.source === 'local' ? '本地 · Ollama' : selected.provider_name || selected.provider || selected.source }}</span>
        <t-tag
          v-if="selected.credentials?.api_key"
          size="small"
          variant="light"
          theme="success"
        >
          Key 已配置
        </t-tag>
        <t-tag v-else size="small" variant="light">Key 未配置</t-tag>
        <t-tag v-if="selected.is_default" size="small" theme="success" variant="light">默认</t-tag>
      </div>

      <t-form-item :label="isVllm ? '视觉提示词' : '测试提示词'">
        <t-textarea v-model="prompt" :autosize="{ minRows: 2, maxRows: 4 }" />
      </t-form-item>

      <t-form-item v-if="isVllm" label="测试图片（可选，不上传则使用内置小图）">
        <input type="file" accept="image/*" @change="onImagePick" />
        <p v-if="imageDataUri" class="field-hint">已选择测试图片</p>
      </t-form-item>
    </t-form>

    <div class="actions">
      <t-button theme="primary" :loading="running" :disabled="!selectedId" @click="run">
        运行测试
      </t-button>
      <t-button variant="outline" @click="close">关闭</t-button>
    </div>

    <div v-if="resultText" class="result">
      <div class="result-title">结果</div>
      <pre>{{ resultText }}</pre>
    </div>
  </t-drawer>
</template>

<style scoped>
.hint {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  line-height: 1.5;
}
.meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  margin-bottom: 8px;
}
.sep {
  opacity: 0.6;
}
.field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}
.actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.result {
  margin-top: 20px;
  padding: 12px;
  border-radius: 8px;
  background: var(--td-bg-color-secondarycontainer);
  border: 1px solid var(--td-border-level-1-color);
}
.result-title {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--td-text-color-secondary);
}
.result pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.5;
}
</style>
