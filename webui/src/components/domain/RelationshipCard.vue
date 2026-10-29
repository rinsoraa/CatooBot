<script setup lang="ts">
/**
 * 关系卡（W5 §21-§22）：四个 0-1 指标只读展示；关系由机器人自身经历累积，
 * WebUI 没有任何写入入口。
 */
import { computed } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import { relationLabel, type RelationshipRow } from '@/types/social'

const props = defineProps<{ relationship: RelationshipRow | null }>()

interface Metric {
  key: string
  label: string
  value: number
}

const metrics = computed<Metric[]>(() => {
  const row = props.relationship
  if (!row) return []
  return [
    { key: 'trust', label: '信任', value: row.trust },
    { key: 'familiarity', label: '熟悉', value: row.familiarity },
    { key: 'closeness', label: '亲密', value: row.closeness },
    { key: 'social_comfort', label: '社交舒适', value: row.social_comfort },
  ]
})

const relationText = computed(() =>
  props.relationship ? relationLabel(props.relationship.relation_type) : '',
)

function display(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : '—'
}

function width(value: number): string {
  if (!Number.isFinite(value)) return '0%'
  const clamped = Math.min(1, Math.max(0, value))
  return `${Math.round(clamped * 100)}%`
}

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="cb-relationship-card" data-test="relationship-card">
    <EmptyState
      v-if="!relationship"
      title="尚无关系记录"
      description="对方还没有进入关系状态：需要真实互动与共同经历累积后才会出现。"
    />

    <template v-else>
      <dl class="cb-relationship-card__facts">
        <div class="cb-relationship-card__fact">
          <dt>关系类型</dt>
          <dd data-test="relationship-type">{{ relationText }}</dd>
        </div>
        <div class="cb-relationship-card__fact">
          <dt>互动次数</dt>
          <dd data-test="relationship-interactions">{{ relationship.interaction_count }}</dd>
        </div>
        <div class="cb-relationship-card__fact">
          <dt>最近互动</dt>
          <dd data-test="relationship-last-interaction">
            {{ formatTime(relationship.last_interaction_at) }}
          </dd>
        </div>
      </dl>

      <ul class="cb-relationship-card__metrics">
        <li
          v-for="metric in metrics"
          :key="metric.key"
          class="cb-relationship-card__metric"
          :data-metric="metric.key"
        >
          <div class="cb-relationship-card__metric-head">
            <span class="cb-caption">{{ metric.label }}</span>
            <span class="cb-relationship-card__value" data-test="relationship-metric-value">
              {{ display(metric.value) }}
            </span>
          </div>
          <div
            class="cb-relationship-card__track"
            role="meter"
            :aria-label="metric.label"
            :aria-valuenow="metric.value"
            aria-valuemin="0"
            aria-valuemax="1"
          >
            <span
              class="cb-relationship-card__bar"
              data-test="relationship-metric-bar"
              :style="{ width: width(metric.value) }"
            />
          </div>
        </li>
      </ul>

      <p class="cb-relationship-card__readonly" data-test="relationship-readonly">
        只读：关系状态由互动历史累积，WebUI 不提供修改。
      </p>
    </template>
  </div>
</template>

<style scoped>
.cb-relationship-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-relationship-card__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.cb-relationship-card__fact dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-relationship-card__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-relationship-card__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-relationship-card__metric-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-2);
}

.cb-relationship-card__value {
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-relationship-card__track {
  height: 6px;
  margin-top: var(--cb-space-1);
  border-radius: 999px;
  background: var(--cb-bg-soft);
  border: 1px solid var(--cb-border);
  overflow: hidden;
}

.cb-relationship-card__bar {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--cb-primary);
}

.cb-relationship-card__readonly {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}
</style>
