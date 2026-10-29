<script setup lang="ts">
/**
 * 承诺页（W5 §23）：状态过滤映射真实 Core 状态值，人物过滤走 `?person=`；
 * 过滤条件全部保存在 URL，刷新与分享后结果一致。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter, type LocationQueryRaw } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import CommitmentCard from '@/components/domain/CommitmentCard.vue'
import { useSocialStore } from '@/stores/social'
import type { CommitmentRow } from '@/types/social'

interface StatusFilter {
  value: string
  label: string
}

/** 「进行中 / 已履行 / 未履行 / 已取消」映射到真实 CommitmentStatus 取值。 */
const STATUS_FILTERS: StatusFilter[] = [
  { value: '', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'completed', label: '已履行' },
  { value: 'broken', label: '未履行' },
  { value: 'cancelled', label: '已取消' },
]

const route = useRoute()
const router = useRouter()
const store = useSocialStore()

const statusFilter = ref('')
const personFilter = ref('')
const personText = ref('')
const loading = ref(false)

function syncFromRoute(): void {
  const rawStatus = typeof route.query.status === 'string' ? route.query.status : ''
  statusFilter.value = STATUS_FILTERS.some((item) => item.value === rawStatus) ? rawStatus : ''
  const rawPerson = typeof route.query.person === 'string' ? route.query.person : ''
  personFilter.value = rawPerson
  if (personText.value !== rawPerson) personText.value = rawPerson
}

async function load(): Promise<void> {
  loading.value = true
  await store.loadCommitments({
    status: statusFilter.value || undefined,
    person: personFilter.value || undefined,
  })
  loading.value = false
}

watch(
  () => route.query,
  () => {
    syncFromRoute()
    void load()
  },
  { immediate: true },
)

const rows = computed<CommitmentRow[]>(() => store.commitments)

const filterActive = computed(() => Boolean(statusFilter.value || personFilter.value))

const emptyDescription = computed(() =>
  filterActive.value
    ? '当前筛选条件下没有承诺记录；可以清除筛选查看全部。'
    : '机器人还没有对任何人做出可追踪的承诺。',
)

function queryWith(changes: Record<string, string | undefined>): LocationQueryRaw {
  return { ...route.query, ...changes }
}

function setStatus(value: string): void {
  if (value === statusFilter.value) return
  void router.replace({ query: queryWith({ status: value || undefined }) })
}

function applyPerson(): void {
  const next = personText.value.trim()
  if (next === personFilter.value) return
  void router.replace({ query: queryWith({ person: next || undefined }) })
}

function clearFilters(): void {
  personText.value = ''
  void router.replace({ query: {} })
}
</script>

<template>
  <div class="cb-social-commitments" data-test="social-commitments">
    <SectionHeader
      title="承诺"
      description="状态过滤使用后端真实枚举值：进行中 = active、已履行 = completed、未履行 = broken、已取消 = cancelled。"
    />

    <div class="cb-social-commitments__filters">
      <div class="cb-social-commitments__status" role="group" aria-label="按状态过滤">
        <button
          v-for="item in STATUS_FILTERS"
          :key="item.value || 'all'"
          type="button"
          class="cb-social-commitments__status-button"
          :class="{ 'cb-social-commitments__status-button--active': statusFilter === item.value }"
          :data-test="`commitment-filter-${item.value || 'all'}`"
          :aria-pressed="statusFilter === item.value"
          @click="setStatus(item.value)"
        >
          {{ item.label }}
        </button>
      </div>

      <form class="cb-social-commitments__person" @submit.prevent="applyPerson">
        <label class="cb-social-commitments__field">
          <span class="cb-caption">人物 person_id</span>
          <input
            v-model="personText"
            data-test="commitment-person-filter"
            type="text"
            placeholder="按 person_id 过滤"
          />
        </label>
        <button type="submit" class="cb-social-commitments__apply" data-test="commitment-person-apply">
          过滤
        </button>
        <button
          v-if="filterActive"
          type="button"
          class="cb-social-commitments__apply"
          data-test="commitment-clear"
          @click="clearFilters"
        >
          清除筛选
        </button>
      </form>
    </div>

    <ErrorState v-if="store.error" :message="store.error" @retry="load()" />

    <LoadingState v-else-if="loading && rows.length === 0" label="正在读取承诺…" :rows="4" />

    <EmptyState
      v-else-if="rows.length === 0"
      title="当前没有承诺记录"
      :description="emptyDescription"
    />

    <div v-else class="cb-social-commitments__list">
      <CommitmentCard v-for="row in rows" :key="row.commitment_id" :commitment="row" />
    </div>
  </div>
</template>

<style scoped>
.cb-social-commitments {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-commitments__filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--cb-space-3);
}

.cb-social-commitments__status {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-commitments__status-button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: none;
  border-radius: var(--cb-radius-sm);
  background: transparent;
  color: var(--cb-text-muted);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-social-commitments__status-button:hover {
  background: var(--cb-surface-raised);
  color: var(--cb-text);
}

.cb-social-commitments__status-button--active {
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.cb-social-commitments__person {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-2);
}

.cb-social-commitments__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.cb-social-commitments__field input {
  min-width: 200px;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-social-commitments__field input:focus {
  outline: none;
  border-color: var(--cb-primary);
  box-shadow: var(--cb-focus);
}

.cb-social-commitments__apply {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-social-commitments__apply:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-social-commitments__list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--cb-space-3);
}
</style>
