<script setup lang="ts">
/**
 * 能力分区外壳（W5 §33、§39、§42）：唯一 h1「媒体与能力」+ 三个 Tab + RouterView。
 *
 * 工具 / 表情 / Agent 三个子页各自从 `/api/v1` 拉取数据；本布局不缓存业务数据。
 */
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'

interface TabItem {
  /** 目标路径前缀，用于 active 判定（工具详情页仍高亮「工具」）。 */
  prefix: string
  to: string
  label: string
  test: string
}

const TABS: TabItem[] = [
  { prefix: '/abilities/tools', to: '/abilities/tools', label: '工具', test: 'abilities-tab-tools' },
  { prefix: '/abilities/media', to: '/abilities/media', label: '表情', test: 'abilities-tab-media' },
  { prefix: '/abilities/agent', to: '/abilities/agent', label: 'Agent', test: 'abilities-tab-agent' },
]

const route = useRoute()

function isActive(tab: TabItem): boolean {
  const path = route.path
  return path === tab.prefix || path.startsWith(`${tab.prefix}/`)
}

const activeTab = computed(() => TABS.find((tab) => isActive(tab))?.prefix ?? '')
</script>

<template>
  <div class="abilities" data-test="abilities-layout">
    <PageHeader title="媒体与能力" subtitle="工具、表情库与 Agent Runtime 的只读视图与受控操作" />

    <nav class="abilities__tabs" aria-label="能力分区">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.prefix"
        class="abilities__tab"
        :class="{ 'abilities__tab--active': activeTab === tab.prefix }"
        :to="tab.to"
        :aria-current="activeTab === tab.prefix ? 'page' : undefined"
        :data-test="tab.test"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style scoped>
.abilities {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.abilities__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding-bottom: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
}

.abilities__tab {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid transparent;
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.abilities__tab:hover {
  background: var(--cb-surface-raised);
  text-decoration: none;
}

.abilities__tab--active {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
