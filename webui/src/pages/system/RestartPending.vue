<script setup lang="ts">
/**
 * 等待重启（W4 §90）：列出「已保存、但运行期还没生效」的键。
 *
 * 每个键显示：中文名称、保存状态（overrides.yaml 里已写入）、当前运行值
 * （effective 里的真实值）与原因。不假装知道旧值——只呈现后端给的事实。
 */
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useConfigStore } from '@/stores/config'
import { SOURCE_LABELS } from '@/types/config'

const store = useConfigStore()

onMounted(() => {
  void Promise.all([store.loadSchema(), store.loadEffective(), store.loadRestartPending()])
})

function formatValue(value: unknown): string {
  if (value === undefined || value === null) return '（无值）'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (typeof value === 'string') return value === '' ? '（空字符串）' : value
  if (typeof value === 'number') return String(value)
  try {
    return JSON.stringify(value) ?? '（无值）'
  } catch {
    return String(value)
  }
}

interface PendingRow {
  key: string
  label: string
  savedText: string
  runningText: string
  reason: string
}

const rows = computed<PendingRow[]>(() =>
  store.restartPending.pending.map((key) => {
    const effective = store.effectiveByKey.get(key)
    const meta = store.fields.find((field) => field.key === key)
    const source = effective?.source ?? 'default'
    const sourceLabel = SOURCE_LABELS[source] ?? source
    return {
      key,
      label: effective?.label ?? meta?.label ?? key,
      savedText:
        source === 'overrides' ? '已保存（等待重启）' : `等待重启（当前来源：${sourceLabel}）`,
      runningText: formatValue(effective?.value),
      reason: '该字段仅在启动时读取，重启 CatooBot 后新值才会生效。',
    }
  }),
)

const since = computed(() => store.restartPending.since)
const sinceText = computed(() => {
  const stamp = since.value
  if (!stamp) return ''
  return `自 ${new Date(stamp * 1000).toLocaleString('zh-CN', { hour12: false })} 起等待重启`
})
</script>

<template>
  <div class="cb-restart-pending" data-test="restart-pending-page">
    <SectionHeader
      title="等待重启"
      description="这些设置已经写入 overrides.yaml，但要等 CatooBot 重启后才会真正生效。"
    />

    <ErrorState v-if="store.error" :message="store.error" @retry="store.loadRestartPending()" />

    <LoadingState v-if="store.loading && store.effective.length === 0" label="正在读取等待重启项…" :rows="3" />

    <EmptyState
      v-else-if="rows.length === 0"
      title="没有等待重启的修改"
      description="当前没有已保存但尚未生效的配置项。"
      data-test="restart-pending-empty"
    >
      <RouterLink class="cb-restart-pending__back" :to="{ name: 'system-settings' }">
        返回设置
      </RouterLink>
    </EmptyState>

    <template v-else>
      <p v-if="sinceText" class="cb-restart-pending__since cb-faint" data-test="restart-pending-since">
        {{ sinceText }}
      </p>
      <ul class="cb-restart-pending__list">
        <li
          v-for="row in rows"
          :key="row.key"
          class="cb-restart-pending__item"
          data-test="restart-pending-item"
          :data-key="row.key"
        >
          <div class="cb-restart-pending__head">
            <span class="cb-restart-pending__label">{{ row.label }}</span>
            <code class="cb-restart-pending__key">{{ row.key }}</code>
          </div>
          <dl class="cb-restart-pending__facts">
            <div class="cb-restart-pending__fact">
              <dt class="cb-caption">保存状态</dt>
              <dd data-test="restart-pending-saved">⚠ {{ row.savedText }}</dd>
            </div>
            <div class="cb-restart-pending__fact">
              <dt class="cb-caption">当前运行值</dt>
              <dd data-test="restart-pending-running">{{ row.runningText }}</dd>
            </div>
          </dl>
          <p class="cb-restart-pending__reason">ⓘ {{ row.reason }}</p>
        </li>
      </ul>
      <div>
        <RouterLink class="cb-restart-pending__back" :to="{ name: 'system-settings' }">
          返回设置
        </RouterLink>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cb-restart-pending {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-restart-pending__since {
  font-size: var(--cb-text-xs);
}

.cb-restart-pending__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-restart-pending__item {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-restart-pending__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--cb-space-2);
}

.cb-restart-pending__label {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
}

.cb-restart-pending__key {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-restart-pending__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.cb-restart-pending__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-restart-pending__reason {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-restart-pending__back {
  align-self: flex-start;
  font-size: var(--cb-text-sm);
}
</style>
