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

<style scoped lang="less">
@import '@/components/css/suggested-questions.less';
</style>
