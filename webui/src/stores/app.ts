/** 应用级偏好：主题（light/dark/system）与侧栏折叠（§34-§35）。 */

import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

export type ThemePreference = 'light' | 'dark' | 'system'

const THEME_KEY = 'catoobot-theme'
const NAV_KEY = 'catoobot-nav-collapsed'

function readTheme(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    /* private mode / no storage */
  }
  return 'system'
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(NAV_KEY) === '1'
  } catch {
    return false
  }
}

function systemPrefersDark(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const useAppStore = defineStore('app', () => {
  const theme = ref<ThemePreference>(readTheme())
  const systemDark = ref(systemPrefersDark())
  const sidebarCollapsed = ref(readCollapsed())

  const isDark = computed(() => (theme.value === 'system' ? systemDark.value : theme.value === 'dark'))

  function apply(): void {
    if (typeof document === 'undefined') return
    document.documentElement.dataset.theme = isDark.value ? 'dark' : 'light'
  }

  function setTheme(next: ThemePreference): void {
    theme.value = next
    try {
      localStorage.setItem(THEME_KEY, next)
    } catch {
      /* ignore */
    }
    apply()
  }

  function cycleTheme(): void {
    const order: ThemePreference[] = ['system', 'light', 'dark']
    const index = order.indexOf(theme.value)
    setTheme(order[(index + 1) % order.length] ?? 'system')
  }

  function toggleSidebar(): void {
    sidebarCollapsed.value = !sidebarCollapsed.value
    try {
      localStorage.setItem(NAV_KEY, sidebarCollapsed.value ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  /** 供测试与「跟随系统」使用：外部注入系统偏好。 */
  function setSystemDark(value: boolean): void {
    systemDark.value = value
    apply()
  }

  watch(isDark, apply, { immediate: true })

  return {
    theme,
    systemDark,
    isDark,
    sidebarCollapsed,
    setTheme,
    cycleTheme,
    toggleSidebar,
    setSystemDark,
    apply,
  }
})
