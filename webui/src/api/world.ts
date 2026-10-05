/** `/api/v1/world`、`/api/v1/character` 的薄封装（含 W5 追加块）。 */

import { api } from '@/api/client'
import type {
  CharacterExportDocument,
  CharacterImportResult,
  CharacterPayload,
  CharacterPersonaPatch,
  CharacterState,
  InterruptedInfo,
  TopicRow,
  WorldData,
  WorldTimelineRow,
} from '@/types/domain'

export type { CharacterPayload, CharacterState } from '@/types/domain'

export const worldApi = {
  character() {
    return api.get<CharacterPayload>('/character')
  },

  /** 只传改动字段；嵌套组（identity 等）需整组提交（后端顶层浅合并）。 */
  saveCharacter(payload: CharacterPersonaPatch) {
    return api.patch<CharacterPayload>('/character', payload)
  },

  state() {
    return api.get<CharacterState>('/character/state')
  },

  setState(changes: Partial<CharacterState>) {
    return api.patch<CharacterState>('/character/state', changes)
  },

  /** 导出完整角色文档（下载 JSON 用）。 */
  exportCharacter() {
    return api.get<CharacterExportDocument>('/character/export')
  },

  /** 导入角色文档：不带 confirm 只预览（`data.applied === false`）。 */
  importCharacter(document: CharacterExportDocument, confirm?: 'import') {
    return api.post<CharacterImportResult>(
      '/character/import',
      confirm ? { document, confirm } : { document },
    )
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
