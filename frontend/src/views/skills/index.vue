<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { MessagePlugin } from 'tdesign-vue-next'
import { listAgents, updateAgent, type AgentInfo } from '@/api/agents'
import { listSkills, type SkillSpec } from '@/api/skills'
import { SKILL_ICON } from '@/utils/skillMention'
import SkillFilesDrawer from './components/SkillFilesDrawer.vue'

const loading = ref(false)
const agents = ref<AgentInfo[]>([])
const catalog = ref<SkillSpec[]>([])

const bindOpen = ref(false)
const bindAgentId = ref('')
const bindSkillName = ref('')
const bindSaving = ref(false)

const filesOpen = ref(false)
const previewSkillName = ref('')

const editableAgents = computed(() => agents.value.filter((a) => a.status !== 'disabled'))

const bindAgent = computed(() => editableAgents.value.find((a) => a.id === bindAgentId.value) || null)

const bindableSkills = computed(() => {
  const agent = bindAgent.value
  if (!agent) return catalog.value
  const bound = new Set(agent.skill_names || [])
  return catalog.value.filter((skill) => !bound.has(skill.name))
})

function orderedSkillNames(names: string[]) {
  const known = new Set(catalog.value.map((skill) => skill.name))
  const seen = new Set<string>()
  const out: string[] = []
  for (const name of names) {
    if (!name || seen.has(name) || !known.has(name)) continue
    seen.add(name)
    out.push(name)
  }
  return out
}

function agentsUsingSkill(skillName: string) {
  return editableAgents.value.filter((a) => (a.skill_names || []).includes(skillName))
}

function skillDeleteHint(skill: SkillSpec) {
  const used = agentsUsingSkill(skill.name)
  if (!used.length) return `没有智能体绑定「${skill.name}」。`
  return `将「${skill.name}」从 ${used.map((a) => a.name).join('、')} 中移除？`
}

async function load() {
  loading.value = true
  try {
    const [a, skills] = await Promise.all([listAgents(), listSkills()])
    agents.value = a
    catalog.value = skills
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openFiles(skill: SkillSpec) {
  previewSkillName.value = skill.name
  filesOpen.value = true
}

function openBindSkill() {
  bindAgentId.value = editableAgents.value[0]?.id || ''
  bindSkillName.value = bindableSkills.value[0]?.name || ''
  bindOpen.value = true
}

watch(bindAgentId, () => {
  if (!bindableSkills.value.some((skill) => skill.name === bindSkillName.value)) {
    bindSkillName.value = bindableSkills.value[0]?.name || ''
  }
})

async function confirmBindSkill() {
  const agent = bindAgent.value
  if (!agent) {
    MessagePlugin.warning('请选择智能体')
    return
  }
  if (!bindSkillName.value) {
    MessagePlugin.warning('请选择要添加的技能')
    return
  }
  bindSaving.value = true
  try {
    await updateAgent(agent.id, {
      name: agent.name,
      skill_names: orderedSkillNames([...(agent.skill_names || []), bindSkillName.value]),
    })
    MessagePlugin.success(`已向「${agent.name}」添加技能`)
    bindOpen.value = false
    await load()
  } catch {
    /* interceptor */
  } finally {
    bindSaving.value = false
  }
}

async function unbindSkill(skill: SkillSpec) {
  const targets = agentsUsingSkill(skill.name)
  if (!targets.length) {
    MessagePlugin.warning(`没有智能体绑定「${skill.name}」`)
    return
  }
  try {
    for (const agent of targets) {
      const next = (agent.skill_names || []).filter((name) => name !== skill.name)
      await updateAgent(agent.id, { name: agent.name, skill_names: next })
    }
    MessagePlugin.success(`已移除「${skill.name}」`)
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
  <div class="skill-settings">
    <div class="section-header">
      <h2>技能</h2>
      <p class="section-description">
        技能由仓库 <code>skills/</code> 目录扫描，不能在页面里上传安装。每个智能体用白名单装配技能，内置智能体也可以增删。绑定后模型会按描述自行匹配；聊天输入框
        <code>@</code> 只是本轮点名。目录与智能体绑定见
        <router-link to="/agents">智能体</router-link>。
      </p>
    </div>

    <t-loading :loading="loading" size="small">
      <div class="skill-sections">
        <div class="skill-toolbar">
          <p class="skill-toolbar__hint">
            删除只会从可编辑智能体上解除绑定，不会卸载技能本身。点卡片可浏览 SKILL.md 和脚本。
          </p>
          <t-button theme="primary" :disabled="!editableAgents.length" @click="openBindSkill">
            <template #icon><t-icon name="add" /></template>
            绑定到智能体
          </t-button>
        </div>
        <div class="skill-list">
          <div
            v-for="skill in catalog"
            :key="skill.name"
            class="skill-row"
            role="button"
            tabindex="0"
            @click="openFiles(skill)"
            @keydown.enter="openFiles(skill)"
          >
            <div class="skill-row__badge">
              <t-icon :name="SKILL_ICON" size="16px" />
            </div>
            <div class="skill-row__main">
              <div class="skill-row__line">
                <span class="skill-row__name">{{ skill.name }}</span>
                <span class="skill-row__actions" @click.stop>
                  <t-popconfirm
                    :content="skillDeleteHint(skill)"
                    :confirm-btn="{ content: '删除', theme: 'danger' }"
                    cancel-btn="取消"
                    placement="bottom"
                    @confirm="unbindSkill(skill)"
                  >
                    <t-button theme="danger" variant="text" size="small">删除</t-button>
                  </t-popconfirm>
                </span>
                <span class="skill-row__desc">{{ skill.description }}</span>
              </div>
              <div class="chip-row">
                <span v-if="skill.file_count" class="skill-chip skill-chip--files">
                  {{ skill.file_count }} 个文件
                </span>
                <span
                  v-for="agent in agentsUsingSkill(skill.name)"
                  :key="agent.id"
                  class="skill-chip"
                >{{ agent.name }}</span>
                <span
                  v-if="!agentsUsingSkill(skill.name).length"
                  class="skill-chip skill-chip--empty"
                >未绑定</span>
              </div>
            </div>
          </div>
        </div>
        <p v-if="!catalog.length" class="empty-hint">还没有可用技能。把带 SKILL.md 的目录放到仓库 skills/ 下即可被扫描。</p>
      </div>
    </t-loading>

    <t-dialog
      v-model:visible="bindOpen"
      header="绑定到智能体"
      width="480px"
      attach="body"
      :confirm-btn="{ content: '绑定', loading: bindSaving }"
      @confirm="confirmBindSkill"
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
          <span>技能</span>
          <t-select
            v-model="bindSkillName"
            placeholder="选择技能"
            :options="bindableSkills.map((s) => ({ label: s.name, value: s.name }))"
          />
        </label>
      </div>
    </t-dialog>

    <SkillFilesDrawer v-model:visible="filesOpen" :skill-name="previewSkillName" />
  </div>
</template>

<style scoped>
.skill-settings {
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

.section-description a,
.section-description code {
  color: var(--td-brand-color);
}

.section-description a {
  text-decoration: none;
}

.section-description a:hover {
  text-decoration: underline;
}

.section-description code {
  font-size: 12px;
  padding: 0 4px;
  border-radius: 4px;
  background: var(--td-bg-color-secondarycontainer);
}

.skill-sections {
  display: flex;
  flex-direction: column;
  gap: 20px;
  max-width: 860px;
  margin-top: 20px;
}

.skill-toolbar {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.skill-toolbar__hint {
  margin: 0;
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.5;
  color: var(--td-text-color-placeholder);
}

.skill-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skill-row {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 8px;
  background: var(--td-bg-color-container);
  cursor: pointer;
}

.skill-row:hover {
  border-color: var(--td-brand-color-3, var(--td-brand-color));
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
}

.skill-row__badge {
  flex: 0 0 auto;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, #7c3aed 12%, transparent);
  color: #7c3aed;
}

.skill-row__main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.skill-row__line {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.skill-row__name {
  flex: 0 0 auto;
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
  white-space: nowrap;
}

.skill-row__actions {
  flex: 0 0 auto;
}

.skill-row__desc {
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

.skill-chip {
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

.skill-chip--files {
  background: color-mix(in srgb, #7c3aed 12%, transparent);
  color: #7c3aed;
}

.skill-chip--empty {
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
