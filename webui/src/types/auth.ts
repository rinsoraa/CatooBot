/** 会话 / CSRF / 元信息（对应 `/api/v1/session`、`/csrf`、`/meta`）。 */

export interface SessionUser {
  name: string
}

export interface SessionData {
  user: SessionUser
  csrf_token: string
  permissions: { admin?: boolean }
  lang?: string
  theme?: string
}

export interface CsrfData {
  csrf_token: string
  header: string
  field: string
}

export interface MetaData {
  api: string
  app_version: string
  webui_version: string
  core_behavior_phase: string
  python: string
  platform: string
  started_at: number
  uptime_seconds: number
}

export type SessionStatus = 'unknown' | 'anonymous' | 'authenticated'
