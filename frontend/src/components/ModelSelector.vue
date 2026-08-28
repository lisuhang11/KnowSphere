<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { MessagePlugin } from 'tdesign-vue-next'
import { listModels, type ModelInfo, type ModelType } from '@/api/models'
import { modelDisplayName } from '@/utils/modelDefaults'
import { filterModelsByType } from './modelSelectorFilter'

const props = withDefaults(
  defineProps<{
    modelType: ModelType
    selectedModelId?: string
    disabled?: boolean
    placeholder?: string
    status?: 'default' | 'success' | 'warning' | 'error'
    clearable?: boolean
    allModels?: ModelInfo[]
  }>(),
  {
    disabled: false,
    placeholder: '选择模型',
    status: 'default',
    clearable: false,
  },
)

const emit = defineEmits<{
  'update:selectedModelId': [value: string]
  'add-model': []
}>()

const router = useRouter()
const models = ref<ModelInfo[]>([])
const loading = ref(false)

const placeholderText = computed(() => props.placeholder)

watch(
  () => [props.allModels, props.modelType] as const,
  ([all]) => {
    if (all && Array.isArray(all)) {
      models.value = filterModelsByType(all, props.modelType)
    }
  },
  { immediate: true },
)

async function loadModels() {
  if (props.allModels) return
  loading.value = true
  try {
    const result = await listModels()
    models.value = filterModelsByType(result, props.modelType)
  } catch (e) {
    MessagePlugin.error((e as Error).message)
    models.value = []
  } finally {
    loading.value = false
  }
}

function handleChange(value?: string) {
  if (value === '__add_model__') {
    emit('add-model')
    router.push('/models')
    return
  }
  emit('update:selectedModelId', value || '')
}

defineExpose({ refresh: loadModels })

onMounted(() => {
  if (!props.allModels) loadModels()
})
</script>

<template>
  <div class="model-selector">
    <t-select
      :value="selectedModelId"
      :placeholder="placeholderText"
      :disabled="disabled"
      :loading="loading"
      :status="status"
      :clearable="clearable"
      filterable
      style="width: 100%"
      @change="handleChange"
    >
      <t-option
        v-for="m in models"
        :key="m.id"
        :value="m.id"
        :label="modelDisplayName(m)"
      >
        <div class="model-option">
          <span class="model-name">{{ modelDisplayName(m) }}</span>
          <t-tag v-if="m.is_builtin" size="small" theme="primary" variant="light">内置</t-tag>
          <t-tag v-if="m.is_default" size="small" theme="success" variant="light">默认</t-tag>
        </div>
      </t-option>
      <t-option v-if="!disabled" value="__add_model__" class="add-model-option">
        <div class="model-option add">
          <t-icon name="add" class="add-icon" />
          <span>在模型管理中添加…</span>
        </div>
      </t-option>
    </t-select>
  </div>
</template>

<style scoped>
.model-option {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.model-name {
  flex: 1;
  min-width: 0;
}
.model-option.add {
  color: var(--td-brand-color);
}
.add-icon {
  font-size: 14px;
}
</style>
