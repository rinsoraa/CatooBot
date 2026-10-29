/** 能力域：工具 / 贴纸 / 口癖 / Agent（`/api/v1/tools|stickers|expressions|agent`）。 */

import { api } from '@/api/client'
import type {
  AgentStatus,
  AgentTaskRow,
  ExpressionRow,
  StickerRow,
  ToolDetail,
  ToolRow,
  ToolTestResult,
} from '@/types/domain'

export interface ToolListPayload {
  items: ToolRow[]
  policy: Record<string, unknown>
  stats: Record<string, unknown>
}

export const toolsApi = {
  list() {
    return api.get<ToolListPayload>('/tools')
  },

  detail(name: string) {
    return api.get<ToolDetail>(`/tools/${encodeURIComponent(name)}`)
  },

  update(name: string, payload: { enabled?: boolean; settings?: Record<string, unknown>; timeout?: number }) {
    return api.patch<ToolDetail & { applied: string[]; restart_required: boolean }>(
      `/tools/${encodeURIComponent(name)}`,
      payload,
    )
  },

  /** 诊断操作：可能真的发起外部请求，因此必须带确认串。 */
  test(name: string, arguments_: Record<string, unknown> = {}) {
    return api.post<ToolTestResult>(`/tools/${encodeURIComponent(name)}/test`, {
      arguments: arguments_,
      confirm: name,
    })
  },

  executions(limit = 50, name = '') {
    return api.get<{ items: Record<string, unknown>[]; total: number | null }>('/tools/executions', {
      query: { limit, name },
    })
  },

  metrics() {
    return api.get<Record<string, unknown>>('/tools/metrics')
  },

  permissions() {
    return api.get<{ items: Record<string, unknown>[]; total: number | null }>('/tools/permissions')
  },

  setPermission(payload: { scope: 'user' | 'group'; scope_id: string; tool: string; allowed: boolean }) {
    return api.put<{ saved: boolean }>('/tools/permissions', payload)
  },

  clearPermission(scope: string, scopeId: string, tool: string) {
    return api.del<{ cleared: boolean }>('/tools/permissions', {
      query: { scope, scope_id: scopeId, tool },
    })
  },

  clearCache(name = '') {
    return api.post<{ cleared: number; tool: string }>('/tools/cache/clear', { confirm: 'clear', name })
  },

  decisionDebug(text: string, mode = '') {
    return api.get<Record<string, unknown>>('/tools/decision-debug', { query: { text, mode } })
  },
}

export const mediaApi = {
  stickers(filters: { q?: string; status?: string; emotion?: string; limit?: number; offset?: number } = {}) {
    return api.get<{ items: StickerRow[]; stats: Record<string, unknown>; total: number | null }>(
      '/stickers',
      { query: filters },
    )
  },

  stickerAction(stickerId: string, action: 'enable' | 'disable' | 'delete', confirm?: string) {
    return api.post<{ sticker_id: string; action: string; status: string }>(
      `/stickers/${encodeURIComponent(stickerId)}/${action}`,
      action === 'delete' ? { confirm: confirm ?? 'delete' } : {},
    )
  },

  reindex() {
    return api.post<Record<string, unknown>>('/stickers/reindex')
  },

  expressions(filters: { status?: string; group_id?: string; limit?: number } = {}) {
    return api.get<{ items: ExpressionRow[]; stats: Record<string, unknown> }>('/expressions', {
      query: filters,
    })
  },

  expressionAction(patternId: string, action: 'enable' | 'disable' | 'delete', confirm?: string) {
    return api.post<{ ok: boolean }>(
      `/expressions/${encodeURIComponent(patternId)}/${action}`,
      action === 'delete' ? { confirm: confirm ?? 'delete' } : {},
    )
  },
}

export const agentApi = {
  status() {
    return api.get<AgentStatus>('/agent')
  },

  tasks(options: { status?: string; limit?: number; offset?: number } = {}) {
    return api.get<{ items: AgentTaskRow[]; total: number | null }>('/agent/tasks', {
      query: options,
    })
  },

  task(taskId: string) {
    return api.get<Record<string, unknown>>(`/agent/tasks/${encodeURIComponent(taskId)}`)
  },

  control(taskId: string, action: 'pause' | 'resume' | 'cancel' | 'retry' | 'replay') {
    return api.post<Record<string, unknown>>(
      `/agent/tasks/${encodeURIComponent(taskId)}/${action}`,
    )
  },

  simulate(text: string) {
    return api.post<Record<string, unknown>>('/agent/simulate', { text })
  },
}
