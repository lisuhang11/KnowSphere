<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  BUILTIN_AGENT_ID,
  createAgent,
  updateAgent,
  type AgentInfo,
  type AgentPayload,
  type SkillSpec,
  type ToolSpec,
} from '@/api/agents'
import { SKILL_ICON } from '@/utils/skillMention'

const props = defineProps<{
  visible: boolean
  editing: AgentInfo | null
  catalog: ToolSpec[]
  skillCatalog: SkillSpec[]
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
  skill_names: [],
  max_iterations: 25,
  is_default: false,
})
const saving = ref(false)
const addOpen = ref(false)
const addSkillOpen = ref(false)

const isEditing = computed(() => !!props.editing)
const toolsLocked = computed(() => props.editing?.id === BUILTIN_AGENT_ID)
const skillsLocked = computed(() => !!props.editing?.is_builtin)

const catalogByName = computed(() => {
  const map = new Map<string, ToolSpec>()
  for (const tool of props.catalog) map.set(tool.name, tool)
  return map
})

const boundTools = computed(() => {
  const out: ToolSpec[] = []
  for (const name of form.value.tool_names || []) {
    const spec = catalogByName.value.get(name)
    if (spec) out.push(spec)
  }
  return out
})

const availableGroups = computed(() => {
  const bound = new Set(form.value.tool_names || [])
  const groups: { category: string; label: string; tools: ToolSpec[] }[] = []
  const index = new Map<string, number>()
  for (const tool of props.catalog) {
    if (bound.has(tool.name)) continue
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

const canAddTool = computed(() => !toolsLocked.value && availableGroups.value.length > 0)

const boundSkills = computed(() => {
  const wanted = new Set(form.value.skill_names || [])
  return props.skillCatalog.filter((s) => wanted.has(s.name))
})

const availableSkills = computed(() => {
  const bound = new Set(form.value.skill_names || [])
  return props.skillCatalog.filter((s) => !bound.has(s.name))
})

const canAddSkill = computed(() => !skillsLocked.value && availableSkills.value.length > 0)

function orderedNames(names: string[]) {
  const wanted = new Set(names)
  return props.catalog.filter((tool) => wanted.has(tool.name)).map((tool) => tool.name)
}

function orderedSkillNames(names: string[]) {
  const wanted = new Set(names)
  return props.skillCatalog.filter((s) => wanted.has(s.name)).map((s) => s.name)
}

function reset() {
  form.value = {
    name: '',
    description: '',
    system_prompt: '',
    tool_names: [],
    skill_names: [],
    max_iterations: 25,
    is_default: false,
  }
  addOpen.value = false
  addSkillOpen.value = false
}

watch(
  () => [props.visible, props.editing] as const,
  ([open, editing]) => {
    if (!open) {
      addOpen.value = false
      addSkillOpen.value = false
      return
    }
    if (editing) {
      form.value = {
        name: editing.name,
        description: editing.description,
        system_prompt: editing.system_prompt,
        tool_names: [...(editing.tool_names || [])],
        skill_names: [...(editing.skill_names || [])],
        max_iterations: editing.max_iterations || 25,
        is_default: editing.is_default,
      }
    } else {
      reset()
    }
  },
)

function close() {
  addOpen.value = false
  addSkillOpen.value = false
  emit('update:visible', false)
}

function addTool(name: string) {
  if (toolsLocked.value) return
  form.value.tool_names = orderedNames([...(form.value.tool_names || []), name])
  if (!availableGroups.value.length) addOpen.value = false
}

function removeTool(name: string) {
  if (toolsLocked.value) return
  const next = (form.value.tool_names || []).filter((item) => item !== name)
  if (!next.length) {
    MessagePlugin.warning('请至少保留一个工具')
    return
  }
  form.value.tool_names = next
}

function addSkill(name: string) {
  if (skillsLocked.value) return
  form.value.skill_names = orderedSkillNames([...(form.value.skill_names || []), name])
  if (!availableSkills.value.length) addSkillOpen.value = false
}

function removeSkill(name: string) {
  if (skillsLocked.value) return
  form.value.skill_names = (form.value.skill_names || []).filter((item) => item !== name)
}

async function save() {
  const name = form.value.name?.trim()
  if (!name) {
    MessagePlugin.warning('请填写智能体名称')
    return
  }
  if (!(form.value.tool_names || []).length) {
    MessagePlugin.warning('请至少添加一个工具')
    return
  }
  saving.value = true
  try {
    const payload: AgentPayload = {
      name,
      description: form.value.description || '',
      system_prompt: form.value.system_prompt || '',
      max_iterations: form.value.max_iterations || 25,
      is_default: form.value.is_default,
    }
    if (!toolsLocked.value) payload.tool_names = form.value.tool_names || []
    if (!skillsLocked.value) payload.skill_names = form.value.skill_names || []
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
    size="720px"
    :close-btn="true"
    :footer="true"
    :z-index="2500"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="agent-form">
      <label class="field">
        <span class="field-label">名称</span>
        <t-input v-model="form.name" placeholder="例如：资料整理助手" :maxlength="40" />
      </label>
      <label class="field">
        <span class="field-label">说明</span>
        <t-textarea
          v-model="form.description"
          placeholder="这个智能体适合做什么"
          :autosize="{ minRows: 2, maxRows: 4 }"
        />
      </label>

      <div class="field">
        <div class="field-label-row">
          <span class="field-label">工具</span>
          <t-button
            v-if="!toolsLocked"
            theme="primary"
            size="small"
            :disabled="!canAddTool"
            @click="addOpen = true"
          >
            <template #icon><t-icon name="add" /></template>
            添加工具
          </t-button>
        </div>
        <p v-if="toolsLocked" class="hint">「智能推理」的工具由系统维护，不能添加或删除。</p>
        <p v-else class="hint">一行一个工具。点右上角「添加工具」，或点右侧「删除」从本智能体移除。</p>

        <div v-if="boundTools.length" class="bound-list">
          <div v-for="tool in boundTools" :key="tool.name" class="bound-tool">
            <span class="bound-tool__name">{{ tool.display_name }}</span>
            <t-button
              v-if="!toolsLocked"
              class="bound-tool__remove"
              theme="danger"
              variant="text"
              size="small"
              @click="removeTool(tool.name)"
            >
              删除
            </t-button>
            <span class="bound-tool__tag">{{ tool.category_label }}</span>
            <span class="bound-tool__desc">{{ tool.description }}</span>
          </div>
        </div>
        <div v-else class="bound-empty">还没有绑定工具，请点「添加工具」。</div>
        <p v-if="!toolsLocked && !canAddTool && boundTools.length" class="hint">目录中的工具都已绑定。</p>
      </div>

      <div class="field">
        <div class="field-label-row">
          <span class="field-label">技能</span>
          <t-button
            v-if="!skillsLocked"
            theme="primary"
            size="small"
            :disabled="!canAddSkill"
            @click="addSkillOpen = true"
          >
            <template #icon><t-icon name="add" /></template>
            添加技能
          </t-button>
        </div>
        <p v-if="skillsLocked" class="hint">内置智能体不启用技能。自定义智能体可勾选仓库内技能；空列表表示关闭。</p>
        <p v-else class="hint">技能不是工具勾选。绑定后模型会按描述自行匹配；输入框 @ 只是本轮点名。</p>

        <div v-if="boundSkills.length" class="bound-list">
          <div v-for="skill in boundSkills" :key="skill.name" class="bound-tool bound-tool--skill">
            <span class="skill-badge" aria-hidden="true">
              <t-icon :name="SKILL_ICON" size="16px" />
            </span>
            <span class="bound-tool__name">{{ skill.name }}</span>
            <t-button
              v-if="!skillsLocked"
              class="bound-tool__remove"
              theme="danger"
              variant="text"
              size="small"
              @click="removeSkill(skill.name)"
            >
              删除
            </t-button>
            <span class="bound-tool__desc">{{ skill.description }}</span>
          </div>
        </div>
        <div v-else class="bound-empty">{{ skillsLocked ? '未启用技能。' : '还没有绑定技能，可点「添加技能」。' }}</div>
        <p v-if="!skillsLocked && !canAddSkill && boundSkills.length" class="hint">目录中的技能都已绑定。</p>
      </div>

      <label class="field">
        <span class="field-label">系统提示词</span>
        <t-textarea
          v-model="form.system_prompt"
          placeholder="留空则按所绑工具自动生成。可以在这里写角色、页数、输出格式。"
          :autosize="{ minRows: 4, maxRows: 8 }"
        />
      </label>
      <label class="field">
        <span class="field-label">最大推理步数</span>
        <t-input-number v-model="form.max_iterations" :min="4" :max="80" theme="column" />
      </label>
      <t-checkbox v-model="form.is_default">设为默认智能体</t-checkbox>
    </div>
    <template #footer>
      <t-button variant="outline" @click="close">取消</t-button>
      <t-button theme="primary" :loading="saving" @click="save">保存</t-button>
    </template>
  </t-drawer>

  <t-dialog
    v-model:visible="addOpen"
    header="添加工具"
    width="520px"
    attach="body"
    :z-index="3200"
    :footer="false"
    :confirm-on-enter="false"
  >
    <p v-if="!availableGroups.length" class="hint">没有可添加的工具。</p>
    <div v-else class="picker-groups">
      <section v-for="group in availableGroups" :key="group.category" class="picker-group">
        <div class="picker-group__title">{{ group.label }}</div>
        <button
          v-for="tool in group.tools"
          :key="tool.name"
          type="button"
          class="picker-tool"
          @click="addTool(tool.name)"
        >
          <span class="picker-tool__name">{{ tool.display_name }}</span>
          <span class="picker-tool__desc">{{ tool.description }}</span>
          <span class="picker-tool__action">添加</span>
        </button>
      </section>
    </div>
  </t-dialog>

  <t-dialog
    v-model:visible="addSkillOpen"
    header="添加技能"
    width="520px"
    attach="body"
    :z-index="3200"
    :footer="false"
    :confirm-on-enter="false"
  >
    <p v-if="!availableSkills.length" class="hint">没有可添加的技能。</p>
    <div v-else class="picker-groups">
      <button
        v-for="skill in availableSkills"
        :key="skill.name"
        type="button"
        class="picker-tool picker-tool--skill"
        @click="addSkill(skill.name)"
      >
        <span class="skill-badge" aria-hidden="true">
          <t-icon :name="SKILL_ICON" size="16px" />
        </span>
        <span class="picker-tool__name">{{ skill.name }}</span>
        <span class="picker-tool__desc">{{ skill.description }}</span>
        <span class="picker-tool__action">添加</span>
      </button>
    </div>
  </t-dialog>
</template>

<style scoped>
.agent-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  box-sizing: border-box;
  padding: 4px 0 16px;
}

.field {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  width: 100%;
}

.field-label,
.field-label-row .field-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.field-label-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.hint {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--td-text-color-placeholder);
}

.bound-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}

.bound-tool {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 10px;
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-container);
}

.bound-tool__name {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.bound-tool__remove {
  flex: 0 0 auto;
}

.bound-tool__tag {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  white-space: nowrap;
}

.bound-tool__desc {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bound-empty {
  padding: 14px;
  border: 1px dashed var(--td-component-stroke);
  border-radius: 8px;
  text-align: center;
  font-size: 13px;
  color: var(--td-text-color-placeholder);
}

.picker-groups {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 60vh;
  overflow: auto;
}

.picker-group__title {
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--td-text-color-secondary);
}

.picker-tool {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 12px;
  width: 100%;
  margin: 0 0 8px;
  padding: 10px 12px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-container);
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.picker-tool:hover {
  border-color: var(--td-brand-color);
}

.picker-tool__name {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.picker-tool__desc {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.picker-tool__action {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 600;
  color: var(--td-brand-color);
  white-space: nowrap;
}

.skill-badge {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 7px;
  background: color-mix(in srgb, #7c3aed 12%, transparent);
  color: #7c3aed;
}

.bound-tool--skill,
.picker-tool--skill {
  align-items: center;
}
</style>
