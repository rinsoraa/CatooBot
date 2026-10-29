<script setup lang="ts">
/**
 * 顶栏（§26）：侧栏开关、当前页面名、实时连接、机器人状态、主题、
 * 旧版后台入口、用户菜单。所有图标按钮都有 aria-label。
 */
import type { Component } from 'vue'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import StatusBadge from '@/components/StatusBadge.vue'
import type { StatusState } from '@/components/StatusBadge.vue'
import IconLogout from '@/components/icons/IconLogout.vue'
import IconMenu from '@/components/icons/IconMenu.vue'
import IconMonitor from '@/components/icons/IconMonitor.vue'
import IconMoon from '@/components/icons/IconMoon.vue'
import IconSun from '@/components/icons/IconSun.vue'
import type { ThemePreference } from '@/stores/app'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useRealtimeStore } from '@/stores/realtime'
import { useRuntimeStore } from '@/stores/runtime'

const emit = defineEmits<{ 'toggle-sidebar': [] }>()

const app = useAppStore()
const auth = useAuthStore()
const realtime = useRealtimeStore()
const runtime = useRuntimeStore()
const route = useRoute()
const router = useRouter()

const pageTitle = computed(() =>
  typeof route.meta.title === 'string' && route.meta.title ? route.meta.title : 'CatooBot',
)

const realtimeDot = computed(() => (realtime.connected ? '●' : '○'))
const realtimeText = computed(() =>
  realtime.connected ? 'Live / 已连接' : 'Reconnecting… / 重连中…',
)

const botStatus = computed<{ state: StatusState; label: string }>(() =>
  runtime.online ? { state: 'ok', label: '在线' } : { state: 'off', label: '离线' },
)

const THEME_LABELS: Record<ThemePreference, string> = {
  system: '跟随系统',
  light: '浅色',
  dark: '深色',
}
const themeLabel = computed(() => THEME_LABELS[app.theme])
const themeIcon = computed<Component>(() => {
  if (app.theme === 'light') return IconSun
  if (app.theme === 'dark') return IconMoon
  return IconMonitor
})

const userName = computed(() => auth.user?.name ?? '未登录')

async function handleLogout(): Promise<void> {
  await auth.logout()
  await router.push('/login')
}
</script>

<template>
  <header class="cb-topbar">
    <div class="cb-topbar__left">
      <button
        type="button"
        class="cb-topbar__icon-button"
        aria-label="切换侧栏"
        data-test="toggle-sidebar"
        @click="emit('toggle-sidebar')"
      >
        <IconMenu :size="18" />
      </button>
      <p class="cb-topbar__title">{{ pageTitle }}</p>
    </div>

    <div class="cb-topbar__right">
      <span
        class="cb-topbar__realtime"
        :data-state="realtime.connected ? 'ok' : 'warn'"
        role="status"
        data-test="realtime"
      >
        <span class="cb-topbar__dot" aria-hidden="true">{{ realtimeDot }}</span>
        <span class="cb-topbar__realtime-text">{{ realtimeText }}</span>
      </span>

      <span class="cb-topbar__bot">
        <span class="cb-topbar__bot-name">机器人</span>
        <StatusBadge :state="botStatus.state" :label="botStatus.label" data-test="bot-status" />
      </span>

      <button
        type="button"
        class="cb-topbar__icon-button"
        :aria-label="`主题：${themeLabel}，点击切换`"
        :title="`主题：${themeLabel}`"
        data-test="theme"
        @click="app.cycleTheme()"
      >
        <component :is="themeIcon" :size="18" />
      </button>

      <a class="cb-topbar__link" href="/legacy" target="_self">旧版后台</a>

      <details class="cb-topbar__user" data-test="user-menu">
        <summary class="cb-topbar__user-summary">
          <span class="cb-topbar__user-name">{{ userName }}</span>
        </summary>
        <div class="cb-topbar__user-menu">
          <button
            type="button"
            class="cb-topbar__logout"
            data-test="logout"
            @click="handleLogout"
          >
            <IconLogout :size="16" />
            <span>退出登录</span>
          </button>
        </div>
      </details>
    </div>
  </header>
</template>

<style scoped>
.cb-topbar {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  height: var(--cb-topbar-height);
  padding: 0 var(--cb-space-4);
  border-bottom: 1px solid var(--cb-border);
  background: var(--cb-bg);
}

.cb-topbar__left,
.cb-topbar__right {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-topbar__title {
  font-size: var(--cb-text-lg);
  font-weight: 600;
  color: var(--cb-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cb-topbar__icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text-muted);
  cursor: pointer;
}

.cb-topbar__icon-button:hover {
  border-color: var(--cb-border-strong);
  color: var(--cb-text);
}

.cb-topbar__realtime {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  white-space: nowrap;
}

.cb-topbar__realtime[data-state='ok'] {
  color: var(--cb-success);
}

.cb-topbar__realtime[data-state='warn'] {
  color: var(--cb-warning);
}

.cb-topbar__bot {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  white-space: nowrap;
}

.cb-topbar__bot-name {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.cb-topbar__link {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  white-space: nowrap;
}

.cb-topbar__link:hover {
  color: var(--cb-primary-strong);
}

.cb-topbar__user {
  position: relative;
  flex: none;
}

.cb-topbar__user-summary {
  display: inline-flex;
  align-items: center;
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  cursor: pointer;
  list-style: none;
}

.cb-topbar__user-summary::-webkit-details-marker {
  display: none;
}

.cb-topbar__user-name {
  max-width: 12ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cb-topbar__user-menu {
  position: absolute;
  right: 0;
  top: calc(100% + var(--cb-space-1));
  min-width: 148px;
  padding: var(--cb-space-1);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
}

.cb-topbar__logout {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  width: 100%;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 0;
  border-radius: var(--cb-radius-sm);
  background: transparent;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.cb-topbar__logout:hover {
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

@media (max-width: 720px) {
  .cb-topbar__bot-name,
  .cb-topbar__link {
    display: none;
  }
}
</style>
