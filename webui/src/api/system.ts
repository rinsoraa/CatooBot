/** 系统运维：日志与 Runtime（`/api/v1/logs/*`、`/api/v1/runtime*`、`/api/v1/overview`）。 */

import { api } from '@/api/client'
import type { LogChannel, LogTailRow, OneBotSnapshot, ProcessSnapshot } from '@/types/domain'
import type { OverviewData, RuntimeSnapshot, SchedulerSnapshot } from '@/types/runtime'

export interface LogTailPayload {
  items: LogTailRow[]
  file: string
  truncated: boolean
  parsed: boolean
  total: number | null
}

export interface RuntimeDetail extends RuntimeSnapshot {
  process?: ProcessSnapshot | null
  onebot?: OneBotSnapshot | null
  database?: { connected?: boolean | null; size_bytes?: number | null } | null
  [key: string]: unknown
}

export const systemApi = {
  overview() {
    return api.get<OverviewData>('/overview')
  },

  runtime() {
    return api.get<RuntimeDetail>('/runtime')
  },

  runtimeStatus() {
    return api.get<RuntimeDetail>('/runtime/status')
  },

  scheduler() {
    return api.get<SchedulerSnapshot>('/runtime/scheduler')
  },

  /** 管理员调试：手动推进一次世界 tick（受运行期锁保护）。 */
  tick() {
    return api.post<{ ran: boolean; minutes?: number; report?: Record<string, unknown> }>(
      '/runtime/tick',
    )
  },

  runAction(name: 'reload_persona' | 'reload_plugins' | 'reload_models' | 'restore_model_overrides') {
    return api.post<{ done: boolean; action: string; detail: string }>(`/runtime/actions/${name}`)
  },

  logsTail(options: { level?: string; channel?: string; q?: string; limit?: number } = {}) {
    return api.get<LogTailPayload>('/logs/tail', {
      query: {
        level: options.level && options.level !== 'ALL' ? options.level : undefined,
        channel: options.channel,
        q: options.q,
        limit: options.limit,
      },
    })
  },

  logChannels() {
    return api.get<{ channels: LogChannel[] }>('/logs/channels')
  },
}
