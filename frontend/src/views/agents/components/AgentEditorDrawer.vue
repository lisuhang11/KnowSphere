<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  createAgent,
  updateAgent,
  type AgentInfo,
  type AgentPayload,
  type ToolSpec,
} from '@/api/agents'

const props = defineProps<{
  visible: boolean
  editing: AgentInfo | null
  catalog: ToolSpec[]
}>()

const emit = defineEmits<{
  'update:visible': [boolean]
  saved: []
}>()

const form = ref<AgentPayload>({
  name: '',
  description: '',
  system_prompt: '',
  tool_names: [],
  max_iterations: 25,
  is_default: false,
})
const saving = ref(false)

const isEditing = computed(() => !!props.editing)
const isBuiltin = computed(() => !!props.editing?.is_builtin)

const groupedCatalog = computed(() => {
  const groups: { category: string; label: string; tools: ToolSpec[] }[] = []
  const index = new Map<string, number>()
  for (const tool of props.catalog) {
    const key = tool.category || 'other'
    let i = index.get(key)
    if (i == null) {
      i = groups.length
      index.set(key, i)
      groups.push({ category: key, label: tool.category_label || key, tools: [] })
    }
    groups[i].tools.push(tool)
  }
  return groups
})

function reset() {
  form.value = {
    name: '',
    description: '',
    system_prompt: '',
    tool_names: [],
    max_iterations: 25,
    is_default: false,
  }
}

watch(
  () => [props.visible, props.editing] as const,
  ([open, editing]) => {
    if (!open) return
    if (editing) {
      form.value = {
        name: editing.name,
        description: editing.description,
        system_prompt: editing.system_prompt,
        tool_names: [...(editing.tool_names || [])],
        max_iterations: editing.max_iterations || 25,
        is_default: editing.is_default,
      }
    } else {
      reset()
    }
  },
)

function close() {
  emit('update:visible', false)
}

function toggleTool(name: string, checked: boolean) {
  if (isBuiltin.value) return
  const cur = new Set(form.value.tool_names || [])
  if (checked) cur.add(name)
  else cur.delete(name)
  form.value.tool_names = [...cur]
}

async function save() {
  const name = form.value.name?.trim()
  if (!name) {
    MessagePlugin.warning('请填写智能体名称')
    return
  }
  if (!(form.value.tool_names || []).length) {
    MessagePlugin.warning('请至少选择一个工具')
    return
  }
  saving.value = true
  try {
    const payload: AgentPayload = {
      name,
      description: form.value.description || '',
      system_prompt: form.value.system_prompt || '',
      tool_names: form.value.tool_names || [],
      max_iterations: form.value.max_iterations || 25,
      is_default: form.value.is_default,
    }
    if (props.editing) await updateAgent(props.editing.id, payload)
    else await createAgent(payload)
    MessagePlugin.success(isEditing.value ? '智能体已更新' : '智能体已创建')
    emit('saved')
    close()
  } catch {
    /* interceptor */
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <t-drawer
    :visible="visible"
    :header="isEditing ? '编辑智能体' : '新建智能体'"
    size="520px"
    :close-btn="true"
    :footer="true"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="agent-form">
      <t-form layout="vertical">
        <t-form-item label="名称" required>
          <t-input v-model="form.name" placeholder="例如：资料整理助手" :maxlength="40" />
        </t-form-item>
        <t-form-item label="说明">
          <t-textarea
            v-model="form.description"
            placeholder="这个智能体适合做什么"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
        </t-form-item>
        <t-form-item label="工具" required>
          <p v-if="isBuiltin" class="builtin-hint">内置智能体的工具随系统更新，不能改勾选。</p>
          <p v-else-if="!catalog.length" class="empty-hint">还没有可用工具。</p>
          <div v-for="group in groupedCatalog" :key="group.category" class="tool-group">
            <div class="tool-group__title">{{ group.label }}</div>
            <label
              v-for="tool in group.tools"
              :key="tool.name"
              class="tool-check"
              :class="{ 'is-checked': (form.tool_names || []).includes(tool.name) }"
            >
              <t-checkbox
                :checked="(form.tool_names || []).includes(tool.name)"
                :disabled="isBuiltin"
                @change="(v: boolean) => toggleTool(tool.name, v)"
              />
              <span class="tool-check__body">
                <span class="tool-check__title">
                  <span class="tool-check__name">{{ tool.display_name }}</span>
                  <code class="tool-check__code">{{ tool.name }}</code>
                </span>
                <span class="tool-check__desc">{{ tool.description }}</span>
              </span>
            </label>
          </div>
        </t-form-item>
        <t-form-item label="系统提示词">
          <t-textarea
            v-model="form.system_prompt"
            placeholder="留空则按所绑工具自动生成。以后做 PPT / 整理数据智能体时，可在此写角色与输出格式。"
            :autosize="{ minRows: 4, maxRows: 10 }"
          />
        </t-form-item>
        <t-form-item label="最大推理步数">
          <t-input-number v-model="form.max_iterations" :min="4" :max="80" theme="column" />
        </t-form-item>
        <t-form-item>
          <t-checkbox v-model="form.is_default">设为默认智能体</t-checkbox>
        </t-form-item>
      </t-form>
    </div>
    <template #footer>
      <t-button variant="outline" @click="close">取消</t-button>
      <t-button theme="primary" :loading="saving" @click="save">保存</t-button>
    </template>
  </t-drawer>
</template>

<style scoped>
.agent-form {
  padding: 4px 4px 24px;
}

.builtin-hint,
.empty-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
}

.tool-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 14px;
}

.tool-group__title {
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
}

.tool-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  cursor: pointer;
  background: var(--td-bg-color-container);
}

.tool-check :deep(.t-checkbox) {
  margin-top: 3px;
}

.tool-check.is-checked {
  border-color: color-mix(in srgb, var(--td-brand-color) 45%, var(--td-component-stroke));
  background: color-mix(in srgb, var(--td-brand-color) 5%, var(--td-bg-color-container));
}

.tool-check__body {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.tool-check__title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
}

.tool-check__name {
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.tool-check__code {
  font-size: 11px;
  color: var(--td-text-color-placeholder);
}

.tool-check__desc {
  font-size: 12px;
  color: var(--td-text-color-secondary);
  line-height: 1.55;
}
</style>
