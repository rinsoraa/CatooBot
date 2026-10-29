/**
 * Naive UI 与 Design Tokens 的适配层。
 *
 * 组件库需要真实颜色值，而颜色只允许在 tokens.css 里定义一次：
 * 这里在运行时读取 CSS 变量（浅/深由 `data-theme` 决定），
 * 组件代码依旧不写死任何色值（§20）。
 */

import { computed, type ComputedRef } from 'vue'
import type { GlobalThemeOverrides } from 'naive-ui'

export function readToken(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export function useNaiveOverrides(dark: ComputedRef<boolean>): ComputedRef<GlobalThemeOverrides> {
  return computed<GlobalThemeOverrides>(() => {
    // touch `dark` so the overrides recompute when the theme flips
    void dark.value
    const primary = readToken('--cb-primary', '#8b93e8')
    const success = readToken('--cb-success', '#7cc6a6')
    const warning = readToken('--cb-warning', '#dcc07f')
    const error = readToken('--cb-danger', '#e08c84')
    const radius = readToken('--cb-radius-md', '10px')
    return {
      common: {
        primaryColor: primary,
        primaryColorHover: readToken('--cb-primary-strong', primary),
        primaryColorPressed: readToken('--cb-primary-strong', primary),
        successColor: success,
        warningColor: warning,
        errorColor: error,
        borderRadius: radius,
        fontFamily: readToken('--cb-font-sans', 'system-ui, sans-serif'),
      },
      Input: {
        color: readToken('--cb-surface-raised', 'transparent'),
        colorFocus: readToken('--cb-surface-raised', 'transparent'),
        border: `1px solid ${readToken('--cb-border', '#2e313b')}`,
        borderFocus: `1px solid ${primary}`,
        textColor: readToken('--cb-text', '#e8e9ee'),
        placeholderColor: readToken('--cb-text-faint', '#767b8a'),
        caretColor: primary,
      },
      Button: {
        textColorPrimary: readToken('--cb-bg', '#14151a'),
        borderRadiusMedium: radius,
      },
      Modal: {
        color: readToken('--cb-surface', '#1d1f26'),
      },
    }
  })
}
