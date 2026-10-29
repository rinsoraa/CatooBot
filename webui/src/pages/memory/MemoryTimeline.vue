<script setup lang="ts">
/**
 * 记忆时间线（W5 §108）：按日期分组，条目展示摘要 + 类型 + person，
 * Expert 模式下补 episode_key。limit 走 URL（?limit=），空态与重试齐全。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import MemoryCard from '@/components/domain/MemoryCard.vue'
import { useMemoryStore } from '@/stores/memory'
import type { MemoryRow } from '@/types/domain'

const DEFAULT_LIMIT = 200
const LIMIT_OPTIONS = [50, 100, 200, 500]

const route = useRoute()
const router = useRouter()
const store = useMemoryStore()

const expert = ref(false)
const loading = ref(false)

function queryText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
  return ''
}

const limit = computed(() => {
  const raw = Number(queryText(route.query.limit))
  return LIMIT_OPTIONS.includes(raw) ? raw : DEFAULT_LIMIT
})

watch(
  () => route.fullPath,
  () => {
    void loadTimeline()
  },
  { immediate: true },
)

async function loadTimeline(): Promise<void> {
  store.clear()
  loading.value = true
  try {
    await store.loadTimeline(limit.value)
  } finally {
    loading.value = false
  }
}

function reload(): void {
  void loadTimeline()
}

function onLimitChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  const query: LocationQueryRaw = { ...route.query }
  if (value === DEFAULT_LIMIT) delete query.limit
  else query.limit = String(value)
  void router.push({ query })
}

function dateKey(value: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '未知时间'
  const date = new Date(value * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

interface TimelineGroup {
  date: string
  items: MemoryRow[]
}

const groups = computed<TimelineGroup[]>(() => {
  const map = new Map<string, MemoryRow[]>()
  for (const item of store.timeline) {
    const key = dateKey(item.created_at)
    const bucket = map.get(key)
    if (bucket) bucket.push(item)
    else map.set(key, [item])
  }
  return [...map.entries()].map(([date, items]) => ({ date, items }))
})

const countLabel = computed(() => `共 ${store.timeline.length} 条`)
</script>

<template>
  <div class="cb-memory-timeline" data-test="memory-timeline">
    <section class="cb-memory-timeline__panel cb-card">
      <SectionHeader title="时间线" description="按创建日期分组；limit 会写进地址栏。" />

      <div class="cb-memory-timeline__controls">
        <label class="cb-memory-timeline__field">
          <span class="cb-caption">条数上限</span>
          <select
            class="cb-memory-timeline__select"
            data-test="timeline-limit"
            :value="String(limit)"
            @change="onLimitChange"
          >
            <option v-for="value in LIMIT_OPTIONS" :key="value" :value="String(value)">
              {{ value }} 条
            </option>
          </select>
        </label>

        <label class="cb-memory-timeline__expert">
          <input
            type="checkbox"
            :checked="expert"
            data-test="timeline-expert"
            @change="expert = ($event.target as HTMLInputElement).checked"
          />
          专家模式
        </label>

        <span class="cb-memory-timeline__count" data-test="timeline-count">{{ countLabel }}</span>
      </div>
    </section>

    <ErrorState v-if="store.error" :message="store.error" @retry="reload" />

    <LoadingState v-else-if="loading && store.timeline.length === 0" label="正在读取时间线…" :rows="5" />

    <EmptyState
      v-else-if="store.timeline.length === 0"
      title="暂无记忆时间线"
      description="时间线按创建时间展示记忆；当前没有可展示的条目。"
    />

    <template v-else>
      <section
        v-for="group in groups"
        :key="group.date"
        class="cb-memory-timeline__group"
        data-test="timeline-group"
      >
        <h2 class="cb-memory-timeline__date" data-test="timeline-date">{{ group.date }}</h2>
        <ul class="cb-memory-timeline__list">
          <li v-for="item in group.items" :key="item.memory_id" data-test="timeline-item">
            <MemoryCard :memory="item" :expert="expert" />
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.cb-memory-timeline {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-timeline__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-timeline__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--cb-space-4);
}

.cb-memory-timeline__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 140px;
}

.cb-memory-timeline__select {
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-memory-timeline__expert {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  cursor: pointer;
}

.cb-memory-timeline__count {
  margin-left: auto;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  font-variant-numeric: tabular-nums;
}

.cb-memory-timeline__group {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding-left: var(--cb-space-3);
  border-left: 2px solid var(--cb-border-strong);
}

.cb-memory-timeline__date {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  font-variant-numeric: tabular-nums;
}

.cb-memory-timeline__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
</style>
