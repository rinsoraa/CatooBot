/** 轻量 Toast（§32）：不用 alert()，统一 success / info / warning / error。 */

import { readonly, ref } from 'vue'

export type ToastKind = 'success' | 'info' | 'warning' | 'error'

export interface ToastItem {
  id: number
  kind: ToastKind
  message: string
  detail: string
  timeout: number
}

const items = ref<ToastItem[]>([])
let nextId = 1

function push(kind: ToastKind, message: string, detail = '', timeout = 4200): number {
  const id = nextId++
  items.value = [...items.value, { id, kind, message, detail, timeout }]
  return id
}

function dismiss(id: number): void {
  items.value = items.value.filter((item) => item.id !== id)
}

function clear(): void {
  items.value = []
}

export function useToast() {
  return { items: readonly(items), push, dismiss, clear }
}

/** 业务代码只调这个：`toast.success('已保存')`。 */
export const toast = {
  success: (message: string, detail = '') => push('success', message, detail),
  info: (message: string, detail = '') => push('info', message, detail),
  warning: (message: string, detail = '') => push('warning', message, detail),
  error: (message: string, detail = '') => push('error', message, detail),
}
