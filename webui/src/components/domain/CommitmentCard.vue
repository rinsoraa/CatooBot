<script setup lang="ts">
/**
 * 承诺卡（W5 §23、§107）：字段全部来自后端投影；状态用 `commitmentLabel`，
 * 对真实 Core 中存在而标签表暂缺的状态给中文兜底，绝不猜测。
 */
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { commitmentLabel, type CommitmentRow } from '@/types/social'

const props = defineProps<{ commitment: CommitmentRow }>()

const router = useRouter()

/** 真实 Core 状态中标签表未覆盖的取值（页面仍显示中文，而不是裸枚举）。 */
const EXTRA_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  scheduled: '已排期',
  in_progress: '进行中',
  completed: '已履行',
  declined: '已拒绝',
  expired: '已过期',
}

const GOAL_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  active: '进行中',
  blocked: '受阻',
  completed: '已完成',
  cancelled: '已取消',
  expired: '已过期',
}

function statusText(status: string): string {
  const label = commitmentLabel(status)
  return label === status ? (EXTRA_STATUS_LABELS[status] ?? label) : label
}

/** 真实 Core 的 strength 是字符串枚举（explicit / soft）；类型声明为 number，渲染时两者都兼容。 */
const STRENGTH_LABELS: Record<string, string> = {
  explicit: '明确',
  soft: '软性',
}

function strengthText(value: number | string): string {
  if (value === null || value === undefined || value === '') return '—'
  const key = String(value)
  return STRENGTH_LABELS[key] ?? key
}

const status = computed(() => statusText(props.commitment.status))
const goalStatus = computed(() => {
  const value = props.commitment.goal_status
  if (!value) return '未关联'
  return GOAL_STATUS_LABELS[value] ?? value
})

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

function open(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('a')) return
  void router.push(`/social/commitments/${encodeURIComponent(props.commitment.commitment_id)}`)
}
</script>

<template>
  <article
    class="cb-commitment-card"
    data-test="commitment-card"
    :data-commitment="commitment.commitment_id"
    @click="open"
  >
    <header class="cb-commitment-card__header">
      <RouterLink
        class="cb-commitment-card__summary"
        data-test="commitment-summary"
        :to="`/social/commitments/${encodeURIComponent(commitment.commitment_id)}`"
      >
        {{ commitment.summary || '（无摘要）' }}
      </RouterLink>
      <span class="cb-commitment-card__status" data-test="commitment-status">{{ status }}</span>
    </header>

    <p class="cb-commitment-card__person" data-test="commitment-person">
      人物：{{ commitment.person_name || commitment.person_id }}
    </p>

    <dl class="cb-commitment-card__facts">
      <div class="cb-commitment-card__fact">
        <dt>强度</dt>
        <dd data-test="commitment-strength">{{ strengthText(commitment.strength) }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>优先级</dt>
        <dd data-test="commitment-priority">{{ commitment.priority }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>时间提示</dt>
        <dd data-test="commitment-time-hint">{{ commitment.time_hint || '—' }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>最早</dt>
        <dd data-test="commitment-earliest">{{ formatTime(commitment.earliest_at) }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>截止</dt>
        <dd data-test="commitment-due">{{ formatTime(commitment.due_at) }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>创建时间</dt>
        <dd data-test="commitment-created">{{ formatTime(commitment.created_at) }}</dd>
      </div>
      <div class="cb-commitment-card__fact">
        <dt>关联 Goal</dt>
        <dd data-test="commitment-goal-status">{{ goalStatus }}</dd>
      </div>
    </dl>
  </article>
</template>

<style scoped>
.cb-commitment-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
  cursor: pointer;
}

.cb-commitment-card:hover {
  border-color: var(--cb-primary);
}

.cb-commitment-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-commitment-card__summary {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-commitment-card__summary:hover {
  color: var(--cb-primary-strong);
}

.cb-commitment-card__status {
  flex: none;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-commitment-card__person {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-commitment-card__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-commitment-card__fact {
  min-width: 0;
}

.cb-commitment-card__fact dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-commitment-card__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}
</style>
