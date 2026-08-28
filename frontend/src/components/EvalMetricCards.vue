<script setup lang="ts">
import { computed } from 'vue'
import {
  buildMetricCardGroups,
  formatMetricValue,
  hasMetricCards,
  metricScoreClass,
} from '@/utils/evalMetrics'

const props = defineProps<{
  summary: Record<string, unknown> | null | undefined
  sampleCount?: number | null
}>()

const groups = computed(() => buildMetricCardGroups(props.summary))
const showCards = computed(() => hasMetricCards(props.summary))
</script>

<template>
  <div v-if="showCards" class="metric-cards">
    <div v-if="sampleCount != null" class="sample-count">共 {{ sampleCount }} 题</div>
    <section v-for="group in groups" :key="group.id" class="metric-group">
      <h4 class="group-title">{{ group.title }}</h4>
      <div class="card-grid">
        <div
          v-for="item in group.items"
          :key="item.key"
          class="metric-card"
          :class="metricScoreClass(item.value)"
        >
          <div class="metric-value">{{ formatMetricValue(item.value) }}</div>
          <div class="metric-label">{{ item.label }}</div>
        </div>
      </div>
    </section>
  </div>
  <div v-else-if="summary?.phase === 'agent'" class="metric-placeholder">Agent 跑题中，完成后展示指标…</div>
  <div v-else-if="summary?.phase === 'ragas'" class="metric-placeholder ragas-active">
    <span class="ragas-pulse" /> RAGAS 批量打分中，请稍候…
  </div>
  <pre v-else-if="summary" class="json-fallback">{{ JSON.stringify(summary, null, 2) }}</pre>
  <div v-else class="metric-placeholder">暂无指标</div>
</template>

<style scoped>
.metric-cards {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sample-count {
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.metric-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.group-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}

.metric-card {
  border: 1px solid var(--td-component-border, #e7e7e7);
  border-radius: 8px;
  padding: 12px 10px;
  background: var(--td-bg-color-container, #fff);
  text-align: center;
  transition: border-color 0.2s;
}

.metric-card.score-high {
  border-color: var(--td-success-color-5, #2ba471);
  background: color-mix(in srgb, var(--td-success-color-1, #e3f9e9) 40%, transparent);
}

.metric-card.score-mid {
  border-color: var(--td-warning-color-5, #e37318);
  background: color-mix(in srgb, var(--td-warning-color-1, #fff1e9) 40%, transparent);
}

.metric-card.score-low {
  border-color: var(--td-error-color-5, #d54941);
  background: color-mix(in srgb, var(--td-error-color-1, #fff0ed) 35%, transparent);
}

.metric-value {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}

.metric-label {
  margin-top: 4px;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.metric-placeholder {
  padding: 16px;
  border-radius: 8px;
  background: var(--td-gray-bg-color, #f5f5f5);
  font-size: 13px;
  color: var(--td-text-color-secondary);
}

.metric-placeholder.ragas-active {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--td-brand-color, #0052d9);
  background: color-mix(in srgb, var(--td-brand-color-1, #ecf2fe) 50%, transparent);
}

.ragas-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--td-brand-color, #0052d9);
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.1);
  }
}

.json-fallback {
  background: var(--td-gray-bg-color, #f5f5f5);
  padding: 12px;
  border-radius: 6px;
  font-size: 12px;
  overflow: auto;
  max-height: 200px;
  margin: 0;
}
</style>
