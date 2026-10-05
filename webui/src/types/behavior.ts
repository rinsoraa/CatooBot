/**
 * 行为调试（`/api/v1/behavior/*`）——v0.8「沙盒 · 对话行为」迁移过来的形状。
 *
 * 三个接口全是 dry-run：生成 / 模拟 / 手动触发，永远不会发 QQ 消息。
 * 后端返回的 state / initiative 字段保持宽松，缺字段即不渲染。
 */

/** `POST /behavior/test-response` 的 data（试跑一次回复链路）。 */
export interface BehaviorTestResponseResult {
  ok: boolean
  reply?: string
  /** 计划延迟（秒）。 */
  delay?: number
  chunks?: string[]
  /** 触发时的角色叙事状态。 */
  state?: Record<string, unknown>
  /** 时间上下文描述。 */
  time?: string
  /** `ok === false` 时的失败原因（模型不可用等）。 */
  error?: string
}

/** 主动搭话判定（preview / test_initiative）。 */
export interface BehaviorInitiativePreview {
  would_consider: boolean
  blocked_by: string
  reason: string
  probability: number
  simulated_roll: number
}

/** `POST /behavior/preview` 的 data（行为模拟器）。 */
export interface BehaviorPreviewResult {
  ok: boolean
  time?: string
  period?: string
  sleeping?: boolean
  dnd?: boolean
  state?: Record<string, unknown>
  relationship?: string
  delay?: number
  chunks?: string[]
  initiative?: BehaviorInitiativePreview
}

/** `POST /behavior/preview` 允许的输入字段（后端白名单完全一致）。 */
export interface BehaviorPreviewInput {
  sim_time?: string
  mood?: string
  activity?: string
  relationship?: string
  topic?: string
  sample_reply?: string
}

/** 手动触发的动作（后端 BEHAVIOR_TRIGGERS）。 */
export type BehaviorTriggerAction = 'mood_up' | 'mood_down' | 'reset_state' | 'test_initiative'

/** `POST /behavior/triggers/{action}` 的 data。 */
export interface BehaviorTriggerResult {
  action: BehaviorTriggerAction
  result: unknown
}
