<script setup lang="ts">
/**
 * 用户页（W5 §18）：搜索 + 分页全部以 URL 为准（`?q=`、`?limit=`、`?offset=`），
 * 输入 300ms 防抖；主展示只用显示名，内部 id 留给人物详情的 Expert 区。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import PersonCard from '@/components/domain/PersonCard.vue'
import { useSocialStore } from '@/stores/social'

const route = useRoute()
const router = useRouter()
const store = useSocialStore()

const q = ref('')
const limit = ref(20)
const offset = ref(0)
const searchText = ref('')

function syncFromRoute(): void {
  const rawQ = typeof route.query.q === 'string' ? route.query.q : ''
  const rawLimit = Number(typeof route.query.limit === 'string' ? route.query.limit : '')
  const rawOffset = Number(typeof route.query.offset === 'string' ? route.query.offset : '')
  q.value = rawQ
  limit.value = rawLimit === 50 ? 50 : 20
  offset.value = Number.isFinite(rawOffset) && rawOffset > 0 ? Math.floor(rawOffset) : 0
  if (searchText.value !== rawQ) searchText.value = rawQ
}

async function load(): Promise<void> {
  await store.loadUsers({
    q: q.value || undefined,
    limit: limit.value,
    offset: offset.value || undefined,
  })
}

watch(
  () => route.query,
  () => {
    syncFromRoute()
    void load()
  },
  { immediate: true },
)

function queryWith(changes: Record<string, string | undefined>): LocationQueryRaw {
  return { ...route.query, ...changes }
}

let debounce: ReturnType<typeof setTimeout> | null = null

function onSearchInput(event: Event): void {
  searchText.value = (event.target as HTMLInputElement).value
  if (debounce !== null) clearTimeout(debounce)
  debounce = setTimeout(() => {
    debounce = null
    if (searchText.value === q.value) return
    void router.replace({ query: queryWith({ q: searchText.value || undefined, offset: undefined }) })
  }, 300)
}

onBeforeUnmount(() => {
  if (debounce !== null) clearTimeout(debounce)
})

function onLimitChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  void router.replace({
    query: queryWith({ limit: value === 50 ? '50' : undefined, offset: undefined }),
  })
}

function goToOffset(next: number): void {
  void router.replace({ query: queryWith({ offset: next > 0 ? String(next) : undefined }) })
}

const hasPrev = computed(() => offset.value > 0)
const hasNext = computed(() => {
  const total = store.userTotal
  if (total === null) return store.users.length >= limit.value
  return offset.value + store.users.length < total
})

const rangeText = computed(() => {
  if (store.users.length === 0) return '共 0 位'
  return `共 ${store.userTotal ?? '—'} 位 · 第 ${offset.value + 1}–${offset.value + store.users.length} 位`
})
</script>

<template>
  <div class="cb-social-users" data-test="social-users">
    <SectionHeader
      title="用户"
      description="搜索按显示名 / QQ / 备注 / 标签匹配；分页与搜索条件都保存在地址栏。"
    />

    <div class="cb-social-users__toolbar">
      <label class="cb-social-users__field">
        <span class="cb-caption">搜索</span>
        <input
          class="cb-social-users__input"
          type="search"
          data-test="users-search"
          placeholder="名字 / QQ / 备注 / 标签"
          :value="searchText"
          @input="onSearchInput"
        />
      </label>
      <label class="cb-social-users__field">
        <span class="cb-caption">每页</span>
        <select class="cb-social-users__select" data-test="users-limit" :value="limit" @change="onLimitChange">
          <option :value="20">20</option>
          <option :value="50">50</option>
        </select>
      </label>
    </div>

    <ErrorState v-if="store.error" :message="store.error" @retry="load()" />

    <LoadingState
      v-else-if="store.loading && store.users.length === 0"
      label="正在读取人物…"
      :rows="4"
    />

    <EmptyState
      v-else-if="store.users.length === 0"
      title="还没有认识的人"
      :description="
        q ? `没有匹配「${q}」的人物，换个关键词试试。` : '与机器人产生真实互动后，这里会出现人物。'
      "
    />

    <template v-else>
      <div class="cb-social-users__grid">
        <PersonCard v-for="user in store.users" :key="user.person_id" :user="user" />
      </div>
      <div class="cb-social-users__pager">
        <span class="cb-caption" data-test="users-range">{{ rangeText }}</span>
        <div class="cb-social-users__pager-actions">
          <button
            type="button"
            class="cb-social-users__button"
            data-test="users-prev"
            :disabled="!hasPrev"
            @click="goToOffset(offset - limit)"
          >
            上一页
          </button>
          <button
            type="button"
            class="cb-social-users__button"
            data-test="users-next"
            :disabled="!hasNext"
            @click="goToOffset(offset + limit)"
          >
            下一页
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cb-social-users {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-users__toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-3);
  align-items: flex-end;
}

.cb-social-users__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.cb-social-users__input,
.cb-social-users__select {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-social-users__input {
  min-width: 240px;
}

.cb-social-users__input:focus,
.cb-social-users__select:focus {
  outline: none;
  border-color: var(--cb-primary);
  box-shadow: var(--cb-focus);
}

.cb-social-users__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--cb-space-3);
}

.cb-social-users__pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
}

.cb-social-users__pager-actions {
  display: flex;
  gap: var(--cb-space-2);
}

.cb-social-users__button {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-social-users__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-social-users__button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
