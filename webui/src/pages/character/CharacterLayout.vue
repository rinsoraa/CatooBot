<script setup lang="ts">
/**
 * 角色区块外壳（W5 §6）：唯一的 h1「角色」+ 三个子页签 + RouterView。
 *
 * 子页面的 h1 由这里统一提供，页面内部只用 h2 区块标题。
 */
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'

interface TabItem {
  key: string
  to: string
  label: string
}

const TABS: TabItem[] = [
  { key: 'character', to: '/character', label: '当前角色' },
  { key: 'world', to: '/character/world', label: '世界' },
  { key: 'timeline', to: '/character/world/timeline', label: '世界时间线' },
]

const route = useRoute()

function isActive(to: string): boolean {
  const path = route.path
  if (to === '/character') return path === '/character'
  if (to === '/character/world') return path === '/character/world'
  return path === to || path.startsWith(`${to}/`)
}

const activeTab = computed(() => TABS.find((tab) => isActive(tab.to))?.key ?? '')
</script>

<template>
  <div class="cb-character-layout" data-testid="character-layout">
    <PageHeader
      title="角色"
      subtitle="运行状态、当前世界与人设：人设与叙事状态可编辑并热加载"
    />

    <nav class="cb-character-layout__tabs" aria-label="角色子页面">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.key"
        class="cb-character-layout__tab"
        :class="{ 'cb-character-layout__tab--active': activeTab === tab.key }"
        :to="tab.to"
        :data-test="`character-tab-${tab.key}`"
        :aria-current="activeTab === tab.key ? 'page' : undefined"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style scoped>
.cb-character-layout {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-character-layout__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-character-layout__tab {
  padding: var(--cb-space-1) var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.cb-character-layout__tab:hover {
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  text-decoration: none;
}

.cb-character-layout__tab--active {
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
