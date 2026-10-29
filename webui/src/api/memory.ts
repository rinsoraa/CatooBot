/** `/api/v1/memories*`：记忆域（含分页、时间线、危险动作确认门、向量运维与检索调试）。 */

import { api } from '@/api/client'
import type {
  MemoryConsolidationRunResult,
  MemoryConsolidationStatus,
  MemoryEmbeddingActionResult,
  MemoryEmbeddingStatus,
  MemoryFilters,
  MemoryHealth,
  MemoryRetrievalDebug,
  MemoryRow,
} from '@/types/domain'

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

/** 向量运维动作（clear-cache 需要确认串）。 */
export type MemoryEmbeddingAction = 'rebuild' | 'retry' | 'clear-cache'

/** clear-cache 的服务端确认串（缺少会 409 `memory.confirm_required`）。 */
export const EMBEDDING_CLEAR_CONFIRM = 'clear-cache'

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

  // ------------------------------------------------------------ 记忆运维

  /** 向量库 + embedding 服务状态（只读）。 */
  embeddingStatus() {
    return api.get<MemoryEmbeddingStatus>('/memories/embeddings')
  },

  /**
   * 重建 / 重试 / 清空向量缓存。
   *
   * 服务端对 clear-cache 要求 `confirm: "clear-cache"`；这里统一在 API 层带上，
   * 界面层仍会先弹确认框。
   */
  embeddingAction(action: MemoryEmbeddingAction) {
    const body = action === 'clear-cache' ? { confirm: EMBEDDING_CLEAR_CONFIRM } : {}
    return api.post<MemoryEmbeddingActionResult>(`/memories/embeddings/${action}`, body)
  },

  /** 记忆整理状态（memory 关闭时返回 `{enabled: false}`）。 */
  consolidationStatus() {
    return api.get<MemoryConsolidationStatus>('/memories/consolidation')
  },

  /** 手动跑一次整理；scope 为空表示全部范围。 */
  runConsolidation(scope = '') {
    return api.post<MemoryConsolidationRunResult>('/memories/consolidation/run', { scope })
  },

  /** 检索打分链路（只读；q 必填，scope 可选）。 */
  retrievalDebug(query: string, scope = '') {
    return api.get<MemoryRetrievalDebug>('/memories/retrieval-debug', {
      query: { q: query, scope },
    })
  },
}
