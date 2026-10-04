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
  last_event: Record<string, unknown> | null
}

export interface MinecraftJoinResult {
  session_id: string
  status: MinecraftPhase
}

export interface MinecraftLeaveResult {
  ok?: boolean
  status: MinecraftPhase
}
