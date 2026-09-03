<script setup lang="ts">
import { SKILL_ICON } from '@/utils/skillMention'
import SkillFilesPanel from './SkillFilesPanel.vue'

const props = defineProps<{
  visible: boolean
  skillName: string
}>()

const emit = defineEmits<{
  'update:visible': [boolean]
}>()

function onClose() {
  emit('update:visible', false)
}
</script>

<template>
  <t-drawer
    :visible="visible"
    :header="false"
    :footer="false"
    :close-btn="false"
    size="880px"
    attach="body"
    :z-index="2600"
    class="skill-files-drawer"
    @update:visible="emit('update:visible', $event)"
  >
    <div class="skill-files-drawer__shell">
      <div class="skill-files-drawer__head">
        <span class="skill-files-drawer__head-icon">
          <t-icon :name="SKILL_ICON" size="16px" />
        </span>
        <div class="skill-files-drawer__head-text">
          <div class="skill-files-drawer__title">{{ props.skillName }}</div>
          <div class="skill-files-drawer__subtitle">技能文件</div>
        </div>
        <button type="button" class="skill-files-drawer__close" aria-label="关闭" @click="onClose">
          <t-icon name="close" size="16px" />
        </button>
      </div>
      <SkillFilesPanel v-if="visible && skillName" class="skill-files-drawer__panel" :skill-name="skillName" />
    </div>
  </t-drawer>
</template>

<style scoped>
.skill-files-drawer__shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.skill-files-drawer__head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
  padding: 14px 18px;
  border-bottom: 1px solid var(--td-component-stroke);
}

.skill-files-drawer__head-icon {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: color-mix(in srgb, #7c3aed 12%, transparent);
  color: #7c3aed;
}

.skill-files-drawer__head-text {
  min-width: 0;
  flex: 1;
}

.skill-files-drawer__title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--td-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.skill-files-drawer__subtitle {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--td-text-color-secondary);
}

.skill-files-drawer__close {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--td-text-color-secondary);
  cursor: pointer;
}

.skill-files-drawer__close:hover {
  background: var(--td-bg-color-container-hover);
  color: var(--td-text-color-primary);
}

.skill-files-drawer__panel {
  flex: 1;
  min-height: 0;
}
</style>

<style>
.t-drawer.skill-files-drawer .t-drawer__body {
  padding: 0 !important;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
