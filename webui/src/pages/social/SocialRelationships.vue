<script setup lang="ts">
/**
 * 关系页（W5 §22）：纯只读表格；关系由机器人自身互动历史累积，
 * 页面与接口都不提供任何修改入口。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useSocialStore } from '@/stores/social'
import { relationLabel, type RelationshipRow } from '@/types/social'

const store = useSocialStore()

const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  await store.loadRelationships(50)
  loading.value = false
}

onMounted(() => {
  void load()
})

const rows = computed<RelationshipRow[]>(() => store.relationships)

const METRICS: Array<{ key: keyof RelationshipRow; label: string }> = [
  { key: 'trust', label: '信任' },
  { key: 'familiarity', label: '熟悉' },
  { key: 'closeness', label: '亲密' },
  { key: 'social_comfort', label: '社交舒适' },
]

function metricValue(row: RelationshipRow, key: keyof RelationshipRow): string {
  const value = row[key]
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function metricWidth(row: RelationshipRow, key: keyof RelationshipRow): string {
  const value = row[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) return '0%'
  return `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`
}

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="cb-social-relationships" data-test="social-relationships">
    <SectionHeader title="关系" description="按重要程度列出已建立关系的人物，最多 50 条。" />

    <p class="cb-social-relationships__readonly" data-test="relationship-readonly-note">
      只读：关系状态由机器人与对方的互动历史累积，WebUI 只展示、不修改。
    </p>

    <ErrorState v-if="store.error" :message="store.error" @retry="load()" />

    <LoadingState v-else-if="loading && rows.length === 0" label="正在读取关系…" :rows="4" />

    <EmptyState
      v-else-if="rows.length === 0"
      title="还没有关系记录"
      description="真实互动累积后，关系会出现在这里。"
    />

    <div v-else class="cb-social-relationships__table-wrap">
      <table class="cb-social-relationships__table">
        <caption class="cb-visually-hidden">关系列表（只读）</caption>
        <thead>
          <tr>
            <th scope="col">人物</th>
            <th scope="col">类型</th>
            <th v-for="metric in METRICS" :key="metric.key" scope="col">{{ metric.label }}</th>
            <th scope="col">互动次数</th>
            <th scope="col">最近互动</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.person_id" data-test="relationship-row" :data-person="row.person_id">
            <td>
              <RouterLink
                class="cb-social-relationships__person"
                :to="`/social/users/${encodeURIComponent(row.person_id)}`"
              >
                {{ row.display_name || row.person_id }}
              </RouterLink>
            </td>
            <td data-test="relationship-row-type">{{ relationLabel(row.relation_type) }}</td>
            <td v-for="metric in METRICS" :key="metric.key" :data-metric="metric.key">
              <span class="cb-social-relationships__value" data-test="relationship-row-value">
                {{ metricValue(row, metric.key) }}
              </span>
              <span class="cb-social-relationships__track" aria-hidden="true">
                <span
                  class="cb-social-relationships__bar"
                  :style="{ width: metricWidth(row, metric.key) }"
                />
              </span>
            </td>
            <td data-test="relationship-row-interactions">{{ row.interaction_count }}</td>
            <td data-test="relationship-row-last">{{ formatTime(row.last_interaction_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.cb-social-relationships {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-relationships__readonly {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-social-relationships__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-relationships__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.cb-social-relationships__table th,
.cb-social-relationships__table td {
  padding: var(--cb-space-2) var(--cb-space-3);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  vertical-align: middle;
}

.cb-social-relationships__table th {
  color: var(--cb-text-muted);
  font-weight: 500;
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-social-relationships__person {
  color: var(--cb-text);
  text-decoration: none;
}

.cb-social-relationships__person:hover {
  color: var(--cb-primary-strong);
  text-decoration: underline;
}

.cb-social-relationships__value {
  display: block;
  margin-bottom: 2px;
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-social-relationships__track {
  display: block;
  width: 72px;
  height: 4px;
  border-radius: 999px;
  background: var(--cb-bg-soft);
  border: 1px solid var(--cb-border);
  overflow: hidden;
}

.cb-social-relationships__bar {
  display: block;
  height: 100%;
  background: var(--cb-primary);
}
</style>
