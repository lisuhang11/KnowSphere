<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  select: [question: string]
}>()

const DEFAULT_QUESTIONS = [
  '这份文档的核心结论是什么？',
  '请总结知识库中的关键信息',
  '有哪些需要注意的风险点？',
  '能否列出主要章节要点？',
  '这个问题在文档里怎么说的？',
  '请用简洁语言解释这个概念',
]

const questions = ref([...DEFAULT_QUESTIONS])

function shuffleQuestions() {
  const next = [...DEFAULT_QUESTIONS]
  for (let i = next.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[next[i], next[j]] = [next[j], next[i]]
  }
  questions.value = next
}

function onSelect(question: string) {
  emit('select', question)
}
</script>

<template>
  <div class="suggested-questions-container has-questions">
    <div class="suggested-questions-inner">
      <div class="suggested-questions-title-row">
        <p class="suggested-questions-caption">
          <span class="suggested-questions-title">试试这些问题</span>
          <button type="button" class="suggested-questions-refresh" title="换一批" @click="shuffleQuestions">
            <t-icon name="refresh" />
          </button>
        </p>
      </div>
      <div class="suggested-questions-grid">
        <div
          v-for="(question, index) in questions"
          :key="`${question}-${index}`"
          class="suggested-question-card"
          @click="onSelect(question)"
        >
          <span class="suggested-question-text">{{ question }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.suggested-questions-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 16px;
  max-width: 860px;
  margin: 0 auto;
  width: 100%;
  min-height: 64px;
}

.suggested-questions-inner {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  width: 100%;
  animation: contentFadeIn 0.3s ease-out;
}

.suggested-questions-title-row {
  margin-bottom: 12px;
  text-align: center;
}

.suggested-questions-caption {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  line-height: 1;
}

.suggested-questions-title {
  font-size: 13px;
  color: var(--td-text-color-placeholder);
  font-weight: 400;
}

.suggested-questions-refresh {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--td-text-color-placeholder);
  cursor: pointer;
}

.suggested-questions-refresh:hover {
  color: var(--td-brand-color);
  background: var(--td-bg-color-secondarycontainer);
}

.suggested-questions-grid {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  width: 100%;
}

.suggested-question-card {
  display: inline-flex;
  align-items: center;
  min-width: 0;
  max-width: 100%;
  padding: 8px 14px;
  border-radius: 10px;
  border: 1px solid var(--td-component-stroke);
  background: var(--td-bg-color-container);
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.suggested-question-card:hover {
  border-color: color-mix(in srgb, var(--td-text-color-primary) 10%, var(--td-component-stroke));
  background: color-mix(in srgb, var(--td-text-color-primary) 4%, var(--td-bg-color-container));
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
}

.suggested-question-card:active {
  background: color-mix(in srgb, var(--td-text-color-primary) 6%, var(--td-bg-color-container));
}

.suggested-question-text {
  font-size: 13px;
  line-height: 1.5;
  color: var(--td-text-color-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@keyframes contentFadeIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
