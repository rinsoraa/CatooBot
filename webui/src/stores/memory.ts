/**
 * 记忆域缓存（W5 §26-§32、§82/§85）。
 *
 * 列表只加载一页（服务端分页），详情只按需拉取；危险动作走确认门。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { errorMessage } from '@/api/client'
import { memoryApi, type MemoryDetail } from '@/api/memory'
import type { MemoryFilters, MemoryHealth, MemoryRow } from '@/types/domain'

const PAGE_SIZE = 20

export const useMemoryStore = defineStore('memory', () => {
  const items = ref<MemoryRow[]>([])
  const total = ref<number | null>(null)
  const offset = ref(0)
  const limit = ref(PAGE_SIZE)
  const filters = ref<MemoryFilters>({ limit: PAGE_SIZE, offset: 0, status: 'active' })
  const detail = ref<MemoryDetail | null>(null)
  const timeline = ref<MemoryRow[]>([])
  const health = ref<MemoryHealth | null>(null)

  const loading = ref(false)
  const error = ref('')

  function fail(caught: unknown): void {
    error.value = errorMessage(caught)
  }

  async function load(next: Partial<MemoryFilters> = {}): Promise<void> {
    filters.value = { ...filters.value, ...next }
    limit.value = filters.value.limit ?? PAGE_SIZE
    offset.value = filters.value.offset ?? 0
    loading.value = true
    error.value = ''
    try {
      const page = await memoryApi.list(filters.value)
      items.value = page.items
      total.value = page.total ?? null
      offset.value = page.offset ?? offset.value
    } catch (caught) {
      fail(caught)
    } finally {
      loading.value = false
    }
  }

  async function search(query: string): Promise<void> {
    await load({ q: query || undefined, offset: 0 })
  }

  async function page(direction: 'next' | 'prev'): Promise<void> {
    const step = direction === 'next' ? limit.value : -limit.value
    const nextOffset = Math.max(0, offset.value + step)
    await load({ offset: nextOffset })
  }

  async function loadDetail(memoryId: number): Promise<void> {
    loading.value = true
    try {
      detail.value = await memoryApi.detail(memoryId)
    } catch (caught) {
      detail.value = null
      fail(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadTimeline(limitRows = 200): Promise<void> {
    try {
      const data = await memoryApi.timeline({ limit: limitRows })
      timeline.value = data.items
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadHealth(): Promise<void> {
    try {
      health.value = await memoryApi.health()
    } catch (caught) {
      fail(caught)
    }
  }

  /** 变更后列表与健康度一起失效（§114）。 */
  async function act(
    memoryId: number,
    action: 'activate' | 'archive' | 'reembed' | 'edit' | 'delete',
    body: Record<string, unknown> = {},
  ): Promise<boolean> {
    error.value = ''
    try {
      if (action === 'delete') {
        await memoryApi.remove(memoryId)
      } else {
        await memoryApi.action(memoryId, action, body)
      }
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([load(), loadHealth()])
    if (detail.value?.memory.memory_id === memoryId) {
      if (action === 'delete') detail.value = null
      else await loadDetail(memoryId)
    }
    return true
  }

  function clear(): void {
    items.value = []
    detail.value = null
    timeline.value = []
    health.value = null
    error.value = ''
  }

  return {
    items,
    total,
    offset,
    limit,
    filters,
    detail,
    timeline,
    health,
    loading,
    error,
    load,
    search,
    page,
    loadDetail,
    loadTimeline,
    loadHealth,
    act,
    clear,
  }
})
