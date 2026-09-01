<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { MessagePlugin, type DropdownOption } from 'tdesign-vue-next'
import {
  deleteModel,
  listModels,
  listProviders,
  updateModel,
  type ModelInfo,
  type ModelProvider,
  type ModelType,
} from '@/api/models'
import {
  MODEL_TYPE_ORDER,
  TYPE_CARD_CLASS,
  TYPE_ICONS,
  typeLabel,
  providerChipLabel,
  type ModelFilter,
} from '@/utils/modelTypes'
import ModelEditorDrawer from './components/ModelEditorDrawer.vue'
import ModelDebugDrawer from './components/ModelDebugDrawer.vue'

const loading = ref(false)
const models = ref<ModelInfo[]>([])
const providers = ref<ModelProvider[]>([])

const activeTab = ref<ModelFilter>('all')
const drawerVisible = ref(false)
const debugVisible = ref(false)
const editing = ref<ModelInfo | null>(null)

const filteredModels = computed(() => {
  if (activeTab.value === 'all') return models.value
  return models.value.filter((m) => m.type === activeTab.value)
})

function countByType(t: ModelType) {
  return models.value.filter((m) => m.type === t).length
}

function tabLabel(value: ModelFilter, label: string) {
  if (value === 'all') return `${label}(${models.value.length})`
  return `${label}(${countByType(value)})`
}

const defaultTypeForCreate = computed((): ModelType => {
  if (activeTab.value !== 'all') return activeTab.value
  return 'KnowledgeQA'
})

function modelDisplayName(m: ModelInfo) {
  const d = m.display_name?.trim()
  return d || m.name
}

function canDelete(m: ModelInfo) {
  return !m.is_builtin && !m.is_default
}

async function load() {
  loading.value = true
  try {
    models.value = await listModels()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function openAddDialog() {
  editing.value = null
  drawerVisible.value = true
}

function openEdit(m: ModelInfo) {
  editing.value = m
  drawerVisible.value = true
}

function onCardClick(m: ModelInfo) {
  openEdit(m)
}

async function testConnection(m: ModelInfo) {
  const { debugModel } = await import('@/api/models')
  MessagePlugin.info(`正在测试「${modelDisplayName(m)}」...`)
  try {
    const r = await debugModel(m.id)
    if (r.ok) MessagePlugin.success(r.message)
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

async function setDefault(m: ModelInfo) {
  try {
    await updateModel(m.id, { is_default: true })
    MessagePlugin.success(`已将「${modelDisplayName(m)}」设为默认`)
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

async function removeModel(m: ModelInfo) {
  if (m.is_builtin) {
    MessagePlugin.warning('内置模型不可删除')
    return
  }
  try {
    await deleteModel(m.id)
    MessagePlugin.success('模型已删除')
    await load()
  } catch (e) {
    MessagePlugin.error((e as Error).message)
  }
}

function cardMenuAction(action: string, m: ModelInfo) {
  if (action === 'edit') openEdit(m)
  else if (action === 'test') testConnection(m)
  else if (action === 'default') setDefault(m)
}

function menuClickHandler(m: ModelInfo) {
  return (data: DropdownOption) => cardMenuAction(String(data.value ?? ''), m)
}

function menuOptions(m: ModelInfo): DropdownOption[] {
  const opts: DropdownOption[] = [{ content: '编辑', value: 'edit' }]
  opts.push({ content: '测试连接', value: 'test' })
  if (m.type !== 'ASR' && !m.is_default) {
    opts.push({ content: '设为默认', value: 'default' })
  }
  return opts
}

onMounted(async () => {
  try {
    providers.value = await listProviders()
  } catch {
    providers.value = []
  }
  await load()
})
</script>

<template>
  <div class="model-settings">
    <div class="section-header">
      <div class="section-header__top">
        <div>
          <h2>模型管理</h2>
          <p class="section-description">
            配置对话、向量化、重排与视觉理解（VLLM）模型；知识库、对话图片与附件解析将使用此处配置的模型与 API Key。
          </p>
        </div>
        <t-button
          theme="primary"
          variant="text"
          class="model-test-trigger"
          @click="debugVisible = true"
        >
          <template #icon><t-icon name="play-circle-stroke" /></template>
          模型调试
        </t-button>
      </div>
    </div>

    <t-tabs v-model="activeTab" class="model-type-tabs">
      <t-tab-panel value="all" :label="tabLabel('all', '全部')" />
      <t-tab-panel
        v-for="t in MODEL_TYPE_ORDER"
        :key="t"
        :value="t"
        :label="tabLabel(t, typeLabel(t))"
      />
    </t-tabs>

    <t-loading :loading="loading" size="small" class="model-list-loading">
      <div v-if="!loading" class="model-grid">
        <div
          v-for="m in filteredModels"
          :key="m.id"
          class="model-card"
          :class="[
            TYPE_CARD_CLASS[m.type],
            {
              'model-card--builtin': m.is_builtin,
              'model-card--clickable': true,
            },
          ]"
          role="button"
          tabindex="0"
          @click="onCardClick(m)"
          @keydown.enter="onCardClick(m)"
        >
          <div class="model-card__badge" :aria-label="typeLabel(m.type)">
            <t-icon :name="TYPE_ICONS[m.type]" size="18px" />
          </div>
          <div class="model-card__body">
            <div class="model-card__header">
              <h3 class="model-card__title">{{ modelDisplayName(m) }}</h3>
              <span
                v-if="m.is_builtin"
                class="model-card__lock"
                title="内置模型"
                aria-label="内置模型"
              >
                <t-icon name="edit-1" />
              </span>
              <t-tag v-if="m.is_default" size="small" theme="success" variant="light">默认</t-tag>
              <div class="model-card__actions" @click.stop>
                <t-dropdown
                  :options="menuOptions(m)"
                  placement="bottom-right"
                  attach="body"
                  trigger="click"
                  @click="menuClickHandler(m)"
                >
                  <t-button
                    variant="text"
                    shape="square"
                    size="small"
                    class="model-card__action-btn model-card__more"
                  >
                    <t-icon name="ellipsis" />
                  </t-button>
                </t-dropdown>
                <t-popconfirm
                  v-if="canDelete(m)"
                  :content="`确定删除「${modelDisplayName(m)}」？该操作不可恢复。`"
                  :confirm-btn="{ content: '删除', theme: 'danger' }"
                  cancel-btn="取消"
                  placement="bottom-right"
                  @confirm="removeModel(m)"
                >
                  <t-tooltip content="删除" placement="top">
                    <t-button
                      theme="danger"
                      shape="square"
                      variant="text"
                      size="small"
                      class="model-card__action-btn model-card__delete"
                      @click.stop
                    >
                      <template #icon><t-icon name="delete" /></template>
                    </t-button>
                  </t-tooltip>
                </t-popconfirm>
              </div>
            </div>
            <p class="model-card__subtitle">
              <span>{{ providerChipLabel(m, providers) }}</span>
              <template v-if="m.type === 'Embedding' && m.parameters?.dimensions">
                <span class="model-card__sep">·</span>
                <span>{{ m.parameters.dimensions }} 维</span>
              </template>
              <template v-if="m.type === 'VLLM' && m.is_default">
                <span class="model-card__sep">·</span>
                <span>聊天图片默认</span>
              </template>
            </p>
          </div>
        </div>

        <button type="button" class="model-card model-card--add" @click="openAddDialog">
          <span class="model-card--add__icon" aria-hidden="true">
            <t-icon name="add" />
          </span>
          <span class="model-card--add__label">添加模型</span>
        </button>
      </div>
    </t-loading>

    <ModelEditorDrawer
      v-model:visible="drawerVisible"
      :editing="editing"
      :providers="providers"
      :default-type="defaultTypeForCreate"
      @saved="load"
    />
    <ModelDebugDrawer v-model:visible="debugVisible" :models="models" />
  </div>
</template>

<style scoped>
.model-settings {
  width: 100%;
  height: 100%;
  overflow-y: auto;
  padding: 20px 28px;
  box-sizing: border-box;
}

.section-header {
  margin-bottom: 16px;
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

.section-header__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.model-test-trigger {
  flex-shrink: 0;
  padding-left: 0;
  padding-right: 0;
  font-weight: 600;
}

.model-test-trigger:hover,
.model-test-trigger:focus,
.model-test-trigger.t-is-active,
.model-test-trigger:active {
  background-color: transparent !important;
  color: var(--td-brand-color-hover);
}

.model-list-loading {
  min-height: 120px;
}

.model-type-tabs {
  margin-bottom: 16px;
}

.model-type-tabs :deep(.t-tabs__content) {
  display: none;
}

.model-type-tabs :deep(.t-tabs__nav-item) {
  font-size: 13px;
}

.model-type-tabs :deep(.t-tabs__nav-item-wrapper) {
  padding: 0 12px;
  margin: 0;
}

.model-type-tabs :deep(.t-tabs__operations) {
  display: none;
}

.model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

.model-grid .model-card--add {
  width: 100%;
  min-height: 68px;
}

.model-card {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--td-component-stroke);
  border-radius: 10px;
  background: var(--td-bg-color-container);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease;
  min-width: 0;
  text-align: left;
}

.model-card--clickable {
  cursor: pointer;
}

.model-card--clickable:hover {
  border-color: var(--td-brand-color-3, var(--td-brand-color));
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.06);
}

.model-card--clickable:focus-visible {
  outline: 2px solid var(--td-brand-color);
  outline-offset: 2px;
}

.model-card--builtin {
  background: var(--td-bg-color-secondarycontainer);
}

.model-card--builtin.model-card--clickable:hover {
  border-color: var(--td-component-stroke);
  box-shadow: none;
}

.model-card--add {
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-style: dashed;
  background: transparent;
  color: var(--td-text-color-placeholder);
  cursor: pointer;
  font: inherit;
}

.model-card--add:hover,
.model-card--add:focus-visible {
  color: var(--td-brand-color);
  border-color: var(--td-brand-color);
  background: color-mix(in srgb, var(--td-brand-color) 6%, transparent);
  box-shadow: none;
}

.model-card--add:focus-visible {
  outline: 2px solid var(--td-brand-color);
  outline-offset: 2px;
}

.model-card--add__icon {
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

.model-card--add__label {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
}

.model-card__badge {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
  background: rgba(0, 82, 217, 0.1);
  color: #0052d9;
}

.model-card--embedding .model-card__badge {
  background: rgba(98, 53, 187, 0.1);
  color: #6235bb;
}

.model-card--rerank .model-card__badge {
  background: rgba(184, 92, 0, 0.1);
  color: #b85c00;
}

.model-card--vllm .model-card__badge {
  background: rgba(201, 62, 62, 0.1);
  color: #c93e3e;
}

.model-card--asr .model-card__badge {
  background: rgba(17, 128, 83, 0.1);
  color: #118053;
}

.model-card__body {
  flex: 1;
  min-width: 0;
}

.model-card__header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.model-card__title {
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

.model-card__lock {
  flex-shrink: 0;
  display: flex;
  color: var(--td-text-color-placeholder);
  font-size: 14px;
}

.model-card__actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 2px;
}

.model-card__action-btn {
  color: var(--td-text-color-secondary);
}

.model-card__delete:hover {
  color: var(--td-error-color);
}

.model-card__subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--td-text-color-placeholder);
  line-height: 1.4;
}

.model-card__sep {
  margin: 0 2px;
}
</style>
