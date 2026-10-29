<script setup lang="ts">
/**
 * 记忆区块外壳（W5 §26）：唯一 h1 + 四个视图 tab + RouterView。
 *
 * 这里不预加载数据：浏览/时间线/健康度/运维各自按需拉取（§82/§85 首屏 + 按需详情）。
 */
import { RouterLink, RouterView } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'

interface TabItem {
  to: string
  label: string
  testKey: string
}

const TABS: TabItem[] = [
  { to: '/memory', label: '浏览', testKey: 'memory-tab-browse' },
  { to: '/memory/timeline', label: '时间线', testKey: 'memory-tab-timeline' },
  { to: '/memory/health', label: '健康度', testKey: 'memory-tab-health' },
  { to: '/memory/ops', label: '运维', testKey: 'memory-tab-ops' },
]
</script>

<template>
  <div class="cb-memory-layout" data-test="memory-layout">
    <PageHeader title="记忆" subtitle="浏览长期记忆、按时间回溯，并检查向量与检索健康度。" />

    <nav class="cb-memory-layout__tabs" aria-label="记忆子页面">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.to"
        class="cb-memory-layout__tab"
        :to="tab.to"
        :data-test="tab.testKey"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style scoped>
.cb-memory-layout {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-layout__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-layout__tab {
  padding: var(--cb-space-1) var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.cb-memory-layout__tab:hover {
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  text-decoration: none;
}

.cb-memory-layout__tab.router-link-exact-active {
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
