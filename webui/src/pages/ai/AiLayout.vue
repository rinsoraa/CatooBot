<script setup lang="ts">
/**
 * AI 区块外壳（§6）：唯一的 h1 + 子页面导航 + RouterView。
 *
 * 首次进入本区块时触发一次全量加载；数据全部来自 useAiStore，页面不各自乱拉。
 */
import { computed, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { useAiStore } from '@/stores/ai'
import type { AiHealth } from '@/types/ai'

const aiStore = useAiStore()

interface TabItem {
  to: string
  label: string
}

const TABS: TabItem[] = [
  { to: '/ai', label: '概览' },
  { to: '/ai/providers', label: '服务商' },
  { to: '/ai/models', label: '模型' },
  { to: '/ai/roles', label: '模型用途' },
  { to: '/ai/failover', label: '故障转移' },
  { to: '/ai/usage', label: '用量' },
  { to: '/ai/test', label: '测试台' },
  { to: '/ai/setup', label: '向导' },
]

const HEALTH_META: Record<AiHealth, { state: StatusState; label: string }> = {
  ready: { state: 'ok', label: '就绪' },
  degraded: { state: 'warn', label: '降级' },
  unavailable: { state: 'error', label: '不可用' },
  not_configured: { state: 'off', label: '尚未配置' },
}

const health = computed(() => {
  const status = aiStore.status?.status
  if (!status) return { state: 'idle' as StatusState, label: '状态未知' }
  return HEALTH_META[status]
})

onMounted(() => {
  if (!aiStore.lastLoadedAt && !aiStore.loading) void aiStore.loadAll()
})
</script>

<template>
  <div class="ai-layout" data-testid="ai-layout">
    <PageHeader title="AI 与模型" subtitle="服务商、模型、用途与运行状态都从这里管理">
      <template #actions>
        <StatusBadge :state="health.state" :label="health.label" data-test="ai-health-badge" />
      </template>
    </PageHeader>

    <nav class="ai-layout__tabs" aria-label="AI 子页面">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.to"
        class="ai-layout__tab"
        :to="tab.to"
        :data-test="`ai-tab-${tab.to}`"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style scoped>
.ai-layout {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.ai-layout__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.ai-layout__tab {
  padding: var(--cb-space-1) var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.ai-layout__tab:hover {
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  text-decoration: none;
}

.ai-layout__tab.router-link-exact-active {
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
