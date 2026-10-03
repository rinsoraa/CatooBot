<script setup lang="ts">
/**
 * 用量页（W4 §64-§65、§112）：纯表格，不做图表，也不估算 token。
 *
 * 过滤条件直接是后端 `days` / `group_by` 参数；tokens 为 0（没有记录到
 * 用量）时显示「—」，绝不按调用次数估算。
 */
import { onMounted, ref } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useAiStore } from '@/stores/ai'
import type { UsageGroupBy, UsageRow } from '@/types/ai'

const aiStore = useAiStore()
const days = ref(aiStore.usageDays)
const groupBy = ref<UsageGroupBy>(aiStore.usageGroupBy)
const loading = ref(false)

const DAYS_OPTIONS = [1, 7, 30] as const
const GROUP_OPTIONS: { value: UsageGroupBy; label: string }[] = [
  { value: 'model', label: '按模型' },
  { value: 'provider', label: '按 Provider' },
  { value: 'purpose', label: '按用途' },
  { value: 'error_type', label: '按错误类型' },
]

async function load(): Promise<void> {
  loading.value = true
  await aiStore.loadUsage(days.value, groupBy.value)
  loading.value = false
}

onMounted(load)

function onDaysChange(event: Event): void {
  days.value = Number((event.target as HTMLSelectElement).value)
  void load()
}

function onGroupChange(event: Event): void {
  groupBy.value = (event.target as HTMLSelectElement).value as UsageGroupBy
  void load()
}

function successCount(row: UsageRow): number {
  return Math.max(0, row.calls - row.failures)
}

/** §112：tokens 为 0 表示没有记录，不估算，显示「—」。 */
function tokenLabel(row: UsageRow): string {
  return row.tokens > 0 ? String(row.tokens) : '—'
}

function latencyLabel(value: number): string {
  if (!value || value <= 0) return '—'
  return `${Math.round(value)} ms`
}
</script>

<template>
  <div class="usage" data-test="ai-usage">
    <PageHeader title="用量" subtitle="按时间与分组维度查看调用、失败与延迟。" />

    <section class="usage__panel cb-card">
      <SectionHeader title="过滤" description="筛选会直接作为请求参数发给后端。" />

      <div class="usage__filters">
        <label class="usage__filter">
          <span class="cb-caption">时间段</span>
          <select :value="days" data-test="usage-days" @change="onDaysChange">
            <option v-for="value in DAYS_OPTIONS" :key="value" :value="value">
              最近 {{ value }} 天
            </option>
          </select>
        </label>

        <label class="usage__filter">
          <span class="cb-caption">分组</span>
          <select :value="groupBy" data-test="usage-group" @change="onGroupChange">
            <option v-for="option in GROUP_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
      </div>
    </section>

    <section class="usage__panel cb-card">
      <SectionHeader title="统计" description="成功 = 请求数 − 失败；tokens 未记录到时为「—」，不做估算。" />

      <ErrorState v-if="aiStore.error" :message="aiStore.error" @retry="load" />
      <LoadingState v-else-if="loading" label="正在读取用量…" :rows="4" />
      <EmptyState
        v-else-if="aiStore.usage.length === 0"
        title="暂无用量数据"
        description="所选时间段内没有记录，或用量记录器尚未启用。"
      />
      <div v-else class="usage__table-wrap">
        <table class="usage__table" data-test="usage-table">
          <thead>
            <tr>
              <th scope="col">Key</th>
              <th scope="col">请求数</th>
              <th scope="col">成功</th>
              <th scope="col">失败</th>
              <th scope="col">429</th>
              <th scope="col">5xx</th>
              <th scope="col">Tokens</th>
              <th scope="col">平均延迟</th>
              <th scope="col">最大延迟</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in aiStore.usage" :key="row.key" data-test="usage-row">
              <th scope="row" class="usage__key">{{ row.key }}</th>
              <td>{{ row.calls }}</td>
              <td>{{ successCount(row) }}</td>
              <td>{{ row.failures }}</td>
              <td>{{ row.rate_limited }}</td>
              <td>{{ row.server_errors }}</td>
              <td data-test="usage-tokens">{{ tokenLabel(row) }}</td>
              <td>{{ latencyLabel(row.avg_latency_ms) }}</td>
              <td>{{ latencyLabel(row.max_latency_ms) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.usage {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.usage__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.usage__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-4);
}

.usage__filter {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 160px;
}

.usage__filter select {
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.usage__table-wrap {
  overflow-x: auto;
}

.usage__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
  font-variant-numeric: tabular-nums;
}

.usage__table th,
.usage__table td {
  padding: var(--cb-space-2) var(--cb-space-3);
  border-bottom: 1px solid var(--cb-border);
  text-align: right;
  white-space: nowrap;
}

.usage__table thead th {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.usage__table th:first-child,
.usage__table td:first-child {
  text-align: left;
}

.usage__table tbody tr:hover {
  background: var(--cb-bg-soft);
}

.usage__key {
  font-family: var(--cb-font-mono);
  font-weight: 400;
  color: var(--cb-text);
  overflow-wrap: anywhere;
}
</style>
