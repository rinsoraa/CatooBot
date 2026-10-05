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

/** `GET /api/v1/memories/embeddings` 的 data：向量库 + embedding 服务快照。 */
export interface MemoryEmbeddingStatus {
  available?: boolean
  embedded?: number | null
  memories?: number | null
  coverage?: number | null
  models?: number | null
  pending?: number | null
  provider?: string
  model?: string
  dimensions?: number | null
  hits?: number | null
  misses?: number | null
  failures?: number | null
  last_error?: string | null
  timeout?: number | null
  [key: string]: unknown
}

/** `POST /api/v1/memories/embeddings/{action}` 的 data；result 里可能是 `{error}`。 */
export interface MemoryEmbeddingActionResult {
  action: string
  result: Record<string, unknown>
}

/** 整理报告（后端 `ConsolidationReport.to_dict()`，运行返回里带 `summary`）。 */
export interface MemoryConsolidationReport {
  scanned?: number
  duplicates_merged?: number
  archived?: number
  compressed_clusters?: number
  compressed_sources?: number
  llm_compressions?: number
  conflicts?: number
  quota_archived?: number
  errors?: number
  duration_ms?: number
  scopes?: string[]
  summary?: string
  error?: string
  [key: string]: unknown
}

/** `GET /api/v1/memories/consolidation` 的 data（memory 关闭时只有 enabled: false）。 */
export interface MemoryConsolidationStatus {
  enabled?: boolean
  schedule?: string
  duplicate_threshold?: number
  compression_min_cluster?: number
  last_report?: MemoryConsolidationReport | null
  runs?: number
  [key: string]: unknown
}

/** `POST /api/v1/memories/consolidation/run` 的 data。 */
export interface MemoryConsolidationRunResult {
  scope: string
  result: MemoryConsolidationReport
}

/** 检索调试的一条命中：final 是混合总分，其余是各打分分量。 */
export interface MemoryRetrievalScoredRow {
  id: number
  content: string
  layer?: string
  category?: string
  scope_key?: string
  status?: string
  final: number
  semantic: number
  keyword: number
  importance: number
  confidence: number
  recency: number
  relationship: number
  topic_bonus: number
  temporal: number
  origin?: string
  [key: string]: unknown
}

/** `GET /api/v1/memories/retrieval-debug` 的 data：候选统计 + trace + 命中列表。 */
export interface MemoryRetrievalDebug {
  query?: string
  scopes?: string[]
  topics?: string[]
  candidates?: number
  keyword_candidates?: number
  semantic_candidates?: number
  merged_candidates?: number
  injected?: number
  dropped_by_guard?: number
  top_score?: number
  duration_ms?: number
  semantic_available?: boolean
  weights?: Record<string, unknown>
  min_final_score?: number
  results?: MemoryRetrievalScoredRow[]
  error?: string
  [key: string]: unknown
}

// ------------------------------------------------------------ Character

/** 后端 `Persona.identity`（GET/PATCH `/api/v1/character`）。 */
export interface CharacterIdentity {
  name: string
  nickname: string
  age: string
  birthday: string
  gender: string
  occupation: string
  location: string
  background: string
}

export interface CharacterPersonality {
  traits: string[]
  likes: string[]
  dislikes: string[]
  habits: string[]
  interests: string[]
}

export interface CharacterSpeakingStyle {
  language: string
  tone: string
  emoji: boolean
  kaomoji: boolean
  length_preference: string
  notes: string
}

export interface CharacterBehaviorRules {
  rules: string[]
}

/** 后端 `Persona.model_dump()` 的真实形状。 */
export interface CharacterPersona {
  name: string
  identity: CharacterIdentity
  personality: CharacterPersonality
  speaking_style: CharacterSpeakingStyle
  behavior_rules: CharacterBehaviorRules
  system_prompt: string
}

/**
 * `PATCH /api/v1/character` 的顶层键。
 *
 * 后端是顶层浅合并（`Persona.model_validate({**current, **data})`）：
 * 改动某个嵌套组时必须整组提交，否则未提交的兄弟字段会被默认值覆盖。
 */
export interface CharacterPersonaPatch {
  identity?: CharacterIdentity
  personality?: CharacterPersonality
  speaking_style?: CharacterSpeakingStyle
  behavior_rules?: CharacterBehaviorRules
  system_prompt?: string
}

/** 后端 `CharacterState.model_dump()`（PATCH 只提交改动字段）。 */
export interface CharacterState {
  mood?: string
  energy?: number
  activity?: string
  current_focus?: string
  location?: string
  social_state?: string
  schedule_state?: string
  current_goal?: string
  current_project?: string
  reason?: string
  mood_updated_at?: number
  updated_at?: number
  [key: string]: unknown
}

/** `GET /api/v1/character` 的 data。 */
export interface CharacterPayload {
  persona: CharacterPersona | null
  state: CharacterState | null
  source: 'config' | 'database' | string | null
}

// ------------------------------------------------- Character 导入导出

/** `GET /api/v1/character/export` 的 data：完整角色文档（可直接存盘 / 再导入）。 */
export interface CharacterExportDocument {
  settings: Record<string, unknown>
  tables: Record<string, Record<string, unknown>[]>
  counts: Record<string, number>
  format: string
  format_version: number
  exported_at: number
  schema_version?: number
  bible_hash?: string
  missing_tables?: string[]
  content_sha256?: string
  [key: string]: unknown
}

/** `POST /api/v1/character/import` 的 data.preview（dry-run 报告，不写库）。 */
export interface CharacterImportPreview {
  ok: boolean
  dry_run?: boolean
  will_reset?: boolean
  /** 各表将写入的行数。 */
  counts?: Record<string, number>
  /** 将覆盖的设置键列表。 */
  settings?: string[]
  rows?: number
  /** `ok === false` 时的原因（invalid_package / schema_too_new）。 */
  reason?: string
  inspect?: Record<string, unknown>
}

/** `POST /api/v1/character/import` 的 data。 */
export interface CharacterImportResult {
  preview: CharacterImportPreview
  applied: boolean
  /** 应用后的写入报告（backup / written / verified 等）。 */
  result?: Record<string, unknown>
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
