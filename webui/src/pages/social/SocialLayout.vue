<script setup lang="ts">
/**
 * 社交区块外壳（W5 §17）：唯一的 h1「社交」+ 五个子页签 + RouterView。
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
  { key: 'users', to: '/social', label: '用户' },
  { key: 'groups', to: '/social/groups', label: '群组' },
  { key: 'relationships', to: '/social/relationships', label: '关系' },
  { key: 'commitments', to: '/social/commitments', label: '承诺' },
  { key: 'sessions', to: '/social/sessions', label: '会话' },
]

const route = useRoute()

function isActive(to: string): boolean {
  const path = route.path
  if (to === '/social') return path === '/social' || path.startsWith('/social/users')
  return path === to || path.startsWith(`${to}/`)
}

const activeTab = computed(() => TABS.find((tab) => isActive(tab.to))?.key ?? '')
</script>

<template>
  <div class="cb-social-layout" data-testid="social-layout">
    <PageHeader
      title="社交"
      subtitle="人物、群组、关系、承诺与会话：全部来自运行期真实状态"
    />

    <nav class="cb-social-layout__tabs" aria-label="社交子页面">
      <RouterLink
        v-for="tab in TABS"
        :key="tab.key"
        class="cb-social-layout__tab"
        :class="{ 'cb-social-layout__tab--active': activeTab === tab.key }"
        :to="tab.to"
        :data-test="`social-tab-${tab.key}`"
        :aria-current="activeTab === tab.key ? 'page' : undefined"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RouterView />
  </div>
</template>

<style scoped>
.cb-social-layout {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-layout__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-layout__tab {
  padding: var(--cb-space-1) var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.cb-social-layout__tab:hover {
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  text-decoration: none;
}

.cb-social-layout__tab--active {
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
