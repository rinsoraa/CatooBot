<script setup lang="ts">
/**
 * 系统分区外壳（W4 §30）：PageHeader + 分区 Tab + RouterView。
 *
 * 顶部固定挂 RestartBanner：任何页面都能看到「已保存、等待重启」的键，
 * 但这里没有任何重启动作（§53）。
 */
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import PageHeader from '@/components/PageHeader.vue'
import RestartBanner from '@/components/config/RestartBanner.vue'

const route = useRoute()

const tabs = [
  { name: 'system-settings', label: '设置' },
  { name: 'system-credentials', label: '凭据' },
  { name: 'system-advanced', label: '高级 YAML' },
  { name: 'system-logs', label: '日志' },
  { name: 'system-runtime', label: 'Runtime' },
  { name: 'system-restart-pending', label: '等待重启' },
] as const

const activeName = computed(() => String(route.name ?? ''))
</script>

<template>
  <div class="cb-system" data-test="system-layout">
    <PageHeader title="系统" subtitle="配置中心、凭据与等待重启项" />

    <nav class="cb-system__tabs" aria-label="系统分区">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.name"
        class="cb-system__tab"
        :class="{ 'cb-system__tab--active': activeName === tab.name }"
        :to="{ name: tab.name }"
        :aria-current="activeName === tab.name ? 'page' : undefined"
        :data-test="`system-tab-${tab.name}`"
      >
        {{ tab.label }}
      </RouterLink>
    </nav>

    <RestartBanner />

    <RouterView />
  </div>
</template>

<style scoped>
.cb-system {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-system__tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
  padding-bottom: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
}

.cb-system__tab {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid transparent;
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-system__tab:hover {
  background: var(--cb-surface-raised);
  text-decoration: none;
}

.cb-system__tab--active {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}
</style>
