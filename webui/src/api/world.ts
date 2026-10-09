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
  WorldActivityDecisionView,
  WorldActivityAdvisorView,
  WorldActivityPlanView,
  WorldActivityView,
  WorldData,
  WorldInitiativeView,
  WorldAgentPlansView,
  WorldProposalsView,
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

  /** Phase 6A：她此刻的活动（Episode）。**只读** —— 不能启动/取消/延长。 */
  activity(limit = 10) {
    return api.get<WorldActivityView>('/world/activity', { query: { limit } })
  },

  /** Phase 6B：活动决策只读视图（decision / reason / transition_pending / …）。 */
  activityDecision() {
    return api.get<WorldActivityDecisionView>('/world/activity/decision')
  },

  /**
   * Phase 6C §五十八：计划（Rolling Horizon）只读视图。
   * 计划是"打算"、不是"现状"；这里**没有** force select，也没有重排按钮。
   */
  activityPlan() {
    return api.get<WorldActivityPlanView>('/world/activity/plan')
  },

  /**
   * Phase 6D §八十九：模型顾问只读回执。
   * 只读：**没有**"让模型再想一次"，也没有强制采纳/否决（§九十）。
   */
  activityAdvisor() {
    return api.get<WorldActivityAdvisorView>('/world/activity/advisor')
  },

  /**
   * Phase 7A §四十九：Initiative / LifeIntent 只读视图。
   * 只读：**没有** Execute / Send / Confirm / Run / Force（执行层恒为 NONE）。
   */
  worldInitiative() {
    return api.get<WorldInitiativeView>('/world/initiative')
  },

  /**
   * Phase 7C §十二：任务提案只读视图。
   * 只读：**没有** Execute / Confirm / Start / Run —— 执行层恒为 NONE。
   */
  worldProposals() {
    return api.get<WorldProposalsView>('/world/proposals')
  },

  /**
   * Phase 7D §九：任务计划只读视图。
   * 只读：**没有**批准 / 执行 / 确认入口 —— 批准只在 QQ，执行只在既有任务链。
   */
  worldAgentPlans() {
    return api.get<WorldAgentPlansView>('/world/agent-plans')
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
