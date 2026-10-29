/** Runtime / World / Scheduler / Realtime 的读取模型（W2 `/api/v1` 实际形状）。 */

export interface LastEventRef {
  channel?: string
  level?: string
  message?: string
}

export interface ActionInfo {
  /** 动作定义名（后端可能为 null：没有当前动作时） */
  name: string | null
  progress?: number | null
  started_at?: number | null
  planned_end_at?: number | null
}

export interface WorldSnapshot {
  phase?: string | null
  location?: string | null
  action?: ActionInfo | null
  modes?: string[]
  needs?: { critical?: string[]; pressing?: string[] }
  world_revision?: number | null
  cognitive_revision?: number | null
  session?: { active?: boolean; person_id?: string | null } | null
  interrupted?: boolean | null
  [key: string]: unknown
}

export interface QqSnapshot {
  online?: boolean | null
  self_id?: number | null
  messages_received?: number | null
  users?: number | null
  groups?: number | null
  sessions?: number | null
  last_event_at?: number | null
}

export interface AiModelStatus {
  name: string
  provider?: string
  model?: string
  enabled?: boolean
  in_cooldown?: boolean
  cooldown_until?: number | null
  last_error?: string | null
  last_success?: number | null
  failure_count?: number
}

export interface AiSnapshot {
  enabled?: boolean | null
  current_model?: string | null
  models_ok?: number | null
  models_total?: number | null
  requests?: number | null
  errors?: number | null
  rate_limited?: number | null
}

export interface SchedulerSnapshot {
  running?: boolean | null
  interval_seconds?: number | null
  ticks?: number | null
  catchups?: number | null
  last_tick_at?: number | null
}

export interface RuntimeSnapshot {
  scheduler?: SchedulerSnapshot | null
  watchdog?: { last_lag_ms?: number | null; max_lag_ms?: number | null; lag_events?: number | null } | null
  database?: { connected?: boolean | null } | null
  hub?: { subscribers?: number | null; published?: number | null; dropped?: number | null } | null
  //: real process facts live under `process` in the API payload; the flat
  //: `uptime_seconds` stays for older payloads/unit fixtures.
  process?: {
    uptime_seconds?: number | null
    started_at?: number | null
    version?: string
    python?: string
  } | null
  uptime_seconds?: number | null
}

export interface CountsSnapshot {
  memories?: number | null
  experiences?: number | null
  goals_open?: number | null
  commitments_open?: number | null
}

/** `GET /api/v1/overview` 的 data。 */
export interface OverviewData {
  qq: QqSnapshot
  ai: AiSnapshot
  world: WorldSnapshot
  runtime: RuntimeSnapshot
  counts: CountsSnapshot
}

/** `GET /api/v1/runtime` 的 data（比 overview.runtime 更细）。 */
export interface RuntimeData extends RuntimeSnapshot {
  [key: string]: unknown
}

/** `/ws/events` 的帧与 topic。 */
export type RealtimeTopic = 'hello' | 'narration' | 'log' | 'status' | 'world' | 'scheduler'

export interface RealtimeEvent<T = unknown> {
  topic: RealtimeTopic
  ts: number
  data: T
}

export interface RealtimeLogEntry {
  ts: number
  topic: RealtimeTopic
  text: string
}

export type ConnectionState = 'connecting' | 'connected' | 'reconnecting' | 'closed'
