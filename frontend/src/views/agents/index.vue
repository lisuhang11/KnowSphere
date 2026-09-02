<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { MessagePlugin, type DropdownOption } from 'tdesign-vue-next'
import {
  deleteAgent,
  listAgents,
  listTools,
  updateAgent,
  type AgentInfo,
  type ToolSpec,
} from '@/api/agents'
import AgentEditorDrawer from './components/AgentEditorDrawer.vue'

const loading = ref(false)
const agents = ref<AgentInfo[]>([])
const catalog = ref<ToolSpec[]>([])

const agentDrawer = ref(false)
const editingAgent = ref<AgentInfo | null>(null)

const agentCount = computed(() => agents.value.length)

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

function openAddAgent() {
  editingAgent.value = null
  agentDrawer.value = true
}

function openEditAgent(item: AgentInfo) {
  editingAgent.value = item
  agentDrawer.value = true
}

async function setDefault(item: AgentInfo) {
  try {
    await updateAgent(item.id, { name: item.name, is_default: true })
    MessagePlugin.success(`已将「${item.name}」设为默认`)
    await load()
  } catch {
    /* interceptor */
  }
}

async function removeAgent(item: AgentInfo) {
  try {
    await deleteAgent(item.id)
    MessagePlugin.success('智能体已删除')
    await load()
  } catch {
    /* interceptor */
  }
}

function agentMenu(item: AgentInfo): DropdownOption[] {
  const opts: DropdownOption[] = [{ content: '编辑', value: 'edit' }]
  if (!item.is_default) opts.push({ content: '设为默认', value: 'default' })
  return opts
}

function onAgentMenu(action: string, item: AgentInfo) {
  if (action === 'edit') openEditAgent(item)
  else if (action === 'default') void setDefault(item)
}

onMounted(() => {
  void load()
})
</script>

<template>
  <div class="agent-settings">
    <div class="section-header">
      <h2>智能体</h2>
      <p class="section-description">
        配置智能体的名称、提示词和可用工具。「智能推理」自带的工具由系统维护，不能改；其它智能体可在编辑页增删。系统工具目录见
        <router-link to="/tools">工具</router-link>。
      </p>
    </div>

    <t-loading :loading="loading" size="small">
      <p class="list-meta">共 {{ agentCount }} 个智能体</p>
      <div class="card-grid">
        <div
          v-for="item in agents"
          :key="item.id"
          class="res-card"
          :class="{ 'res-card--builtin': item.is_builtin, 'res-card--clickable': true }"
          role="button"
          tabindex="0"
          @click="openEditAgent(item)"
          @keydown.enter="openEditAgent(item)"
        >
          <div class="res-card__badge">
            <t-icon name="user" size="18px" />
          </div>
          <div class="res-card__body">
            <div class="res-card__header">
              <h3 class="res-card__title">{{ item.name }}</h3>
              <t-tag v-if="item.is_builtin" size="small" variant="light">内置</t-tag>
              <t-tag v-if="item.is_default" size="small" theme="success" variant="light">默认</t-tag>
              <div class="res-card__actions" @click.stop>
                <t-dropdown
                  :options="agentMenu(item)"
                  placement="bottom-right"
                  attach="body"
                  trigger="click"
                  @click="(data: DropdownOption) => onAgentMenu(String(data.value ?? ''), item)"
                >
                  <t-button variant="text" shape="square" size="small">
                    <t-icon name="ellipsis" />
                  </t-button>
                </t-dropdown>
                <t-popconfirm
                  v-if="!item.is_builtin && !item.is_default"
                  :content="`确定删除「${item.name}」？`"
                  :confirm-btn="{ content: '删除', theme: 'danger' }"
                  cancel-btn="取消"
                  placement="bottom-right"
                  @confirm="removeAgent(item)"
                >
                  <t-button theme="danger" shape="square" variant="text" size="small" @click.stop>
                    <template #icon><t-icon name="delete" /></template>
                  </t-button>
                </t-popconfirm>
              </div>
            </div>
            <p v-if="item.description" class="res-card__desc">{{ item.description }}</p>
            <div v-if="item.tools.length" class="chip-row">
              <span v-for="tool in item.tools" :key="tool.name" class="tool-chip">{{ tool.display_name }}</span>
            </div>
            <p v-else class="res-card__desc">未绑定工具</p>
          </div>
        </div>
        <button type="button" class="res-card res-card--add" @click="openAddAgent">
          <span class="res-card--add__icon"><t-icon name="add" /></span>
          <span class="res-card--add__label">添加智能体</span>
        </button>
      </div>
    </t-loading>

    <AgentEditorDrawer
      v-model:visible="agentDrawer"
      :editing="editingAgent"
      :catalog="catalog"
      @saved="load"
    />
  </div>
</template>

<style scoped>
.agent-settings {
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

.section-description a {
  color: var(--td-brand-color);
  text-decoration: none;
}

.section-description a:hover {
  text-decoration: underline;
}

.list-meta {
  margin: 16px 0 12px;
  font-size: 13px;
  color: var(--td-text-color-placeholder);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px;
}

.res-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 10px;
  background: var(--td-bg-color-container);
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-align: left;
}

.res-card--clickable {
  cursor: pointer;
}

.res-card--clickable:hover {
  border-color: var(--td-brand-color-3, var(--td-brand-color));
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
}

.res-card--builtin {
  background: var(--td-bg-color-secondarycontainer);
}

.res-card__badge {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 82, 217, 0.1);
  color: #0052d9;
}

.res-card__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.res-card__header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.res-card__title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.res-card__actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.res-card__desc {
  margin: 0;
  font-size: 13px;
  color: var(--td-text-color-secondary);
  line-height: 1.55;
}

.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
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

.res-card--add {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 68px;
  border-style: dashed;
  background: transparent;
  color: var(--td-text-color-placeholder);
  cursor: pointer;
  font: inherit;
}

.res-card--add:hover {
  color: var(--td-brand-color);
  border-color: var(--td-brand-color);
  background: color-mix(in srgb, var(--td-brand-color) 6%, transparent);
}

.res-card--add__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--td-brand-color) 10%, transparent);
  color: var(--td-brand-color);
  font-size: 18px;
}

.res-card--add__label {
  font-size: 13px;
  font-weight: 500;
}
</style>
