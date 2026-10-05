/** Minecraft 连接层（Phase 1）的类型，与 docs/WEBUI_API_CONTRACT.md §7.5 对齐。 */

/** 显式状态机（任务书 §生命周期），镜像自 Bridge runtime。 */
export type MinecraftPhase =
  | 'DISCONNECTED'
  | 'CONNECTING'
  | 'AUTHENTICATING'
  | 'CONNECTED'
  | 'SPAWNING'
  | 'ONLINE'
  | 'DISCONNECTING'
  | 'ERROR'

export interface MinecraftPosition {
  x: number
  y: number
  z: number
}

export interface MinecraftConnection {
  status: MinecraftPhase
  session_id: string | null
  host: string | null
  port: number | null
  username: string | null
  auth_mode: string | null
  dimension: string | null
  position: MinecraftPosition | null
  health: number | null
  last_error: string | null
  kicked_reason: string | null
  connected_at: number | null
}

export interface MinecraftRuntimeInfo {
  running: boolean
  pid: number | null
  managed: boolean
  restarts: number
  down: boolean
  log_tail: string[]
}

export interface MinecraftOverview {
  enabled: boolean
  auth_configured: boolean
  runtime: MinecraftRuntimeInfo
  connection: MinecraftConnection
  /** Phase 3B：当前/最近一次动作（IDLE = 从未有动作）。 */
  action?: MinecraftActionView
  /** Phase 3C：Pathfinder 诊断（goal 类型 / 目标坐标 / 是否在移动）。 */
  pathfinder?: MinecraftPathfinderInfo
  /** Phase 3E：LLM Tool Debug（六个工具的风险/开关/是否允许 + Agent 上下文）。 */
  agent?: MinecraftAgentView
  last_event: Record<string, unknown> | null
}

// ---------------- Phase 3B · Action Runtime ----------------

export type MinecraftActionStatus =
  | 'IDLE'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMEOUT'

export interface MinecraftActionView {
  action: string | null
  action_id: string | null
  status: MinecraftActionStatus
  started_at: number | null
  finished_at: number | null
  elapsed_ms: number | null
  /** runtime 里进行中的动作数（含非互斥 chat）。 */
  active_count?: number
}

export interface MinecraftLookAtResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
}

export interface MinecraftStopResult {
  status: string
  cancelled: string[]
}

// ---------------- Phase 3C · Navigation (move_to) ----------------

export interface MinecraftPathfinderInfo {
  goal: string | null
  /** GoalFollow 时带 username；GoalNear 时只有坐标。 */
  target: { username?: string | null; x: number; y: number; z: number } | null
  distance: number | null
  moving: boolean
}

export interface MinecraftFollowPlayerResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
}

export interface MinecraftMoveToResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
  result?: {
    target: { x: number; y: number; z: number }
    final_position: { x: number; y: number; z: number }
    distance_to_target: number
  }
}

export interface MinecraftJoinResult {
  session_id: string
  status: MinecraftPhase
}

export interface MinecraftLeaveResult {
  ok?: boolean
  status: MinecraftPhase
}

// ---------------- Phase 2 · World Perception ----------------

export interface MinecraftWorldPlayer {
  name: string
  direction: string
  distance: number
  compass?: string
}

export interface MinecraftWorldEntity {
  type: string
  count: number
  direction?: string | null
  distance?: number
}

export interface MinecraftWorldPoi {
  type: string
  direction: string
  distance: number
  compass?: string
  pos?: { x: number; y: number; z: number } | null
}

export interface MinecraftTerrainItem {
  type: string
  direction: string
  distance?: number
  samples?: number
}

export interface MinecraftSemantic {
  captured_at: number
  self?: {
    location?: string | null
    dimension?: string | null
    position?: { x: number; y: number; z: number } | null
    health?: number | null
    food?: number | null
    game_mode?: string | null
    held_item?: string | null
    yaw?: number | null
  } | null
  environment?: Record<string, unknown> | null
  terrain?: MinecraftTerrainItem[]
  players?: MinecraftWorldPlayer[]
  entities?: MinecraftWorldEntity[]
  points_of_interest?: MinecraftWorldPoi[]
}

// ---------------- Phase 3E · LLM Agent（Tool Debug + Context） ----------------

export type MinecraftActionRisk = 'SAFE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'DESTRUCTIVE'

/** 一个 Minecraft Tool 的只读行：风险 / 是否注册启用 / 现在是否允许（§三十）。 */
export interface MinecraftAgentToolRow {
  name: string
  risk: MinecraftActionRisk
  enabled: boolean
  allowed: boolean
  /** 被拒时的稳定错误码（minecraft.offline / minecraft.action_busy …）。 */
  reason: string
}

export interface MinecraftAgentLastAction {
  action: string | null
  action_id: string | null
  status: MinecraftActionStatus
  code: string
  error: string
  result: Record<string, unknown> | null
  at: number
}

/** Agent 当前上下文（§十九/§四十八）：只读，来自事件 + 感知层实时读取。 */
export interface MinecraftAgentContext {
  online: boolean
  username: string
  current_action: { action: string | null; action_id: string | null; status: string } | null
  last_action: MinecraftAgentLastAction | null
  /** 最近一次成功动作的一句话描述（§二十二）。 */
  activity: string
  updated_at: number
  available?: boolean
  dimension?: string | null
  position?: MinecraftPosition | null
  biome?: string | null
  players?: { name: string; distance: number | null; direction: string | null }[]
}

export interface MinecraftAgentPolicy {
  enabled: boolean
  risk_flags: Record<MinecraftActionRisk, boolean>
  registered: Record<string, MinecraftActionRisk>
}

/** Phase 4A：一条待确认/已确认的 Minecraft 动作授权（只读投影）。 */
export interface MinecraftConfirmationView {
  confirmation_id: string
  session_id: string
  user_id: string
  tool: string
  risk: MinecraftActionRisk
  arguments_hash: string
  arguments: Record<string, unknown>
  summary: string
  created_at: number
  expires_at: number
  status: 'PENDING' | 'CONFIRMED' | 'EXPIRED' | 'CANCELLED' | 'CONSUMED'
}

export interface MinecraftConfirmationStoreView {
  ttl_seconds: number
  max_pending: number
  pending: MinecraftConfirmationView[]
  total: number
}

export interface MinecraftAgentView {
  enabled: boolean
  context: Partial<MinecraftAgentContext>
  policy: Partial<MinecraftAgentPolicy>
  tools: MinecraftAgentToolRow[]
  /** Phase 4A：待确认列表（只读）。 */
  confirmations?: MinecraftConfirmationStoreView
  /** Phase 4A：可信 Minecraft 玩家（LOW 及以上动作只对这些人执行）。 */
  trusted_players?: string[]
}

export interface MinecraftWorldView {
  available: boolean
  online: boolean
  reason?: string
  captured_at?: number | null
  age_seconds?: number | null
  layers?: Record<string, { age_seconds: number | null }>
  semantic?: MinecraftSemantic | null
  raw?: Record<string, unknown> | null
}
