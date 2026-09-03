<script setup lang="ts">
import { computed } from 'vue'
import {
  metricScoreClass,
  primaryBarItems,
  squadCountPair,
  formatMetricValue,
} from '@/utils/evalMetrics'

const props = defineProps<{
  summary: Record<string, unknown> | null | undefined
}>()

const bars = computed(() => primaryBarItems(props.summary).filter((i) => i.value >= 0 && i.value <= 1.0001))
const counts = computed(() => squadCountPair(props.summary))
const countTotal = computed(() => (counts.value ? counts.value.hasAns + counts.value.noAns : 0))
</script>

<template>
  <div v-if="bars.length || counts" class="metric-bars">
    <div v-if="counts && countTotal > 0" class="count-row">
      <div class="count-label">题型构成</div>
      <div class="count-track">
        <div
          class="count-has"
          :style="{ width: `${(counts.hasAns / countTotal) * 100}%` }"
          :title="`HasAns ${counts.hasAns}`"
        />
        <div
          class="count-no"
          :style="{ width: `${(counts.noAns / countTotal) * 100}%` }"
          :title="`NoAns ${counts.noAns}`"
        />
      </div>
      <div class="count-legend">
        <span>HasAns {{ counts.hasAns }}</span>
        <span>NoAns {{ counts.noAns }}</span>
      </div>
    </div>
    <div v-for="item in bars" :key="item.key" class="bar-row">
      <div class="bar-meta">
        <span>{{ item.label }}</span>
        <span class="bar-value">{{ formatMetricValue(item.value) }}</span>
      </div>
      <div class="bar-track">
        <div
          class="bar-fill"
          :class="metricScoreClass(item.value)"
          :style="{ width: `${Math.min(Math.max(item.value, 0), 1) * 100}%` }"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.metric-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bar-row,
.count-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bar-meta,
.count-legend,
.count-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--td-text-color-secondary);
}

.bar-value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--td-text-color-primary);
}

.bar-track,
.count-track {
  height: 8px;
  border-radius: 4px;
  background: var(--td-bg-color-component, #f3f3f3);
  overflow: hidden;
  display: flex;
}

.bar-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--td-error-color-5, #d54941);
}

.bar-fill.score-mid {
  background: var(--td-warning-color-5, #e37318);
}

.bar-fill.score-high {
  background: var(--td-success-color-5, #2ba471);
}

.count-has {
  background: var(--td-brand-color, #0052d9);
}

.count-no {
  background: var(--td-gray-color-5, #9b9b9b);
}
</style>
