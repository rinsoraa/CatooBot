/** `/api/v1/memories*`：记忆域（含分页、时间线、危险动作确认门）。 */

import { api } from '@/api/client'
import type { MemoryFilters, MemoryHealth, MemoryRow } from '@/types/domain'

export interface MemoryPage {
  items: MemoryRow[]
  total: number | null
  limit: number
  offset: number
  next_cursor: string | null
}

export interface MemoryDetail {
  memory: MemoryRow
  relations: Record<string, unknown>[]
  supersedes: Record<string, unknown>[]
}

export const memoryApi = {
  list(filters: MemoryFilters = {}) {
    return api.get<MemoryPage>('/memories', {
      query: {
        q: filters.q,
        mode: filters.mode,
        scope_key: filters.scope_key,
        category: filters.category,
        layer: filters.layer,
        status: filters.status,
        person: filters.person,
        limit: filters.limit,
        offset: filters.offset,
      },
    })
  },

  detail(memoryId: number) {
    return api.get<MemoryDetail>(`/memories/${memoryId}`)
  },

  timeline(options: { limit?: number; scope_key?: string } = {}) {
    return api.get<{ items: MemoryRow[] }>('/memories/timeline', {
      query: { limit: options.limit, scope_key: options.scope_key },
    })
  },

  health() {
    return api.get<MemoryHealth>('/memories/health')
  },

  action(memoryId: number, action: 'activate' | 'archive' | 'reembed' | 'edit', body: Record<string, unknown> = {}) {
    return api.post<{ ok: boolean }>(`/memories/${memoryId}/${action}`, body)
  },

  /** 物理删除长期记忆：不可撤销，必须带确认串。 */
  remove(memoryId: number) {
    return api.post<{ ok: boolean }>(`/memories/${memoryId}/delete`, { confirm: 'delete' })
  },
}
