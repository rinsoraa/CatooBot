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
