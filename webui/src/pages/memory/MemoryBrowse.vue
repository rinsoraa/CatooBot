<script setup lang="ts">
/**
 * 记忆浏览（W5 §27/§28）：筛选写 URL、列表渲染、上一页/下一页（offset 走 URL）。
 *
 * 地址栏是唯一状态源：筛选组件 emit 后只 push 查询串，本页 watch 路由再请求。
 * keyword 在筛选组件内 300ms 防抖，因此打字只在停顿后触发一次请求。
 * `total` 为 null（检索态后端不返回总数）时显示「还有更多」，绝不编造数字。
 */
import { computed, watch } from 'vue'
import { RouterLink, useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import MemoryCard from '@/components/domain/MemoryCard.vue'
import MemoryFilters from '@/components/domain/MemoryFilters.vue'
import { useMemoryStore } from '@/stores/memory'
import type { MemoryFilters as MemoryFilterModel } from '@/types/domain'

const DEFAULT_LIMIT = 20
const LIMIT_OPTIONS = [10, 20, 50, 100]

const route = useRoute()
const router = useRouter()
const store = useMemoryStore()

function queryText(value: unknown): string {
  if (typeof value === 'string') return value
  if (Array.isArray(value) && typeof value[0] === 'string') return value[0]
  return ''
}

const filters = computed<MemoryFilterModel>(() => {
  const limitRaw = Number(queryText(route.query.limit))
  const limit = LIMIT_OPTIONS.includes(limitRaw) ? limitRaw : DEFAULT_LIMIT
  const offsetRaw = Number(queryText(route.query.offset))
  const offset = Number.isFinite(offsetRaw) && offsetRaw > 0 ? Math.floor(offsetRaw) : 0
  return {
    q: queryText(route.query.q) || undefined,
    mode: queryText(route.query.mode) || 'hybrid',
    person: queryText(route.query.person) || undefined,
    category: queryText(route.query.category) || undefined,
    layer: queryText(route.query.layer) || undefined,
    status: queryText(route.query.status) || 'active',
    limit,
    offset,
  }
})

/** 是否处于「筛选」状态：用于区分「无记忆」与「无匹配结果」两种空态。 */
const hasFilters = computed(() => {
  const value = filters.value
  return Boolean(
    value.q || value.person || value.category || value.layer || value.status !== 'active',
  )
})

watch(
  () => route.fullPath,
  () => {
    void store.load(filters.value)
  },
  { immediate: true },
)

function reload(): void {
  void store.load(filters.value)
}

function toQuery(next: MemoryFilterModel): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  const q = next.q?.trim()
  if (q) query.q = q
  if (next.mode && next.mode !== 'hybrid') query.mode = next.mode
  if (next.person?.trim()) query.person = next.person.trim()
  if (next.category) query.category = next.category
  if (next.layer) query.layer = next.layer
  if (next.status && next.status !== 'active') query.status = next.status
  if (next.limit && next.limit !== DEFAULT_LIMIT) query.limit = String(next.limit)
  return query
}

/** 筛选变化重置回第一页（offset 不出现在新查询串里）。 */
function applyFilters(next: MemoryFilterModel): void {
  void router.push({ query: toQuery(next) })
}

const canGoPrev = computed(() => store.offset > 0)
const canGoNext = computed(() => {
  if (store.total !== null) return store.offset + store.limit < store.total
  // total 未知时，用「本页是否装满」判断还有更多（不编造总数）。
  return store.items.length >= store.limit
})

function goPage(step: number): void {
  const nextOffset = Math.max(0, store.offset + step * store.limit)
  const query: LocationQueryRaw = { ...route.query }
  if (nextOffset > 0) query.offset = String(nextOffset)
  else delete query.offset
  void router.push({ query })
}

const totalLabel = computed(() => {
  if (store.total === null) return '还有更多'
  return `共 ${store.total} 条`
})

const rangeLabel = computed(() => {
  if (store.items.length === 0) return ''
  return `第 ${store.offset + 1}–${store.offset + store.items.length} 条`
})
</script>

<template>
  <div class="cb-memory-browse" data-test="memory-browse">
    <section class="cb-memory-browse__panel cb-card">
      <SectionHeader title="筛选" description="筛选条件会写进地址栏，可直接分享或刷新。" />
      <MemoryFilters :model="filters" @update:model="applyFilters" />
    </section>

    <section class="cb-memory-browse__panel cb-card">
      <SectionHeader title="记忆列表" description="按创建时间倒序分页；点击卡片查看详情与出处。" />

      <ErrorState v-if="store.error" :message="store.error" @retry="reload" />

      <LoadingState
        v-else-if="store.loading && store.items.length === 0"
        label="正在读取记忆…"
        :rows="4"
      />

      <EmptyState
        v-else-if="store.items.length === 0 && hasFilters"
        title="没有匹配的记忆"
        description="当前筛选条件下没有找到记忆，试着放宽关键词或清除筛选。"
      />

      <EmptyState
        v-else-if="store.items.length === 0"
        title="还没有长期记忆"
        description="角色还没有形成可浏览的长期记忆；当对话或经历被写入后会出现在这里。"
      />

      <template v-else>
        <ul class="cb-memory-browse__list" data-test="memory-list">
          <li v-for="item in store.items" :key="item.memory_id" class="cb-memory-browse__item">
            <RouterLink
              class="cb-memory-browse__link"
              :to="{ name: 'memory-detail', params: { memoryId: item.memory_id } }"
            >
              <MemoryCard :memory="item" />
            </RouterLink>
          </li>
        </ul>

        <div class="cb-memory-browse__footer">
          <span class="cb-memory-browse__range" data-test="memory-range">{{ rangeLabel }}</span>
          <span class="cb-memory-browse__total" data-test="memory-total">{{ totalLabel }}</span>
          <div class="cb-memory-browse__pager">
            <button
              type="button"
              class="cb-memory-browse__button"
              data-test="memory-prev"
              :disabled="!canGoPrev"
              @click="goPage(-1)"
            >
              上一页
            </button>
            <button
              type="button"
              class="cb-memory-browse__button"
              data-test="memory-next"
              :disabled="!canGoNext"
              @click="goPage(1)"
            >
              下一页
            </button>
          </div>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.cb-memory-browse {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-browse__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-browse__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-memory-browse__link {
  display: block;
  color: inherit;
  text-decoration: none;
}

.cb-memory-browse__link:hover {
  text-decoration: none;
}

.cb-memory-browse__link:hover :deep(.cb-memory-card) {
  border-color: var(--cb-primary);
}

.cb-memory-browse__footer {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
  padding-top: var(--cb-space-2);
  border-top: 1px solid var(--cb-border);
}

.cb-memory-browse__range,
.cb-memory-browse__total {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  font-variant-numeric: tabular-nums;
}

.cb-memory-browse__pager {
  display: flex;
  gap: var(--cb-space-2);
  margin-left: auto;
}

.cb-memory-browse__button {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-memory-browse__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-memory-browse__button:disabled {
  color: var(--cb-text-faint);
  cursor: not-allowed;
}
</style>
