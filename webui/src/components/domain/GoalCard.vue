<script setup lang="ts">
/**
 * 目标卡（W5 §15、§145）：标题 / 状态 / 优先级 / 进度 / 当前步骤 / 关联承诺；
 * goal_id 与 dedupe_key 只在 Expert 折叠区出现。
 */
import { computed } from 'vue'

import type { WorldGoalRow } from '@/types/domain'

const props = defineProps<{ goal: WorldGoalRow }>()

const STATUS_LABELS: Record<string, string> = {
  pending: '待开始',
  active: '进行中',
  blocked: '受阻',
  completed: '已完成',
  cancelled: '已取消',
  expired: '已过期',
}

function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    const parts = value.map((item) => text(item, '')).filter((part) => part !== '')
    return parts.length > 0 ? parts.join('、') : fallback
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['summary', 'description', 'label', 'name', 'title', 'action_instance_id', 'id']) {
      const candidate = record[key]
      if (candidate !== undefined && candidate !== null && candidate !== '') {
        return text(candidate, fallback)
      }
    }
  }
  return fallback
}

const titleText = computed(() => text(props.goal.title, '未命名目标'))
const statusText = computed(() => {
  const status = props.goal.status
  if (!status) return '—'
  return STATUS_LABELS[status] ?? status
})
const priorityText = computed(() => text(props.goal.priority))
const stepText = computed(() => text(props.goal.current_step))
const commitmentText = computed(() => text(props.goal.target_commitment))
const goalIdText = computed(() => text(props.goal.goal_id))
const dedupeText = computed(() => text(props.goal.dedupe_key))

const percent = computed<number | null>(() => {
  const raw = props.goal.progress
  if (!Number.isFinite(raw)) return null
  return Math.round(Math.min(1, Math.max(0, raw)) * 100)
})
const percentText = computed(() => (percent.value === null ? '—' : `${percent.value}%`))
const progressWidth = computed(() => `${percent.value ?? 0}%`)
</script>

<template>
  <article class="cb-card cb-goal-card" data-test="goal-card">
    <header class="cb-goal-card__head">
      <h3 class="cb-goal-card__title" data-test="goal-title">{{ titleText }}</h3>
      <span class="cb-goal-card__status" data-test="goal-status">{{ statusText }}</span>
    </header>

    <dl class="cb-goal-card__facts">
      <div class="cb-goal-card__fact">
        <dt class="cb-caption">优先级</dt>
        <dd data-test="goal-priority">{{ priorityText }}</dd>
      </div>
      <div class="cb-goal-card__fact">
        <dt class="cb-caption">当前步骤</dt>
        <dd data-test="goal-step">{{ stepText }}</dd>
      </div>
      <div class="cb-goal-card__fact">
        <dt class="cb-caption">关联承诺</dt>
        <dd data-test="goal-commitment">{{ commitmentText }}</dd>
      </div>
    </dl>

    <div class="cb-goal-card__progress" data-test="goal-progress">
      <span class="cb-caption">进度</span>
      <div
        class="cb-goal-card__bar"
        role="progressbar"
        :aria-valuenow="percent ?? 0"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="目标进度"
      >
        <span class="cb-goal-card__fill" :style="{ width: progressWidth }" data-test="goal-bar" />
      </div>
      <span class="cb-goal-card__percent" data-test="goal-percent">{{ percentText }}</span>
    </div>

    <details class="cb-goal-card__expert">
      <summary class="cb-caption">Expert 标识</summary>
      <p class="cb-goal-card__expert-text" data-test="goal-expert">
        goal_id: {{ goalIdText }} · dedupe_key: {{ dedupeText }}
      </p>
    </details>
  </article>
</template>

<style scoped>
.cb-goal-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-goal-card__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-goal-card__title {
  color: var(--cb-text);
  font-size: var(--cb-text-lg);
  overflow-wrap: anywhere;
}

.cb-goal-card__status {
  flex: none;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-goal-card__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-goal-card__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-goal-card__progress {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.cb-goal-card__bar {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: var(--cb-border);
  overflow: hidden;
}

.cb-goal-card__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--cb-primary);
}

.cb-goal-card__percent {
  flex: none;
  min-width: 4ch;
  text-align: right;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-variant-numeric: tabular-nums;
}

.cb-goal-card__expert summary {
  cursor: pointer;
}

.cb-goal-card__expert-text {
  margin-top: var(--cb-space-2);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-family: var(--cb-font-mono);
  overflow-wrap: anywhere;
}
</style>
