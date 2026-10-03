<script setup lang="ts">
/**
 * 动作卡（W5 §11-§13、§145）：展示运行中的真实 ActionInstance，
 * progress 是后端的 0-1 进度，不是前端任务队列；null = 当前没有动作。
 */
import { computed } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import type { WorldActionInfo } from '@/types/domain'

const props = defineProps<{ action: WorldActionInfo | null }>()

/** 安全文本：goal_step 在真实 payload 里可能是结构化对象，取常见标签降级显示。 */
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

function formatTime(value: number | null | undefined): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

const progress = computed<number | null>(() => {
  const raw = props.action?.progress
  if (raw === null || raw === undefined || Number.isNaN(raw)) return null
  return Math.min(1, Math.max(0, raw))
})
const percent = computed<number | null>(() =>
  progress.value === null ? null : Math.round(progress.value * 100),
)
const percentText = computed(() => (percent.value === null ? '—' : `${percent.value}%`))
const progressWidth = computed(() => `${percent.value ?? 0}%`)

const nameText = computed(() => text(props.action?.name))
const goalText = computed(() => text(props.action?.goal_id))
const goalStepText = computed(() => text(props.action?.goal_step))
const spaceText = computed(() => text(props.action?.space_id))
const detailText = computed(() => text(props.action?.detail, ''))
const startedText = computed(() => formatTime(props.action?.started_at))
const plannedEndText = computed(() => formatTime(props.action?.planned_end_at))
const definitionText = computed(() => text(props.action?.definition_id))
const reasonText = computed(() => text(props.action?.reason_code))
</script>

<template>
  <section class="cb-card cb-action-card" data-test="action-card">
    <SectionHeader title="当前动作" description="运行中的真实 ActionInstance" />

    <EmptyState
      v-if="action === null"
      title="当前没有正在进行的动作"
      description="角色处于空闲/待机状态时，这里不会显示进度。"
    />

    <template v-else>
      <p class="cb-action-card__note" data-test="action-instance-note">
        这是运行中的真实 ActionInstance，不是前端任务。
      </p>

      <dl class="cb-action-card__facts">
        <div class="cb-action-card__fact">
          <dt class="cb-caption">动作</dt>
          <dd data-test="action-name">{{ nameText }}</dd>
        </div>
        <div class="cb-action-card__fact">
          <dt class="cb-caption">开始时间</dt>
          <dd data-test="action-started">{{ startedText }}</dd>
        </div>
        <div class="cb-action-card__fact">
          <dt class="cb-caption">预计完成</dt>
          <dd data-test="action-planned-end">{{ plannedEndText }}</dd>
        </div>
        <div class="cb-action-card__fact">
          <dt class="cb-caption">空间</dt>
          <dd data-test="action-space">{{ spaceText }}</dd>
        </div>
        <div class="cb-action-card__fact">
          <dt class="cb-caption">关联目标</dt>
          <dd data-test="action-goal">{{ goalText }}</dd>
        </div>
        <div class="cb-action-card__fact">
          <dt class="cb-caption">当前步骤</dt>
          <dd data-test="action-goal-step">{{ goalStepText }}</dd>
        </div>
      </dl>

      <div class="cb-action-card__progress" data-test="action-progress">
        <span class="cb-caption">进度</span>
        <div
          v-if="percent !== null"
          class="cb-action-card__bar"
          role="progressbar"
          :aria-valuenow="percent"
          aria-valuemin="0"
          aria-valuemax="100"
          aria-label="动作进度"
        >
          <span
            class="cb-action-card__fill"
            :style="{ width: progressWidth }"
            data-test="action-progress-bar"
          />
        </div>
        <span class="cb-action-card__percent" data-test="action-percent">{{ percentText }}</span>
      </div>

      <p v-if="detailText" class="cb-action-card__detail" data-test="action-detail">
        {{ detailText }}
      </p>

      <details class="cb-action-card__expert">
        <summary class="cb-caption">Expert 标识</summary>
        <p class="cb-action-card__expert-text" data-test="action-expert">
          definition_id: {{ definitionText }} · reason_code: {{ reasonText }}
        </p>
      </details>
    </template>
  </section>
</template>

<style scoped>
.cb-action-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-action-card__note {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-info);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-info-soft);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-action-card__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3) var(--cb-space-4);
  margin: 0;
}

.cb-action-card__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-action-card__progress {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.cb-action-card__bar {
  flex: 1;
  height: 10px;
  border-radius: 999px;
  background: var(--cb-border);
  overflow: hidden;
}

.cb-action-card__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--cb-primary);
}

.cb-action-card__percent {
  flex: none;
  min-width: 4ch;
  text-align: right;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  font-variant-numeric: tabular-nums;
}

.cb-action-card__detail {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-action-card__expert summary {
  cursor: pointer;
}

.cb-action-card__expert-text {
  margin-top: var(--cb-space-2);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-family: var(--cb-font-mono);
  overflow-wrap: anywhere;
}
</style>
