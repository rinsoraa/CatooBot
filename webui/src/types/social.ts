/** 社交域（`/api/v1/social/*`）——形状严格对应 W5 契约 §7.2.1。 */

export interface RelationshipRow {
  person_id: string
  display_name?: string
  relation_type: string
  trust: number
  familiarity: number
  closeness: number
  social_comfort: number
  interaction_count: number
  last_interaction_at: number | null
}

export interface SocialUser {
  person_id: string
  display_name: string
  qq: string
  nickname: string
  nickname_override: string
  interaction_count: number
  stage: string
  initiative_enabled: boolean
  notes: string
  tags: string[]
  last_seen: number | null
  relationship: RelationshipRow | null
  open_commitments: number
  recent_experience: string | null
  spaces: string[]
}

export interface CommitmentRow {
  commitment_id: string
  person_id: string
  person_name: string
  kind: string
  status: string
  strength: number
  priority: number
  summary: string
  target_activity: string
  time_hint: string
  earliest_at: number | null
  due_at: number | null
  created_at: number | null
  goal_id: string
  goal_status: string
}

export interface CommitmentDetail extends CommitmentRow {
  goal: Record<string, unknown> | null
  action: Record<string, unknown> | null
  outcome: {
    status: string
    resolved_at: number | null
    updated_at: number | null
    revision: number
    result: string
  } | null
}

export interface SocialSessionRow {
  person_id: string
  person_name: string
  social_space_id: string
  started_at: number | null
  last_activity_at: number | null
  turns: number
  interrupted: boolean
}

/** `POST /api/v1/sessions/{session_id}/clear` 的 data（清空会话上下文）。 */
export interface SessionClearResult {
  cleared: boolean
  session_id: string
}

export interface SocialSpaceRow {
  space_id: string
  kind: string
  qq_group_id: string
  name: string
  participants: string[]
  character_presence: boolean
  interest: number
}

export interface GroupRow {
  group_id: string
  name: string
  last_seen: number | null
  participation_enabled: boolean
  notes: string
  tags: string[]
  interaction_count: number
}

export interface SocialUserDetail {
  person: SocialUser
  relationship: RelationshipRow | null
  commitments: CommitmentRow[]
  experiences: Record<string, unknown>[]
  memories: Record<string, unknown>[] | null
  spaces: string[]
}

export interface Paged<T> {
  items: T[]
  total?: number | null
  count?: number | null
  limit?: number
  offset?: number
  next_cursor?: string | null
}

/** 承诺状态的中文（严格对应 `CommitmentStatus` 的真实取值，见 app/sandbox/commitments.py）。 */
export const COMMITMENT_LABELS: Record<string, string> = {
  pending: '待确认',
  scheduled: '已排期',
  active: '进行中',
  in_progress: '进行中',
  rescheduled: '已改期',
  completed: '已完成',
  cancelled: '已取消',
  declined: '已拒绝',
  expired: '已过期',
  broken: '未履行',
}

/** 仍然算「开放」的状态（用于详情页与筛选）。 */
export const OPEN_COMMITMENT_STATUSES = ['pending', 'scheduled', 'active', 'in_progress'] as const

export function commitmentLabel(status: string): string {
  return COMMITMENT_LABELS[status] ?? status
}

/** 关系阶段的中文（`RelationshipState.relation_type` 的真实取值）。 */
export const RELATION_LABELS: Record<string, string> = {
  stranger: '陌生',
  acquaintance: '认识',
  familiar: '熟悉',
  friend: '朋友',
  close_friend: '亲密朋友',
  core_friend: '核心好友',
}

export function relationLabel(value: string): string {
  return RELATION_LABELS[value] ?? value
}
