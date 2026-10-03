/** W5 领域读取模型：世界 / 记忆 / 能力 / 系统（形状对应契约 §7.1、§7.4）。 */

// ---------------------------------------------------------------- World

export interface NeedFullRow {
  key: string
  label: string
  level: number
  band: string
  growth: number
  critical: boolean
  pressing: boolean
}

export interface InterruptedInfo {
  active: boolean
  definition_id?: string
  remaining_minutes?: number
  reason?: string
  progress?: number
}

export interface WorldActionInfo {
  name: string | null
  definition_id?: string
  detail?: string
  reason_code?: string
  space_id?: string
  goal_id?: string
  goal_step?: string
  progress?: number | null
  started_at?: number | null
  planned_end_at?: number | null
}

export interface WorldGoalRow {
  goal_id: string
  title: string
  status: string
  priority: number
  progress: number
  current_step: string
  target_commitment: string
  dedupe_key: string
}

export interface WorldSpaceRow {
  space_id: string
  name: string
  kind?: string
  [key: string]: unknown
}

export interface ActionDefRow {
  definition_id: string
  name: string
  space_id?: string
  needs?: string[]
}

export interface ModeDefRow {
  key: string
  label: string
  description: string
}

/** `GET /api/v1/world` 的 data（W5 追加块全部为可选，缺失即 null/[]）。 */
export interface WorldData {
  phase?: string | null
  location?: string | null
  action?: WorldActionInfo | null
  modes?: string[]
  needs?: { critical?: string[]; pressing?: string[]; bands?: Record<string, string> }
  world_revision?: number | null
  cognitive_revision?: number | null
  session?: { active?: boolean; person_id?: string | null } | null
  interrupted?: InterruptedInfo | null
  goals?: WorldGoalRow[]
  commitments?: Record<string, unknown>[]
  relationships?: Record<string, unknown>[]
  needs_full?: NeedFullRow[]
  spaces?: WorldSpaceRow[]
  objects?: Record<string, unknown>[]
  inventories?: Record<string, unknown>
  pet?: Record<string, unknown> | null
  social_spaces?: Record<string, unknown>[]
  action_defs?: ActionDefRow[]
  modes_defs?: ModeDefRow[]
  goals_full?: WorldGoalRow[]
}

export interface WorldTimelineRow {
  ts: number | null
  event_type: string
  summary: string
  location?: string | null
  action?: string | null
  revisions?: { world?: number | null; cognitive?: number | null }
}

export interface TopicRow {
  topic_id: string
  scope: string
  status: string
  text: string
  created_at: number | null
  updated_at: number | null
  [key: string]: unknown
}

// ---------------------------------------------------------------- Memory

export interface MemoryRow {
  memory_id: number
  content: string
  summary: string
  layer: string
  category: string
  scope_key: string
  status: string
  importance: number
  confidence: number
  created_at: number | null
  updated_at: number | null
  provenance: Record<string, unknown>
  score?: number
  origin?: string
}

export interface MemoryFilters {
  q?: string
  mode?: string
  scope_key?: string
  category?: string
  layer?: string
  status?: string
  person?: string
  limit?: number
  offset?: number
}

export interface MemoryHealth {
  enabled?: boolean
  total?: number | null
  active?: number | null
  archived?: number | null
  embedded?: number | null
  embedding_coverage?: number | null
  semantic_enabled?: boolean
  retrieval?: Record<string, unknown>
  database?: Record<string, unknown>
  [key: string]: unknown
}

// ---------------------------------------------------------------- Tools

export interface ToolRow {
  name: string
  display_name: string
  description: string
  category: string
  risk_level: string
  enabled: boolean
  requires_credentials: boolean
  has_credential: boolean | null
  timeout: number | null
  cache_ttl_seconds: number | null
  calls: number | null
  failures: number | null
  last_used_at: number | null
}

export interface ToolDetail extends ToolRow {
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  when_to_use?: string
  when_not_to_use?: string
  limitations?: string
  settings?: Record<string, unknown>
  metrics?: Record<string, unknown>
  recent_executions?: Record<string, unknown>[]
  permissions_summary?: Record<string, unknown>
}

export interface ToolTestResult {
  ok: boolean
  result: unknown
  error: string
  duration_ms: number
  may_have_called_external: boolean
  note?: string
}

// ------------------------------------------------------------ Media

export interface StickerRow {
  sticker_id: string
  file: string
  file_name: string
  preview_url: string | null
  emotion: string
  intent: string
  status: string
  origin: string
  origin_user: string
  usage_count: number
  last_used_at: number | null
  created_at: number | null
  safety_status: string
  valid: boolean
}

export interface ExpressionRow {
  pattern_id: string
  pattern: string
  kind: string
  status: string
  group_id: string
  occurrences: number
  speakers: number
  first_seen: number | null
  last_seen: number | null
}

// ------------------------------------------------------------- Agent

export interface AgentStatus {
  status: 'ready' | 'disabled' | 'unavailable' | string
  health?: Record<string, unknown>
  active_tasks?: Record<string, unknown>[]
  recent_tasks?: Record<string, unknown>[]
  policy?: Record<string, unknown>
  budget?: Record<string, unknown>
  planner_model?: string
  evaluator_model?: string
}

export interface AgentTaskRow {
  task_id: string
  status: string
  goal?: string
  created_at?: number | null
  updated_at?: number | null
  [key: string]: unknown
}

// ------------------------------------------------------- Runtime / Logs

export interface OneBotLaneRow {
  lane: string
  pending: number
  busy: boolean
}

export interface OneBotSnapshot {
  state: string
  connected: boolean | null
  self_id: number | null
  last_event_at: number | null
  received: number | null
  accepted: number | null
  deduped: number | null
  dropped: number | null
  self_ignored: number | null
  responses: number | null
  sent: number | null
  failed: number | null
  pending_outbound: number | null
  busy: boolean | null
  lanes: OneBotLaneRow[]
}

export interface ProcessSnapshot {
  uptime_seconds: number | null
  started_at: number | null
  version: string
  python: string
}

export interface LogTailRow {
  ts: number | null
  time: string
  level: string
  logger: string
  channel: string
  message: string
}

export interface LogChannel {
  key: string
  icon: string
  label: string
}

/** 日志等级（后端文件里真实出现的等级）。 */
export const LOG_LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR'] as const
