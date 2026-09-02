<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import {
  BUILTIN_AGENT_ID,
  listAgents,
  listTools,
  updateAgent,
  type AgentInfo,
  type ToolSpec,
} from '@/api/agents'

const loading = ref(false)
const agents = ref<AgentInfo[]>([])
const catalog = ref<ToolSpec[]>([])

const bindOpen = ref(false)
const bindAgentId = ref('')
const bindToolName = ref('')
const bindSaving = ref(false)

const groupedCatalog = computed(() => {
  const groups: { category: string; label: string; tools: ToolSpec[] }[] = []
  const index = new Map<string, number>()
  for (const tool of catalog.value) {
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

const editableAgents = computed(() =>
  agents.value.filter((a) => a.id !== BUILTIN_AGENT_ID && a.status !== 'disabled'),
)

const bindAgent = computed(() => editableAgents.value.find((a) => a.id === bindAgentId.value) || null)

const bindableTools = computed(() => {
  const agent = bindAgent.value
  if (!agent) return catalog.value
  const bound = new Set(agent.tool_names || [])
  return catalog.value.filter((tool) => !bound.has(tool.name))
})

function toolIcon(category: string) {
  if (category === 'planning') return 'root-list'
  if (category === 'knowledge') return 'folder'
  if (category === 'web') return 'internet'
  if (category === 'creation') return 'file'
  return 'tools'
}

function orderedToolNames(names: string[]) {
  const wanted = new Set(names)
  return catalog.value.filter((tool) => wanted.has(tool.name)).map((tool) => tool.name)
}

function agentsUsingTool(toolName: string) {
  return editableAgents.value.filter((a) => (a.tool_names || []).includes(toolName))
}

function builtinAgentsUsingTool(toolName: string) {
  return agents.value.filter(
    (a) => a.id === BUILTIN_AGENT_ID && (a.tool_names || []).includes(toolName),
  )
}

function toolDeleteHint(tool: ToolSpec) {
  const used = agentsUsingTool(tool.name)
  if (!used.length) return `没有智能体绑定「${tool.display_name}」。智能推理的工具不能改。`
  return `将「${tool.display_name}」从 ${used.map((a) => a.name).join('、')} 中移除？`
}

async function load() {
  loading.value = true
  try {
    const [a, tools] = await Promise.all([listAgents(), listTools()])
    agents.value = a
    catalog.value = tools
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openBindTool() {
  bindAgentId.value = editableAgents.value[0]?.id || ''
  bindToolName.value = bindableTools.value[0]?.name || ''
  bindOpen.value = true
}

watch(bindAgentId, () => {
  if (!bindableTools.value.some((tool) => tool.name === bindToolName.value)) {
    bindToolName.value = bindableTools.value[0]?.name || ''
  }
})

async function confirmBindTool() {
  const agent = bindAgent.value
  if (!agent) {
    MessagePlugin.warning('请选择智能体')
    return
  }
  if (!bindToolName.value) {
    MessagePlugin.warning('请选择要添加的工具')
    return
  }
  bindSaving.value = true
  try {
    await updateAgent(agent.id, {
      name: agent.name,
      tool_names: orderedToolNames([...(agent.tool_names || []), bindToolName.value]),
    })
    MessagePlugin.success(`已向「${agent.name}」添加工具`)
    bindOpen.value = false
    await load()
  } catch {
    /* interceptor */
  } finally {
    bindSaving.value = false
  }
}

async function unbindTool(tool: ToolSpec) {
  const targets = agentsUsingTool(tool.name)
  if (!targets.length) {
    MessagePlugin.warning(`没有可修改的智能体绑定「${tool.display_name}」`)
    return
  }
  try {
    for (const agent of targets) {
      const next = (agent.tool_names || []).filter((name) => name !== tool.name)
      if (!next.length) {
        MessagePlugin.warning(`「${agent.name}」至少要保留一个工具，已跳过`)
        continue
      }
      await updateAgent(agent.id, { name: agent.name, tool_names: next })
    }
    MessagePlugin.success(`已移除「${tool.display_name}」`)
    await load()
  } catch {
    /* interceptor */
  }
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="tool-settings">
    <div class="section-header">
      <h2>工具</h2>
      <p class="section-description">
        系统工具由代码登记，不能在页面里新建可执行工具。这里可以把已有工具绑定到智能体。「智能推理」自带的工具不能改。
      </p>
    </div>

    <t-loading :loading="loading" size="small">
      <div class="tool-sections">
        <div class="tool-toolbar">
          <p class="tool-toolbar__hint">
            删除只会从 PPT 助手等可编辑智能体上解除绑定，不会卸载工具本身。
          </p>
          <t-button theme="primary" :disabled="!editableAgents.length" @click="openBindTool">
            <template #icon><t-icon name="add" /></template>
            绑定到智能体
          </t-button>
        </div>
        <section v-for="group in groupedCatalog" :key="group.category" class="tool-section">
          <h3 class="tool-section__title">{{ group.label }}</h3>
          <div class="tool-list">
            <div v-for="tool in group.tools" :key="tool.name" class="tool-row">
              <div class="tool-row__badge">
                <t-icon :name="toolIcon(tool.category)" size="16px" />
              </div>
              <div class="tool-row__main">
                <div class="tool-row__line">
                  <span class="tool-row__name">{{ tool.display_name }}</span>
                  <t-popconfirm
                    :content="toolDeleteHint(tool)"
                    :confirm-btn="{ content: '删除', theme: 'danger' }"
                    cancel-btn="取消"
                    placement="bottom"
                    @confirm="unbindTool(tool)"
                  >
                    <t-button theme="danger" variant="text" size="small">删除</t-button>
                  </t-popconfirm>
                  <span class="tool-row__desc">{{ tool.description }}</span>
                </div>
                <div class="chip-row">
                  <span
                    v-for="agent in builtinAgentsUsingTool(tool.name)"
                    :key="agent.id"
                    class="tool-chip tool-chip--locked"
                  >{{ agent.name }}</span>
                  <span
                    v-for="agent in agentsUsingTool(tool.name)"
                    :key="agent.id"
                    class="tool-chip"
                  >{{ agent.name }}</span>
                  <span
                    v-if="!builtinAgentsUsingTool(tool.name).length && !agentsUsingTool(tool.name).length"
                    class="tool-chip tool-chip--empty"
                  >未绑定</span>
                </div>
              </div>
            </div>
          </div>
        </section>
        <p v-if="!catalog.length" class="empty-hint">还没有可用工具。</p>
      </div>
    </t-loading>

    <t-dialog
      v-model:visible="bindOpen"
      header="绑定到智能体"
      width="480px"
      attach="body"
      :confirm-btn="{ content: '绑定', loading: bindSaving }"
      @confirm="confirmBindTool"
    >
      <div class="bind-form">
        <label class="bind-field">
          <span>智能体</span>
          <t-select
            v-model="bindAgentId"
            placeholder="选择智能体"
            :options="editableAgents.map((a) => ({ label: a.name, value: a.id }))"
          />
        </label>
        <label class="bind-field">
          <span>工具</span>
          <t-select
            v-model="bindToolName"
            placeholder="选择工具"
            :options="bindableTools.map((t) => ({ label: t.display_name, value: t.name }))"
          />
        </label>
      </div>
    </t-dialog>
  </div>
</template>

<style scoped>
.tool-settings {
  width: 100%;
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 20px 28px;
  box-sizing: border-box;
}

.section-header h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 600;
  line-height: 32px;
  color: var(--td-text-color-primary);
}

.section-description {
  margin: 4px 0 0;
  font-size: 14px;
  color: var(--td-text-color-placeholder);
  line-height: 20px;
}

.tool-sections {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 860px;
  margin-top: 20px;
}

.tool-toolbar {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.tool-toolbar__hint {
  margin: 0;
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--td-text-color-placeholder);
}

.tool-section__title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.tool-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-row {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-container);
}

.tool-row__badge {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(98, 53, 187, 0.1);
  color: #6235bb;
}

.tool-row__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tool-row__line {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.tool-row__name {
  flex: 0 0 auto;
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.tool-row__desc {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 4px;
  background: var(--td-bg-color-secondarycontainer);
  color: var(--td-text-color-secondary);
  font-size: 12px;
  line-height: 22px;
  white-space: nowrap;
}

.tool-chip--locked {
  color: var(--td-text-color-placeholder);
}

.tool-chip--empty {
  background: transparent;
  color: var(--td-text-color-placeholder);
  padding-left: 0;
}

.bind-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.bind-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--td-text-color-placeholder);
}
</style>
