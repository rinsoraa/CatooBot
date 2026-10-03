<script setup lang="ts">
/**
 * 记忆健康度（W5 §31）：只展示后端真实数字，未知一律「—」，不做任何估算。
 *
 * `enabled: false` 时明确显示「记忆功能未启用」；[刷新] 重新拉取 /memories/health。
 */
import { computed, onMounted, ref } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import MetricCard from '@/components/MetricCard.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useMemoryStore } from '@/stores/memory'

const store = useMemoryStore()
const loading = ref(false)

async function refresh(): Promise<void> {
  loading.value = true
  store.clear()
  try {
    await store.loadHealth()
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (!store.health && !loading.value) void refresh()
})

const health = computed(() => store.health)
const notEnabled = computed(() => health.value?.enabled === false)

function count(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

const totalValue = computed(() => count(health.value?.total))
const activeValue = computed(() => count(health.value?.active))
const archivedValue = computed(() => count(health.value?.archived))
const embeddedValue = computed(() => count(health.value?.embedded))

const coverageLabel = computed<string | null>(() => {
  const value = health.value?.embedding_coverage
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return `${Math.round(value * 100)}%`
})

function boolLabel(value: unknown): string | null {
  if (typeof value !== 'boolean') return null
  return value ? '已启用' : '未启用'
}

const semanticLabel = computed<string | null>(() => {
  const direct = boolLabel(health.value?.semantic_enabled)
  if (direct) return direct
  const retrieval = health.value?.retrieval
  if (retrieval && typeof retrieval === 'object') return boolLabel(retrieval.semantic_available)
  return null
})

const retrievalLabel = computed<string | null>(() => {
  const retrieval = health.value?.retrieval
  if (!retrieval || typeof retrieval !== 'object') return null
  const available = boolLabel(retrieval.semantic_available)
  if (available) return available === '已启用' ? '语义 + 关键词' : '仅关键词'
  return Object.keys(retrieval).length > 0 ? '已加载' : null
})

const databaseLabel = computed<string | null>(() => {
  const database = health.value?.database
  if (!database) return null
  if (typeof database === 'string') return database || null
  if (typeof database !== 'object') return null
  const connected = boolLabel(database.connected)
  if (connected) return connected === '已启用' ? '已连接' : '未连接'
  return Object.keys(database).length > 0 ? '已加载' : null
})
</script>

<template>
  <div class="cb-memory-health" data-test="memory-health">
    <section class="cb-memory-health__panel cb-card">
      <SectionHeader
        title="记忆健康度"
        description="数字全部来自后端；没有数据的字段显示「—」，不做估算。"
      >
        <template #actions>
          <button
            type="button"
            class="cb-memory-health__refresh"
            data-test="health-refresh"
            :disabled="loading"
            @click="refresh"
          >
            刷新
          </button>
        </template>
      </SectionHeader>
    </section>

    <ErrorState v-if="store.error" :message="store.error" @retry="refresh" />

    <LoadingState v-else-if="loading && !health" label="正在读取记忆健康度…" :rows="3" />

    <EmptyState
      v-else-if="notEnabled"
      title="记忆功能未启用"
      description="后端没有启用长期记忆；启用并重启后再回来查看健康度。"
    />

    <div v-else-if="health" class="cb-memory-health__grid" data-test="health-grid">
      <MetricCard label="总记忆数" :value="totalValue" data-test="health-total" />
      <MetricCard label="活跃" :value="activeValue" data-test="health-active" />
      <MetricCard label="已归档" :value="archivedValue" data-test="health-archived" />
      <MetricCard label="已向量化" :value="embeddedValue" data-test="health-embedded" />
      <MetricCard label="向量覆盖率" :value="coverageLabel" data-test="health-coverage" />
      <MetricCard label="语义检索" :value="semanticLabel" data-test="health-semantic" />
      <MetricCard label="检索状态" :value="retrievalLabel" data-test="health-retrieval" />
      <MetricCard label="数据库状态" :value="databaseLabel" data-test="health-database" />
    </div>
  </div>
</template>

<style scoped>
.cb-memory-health {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-health__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-health__refresh {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-memory-health__refresh:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-memory-health__refresh:disabled {
  color: var(--cb-text-faint);
  cursor: not-allowed;
}

.cb-memory-health__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--cb-space-3);
}
</style>
