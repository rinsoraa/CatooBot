/** `/api/v1/world`、`/api/v1/character` 的薄封装（含 W5 追加块）。 */

import { api } from '@/api/client'
import type { InterruptedInfo, TopicRow, WorldData, WorldTimelineRow } from '@/types/domain'

export interface CharacterState {
  mood?: string
  energy?: number
  [key: string]: unknown
}

export interface CharacterPayload {
  persona: Record<string, unknown>
  state: CharacterState
  source: 'config' | 'database' | string
}

export const worldApi = {
  character() {
    return api.get<CharacterPayload>('/character')
  },

  saveCharacter(payload: Record<string, unknown>) {
    return api.patch<CharacterPayload>('/character', payload)
  },

  state() {
    return api.get<CharacterState>('/character/state')
  },

  setState(changes: CharacterState) {
    return api.patch<CharacterState>('/character/state', changes)
  },

  world() {
    return api.get<WorldData>('/world')
  },

  timeline(limit = 100) {
    return api.get<{ items: WorldTimelineRow[] }>('/world/timeline', { query: { limit } })
  },

  trace(limit = 50) {
    return api.get<{ items: Record<string, unknown>[] }>('/world/trace', { query: { limit } })
  },

  topics() {
    return api.get<{ items: TopicRow[] }>('/world/topics')
  },

  topicAction(topicId: string, action: 'resolve' | 'forget' | 'delete') {
    return api.post<{ ok: boolean }>(`/world/topics/${encodeURIComponent(topicId)}/${action}`)
  },

  /** 管理员调试操作：会直接影响运行中的世界（页面必须二次确认）。 */
  control(action: 'pause' | 'resume' | 'reset' | 'reinitialize', confirm?: string) {
    return api.post<{ ok: boolean; reason?: string; phase?: string }>(`/world/control/${action}`, {
      confirm,
    })
  },
}

export type { InterruptedInfo }
