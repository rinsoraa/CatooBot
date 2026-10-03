/** `/api/v1/social/*`：社交域的薄封装（只做路径、查询与类型）。 */

import { api } from '@/api/client'
import type {
  CommitmentDetail,
  CommitmentRow,
  GroupRow,
  Paged,
  RelationshipRow,
  SocialSessionRow,
  SocialSpaceRow,
  SocialUser,
  SocialUserDetail,
} from '@/types/social'

export const socialApi = {
  users(options: { q?: string; limit?: number; offset?: number } = {}) {
    return api.get<Paged<SocialUser>>('/social/users', {
      query: { q: options.q, limit: options.limit, offset: options.offset },
    })
  },

  user(personId: string) {
    return api.get<SocialUserDetail>(`/social/users/${encodeURIComponent(personId)}`)
  },

  saveUser(
    personId: string,
    payload: { nickname_override?: string; notes?: string; tags?: string[]; initiative_enabled?: boolean },
  ) {
    return api.patch<{ person: SocialUser }>(`/social/users/${encodeURIComponent(personId)}`, payload)
  },

  groups() {
    return api.get<Paged<GroupRow>>('/social/groups')
  },

  setGroupParticipation(groupId: string, enabled: boolean) {
    return api.patch<GroupRow>(`/social/groups/${encodeURIComponent(groupId)}`, {
      participation_enabled: enabled,
    })
  },

  sessions() {
    return api.get<{ active: boolean; items: SocialSessionRow[] }>('/social/sessions')
  },

  relationships(limit = 50) {
    return api.get<Paged<RelationshipRow>>('/social/relationships', { query: { limit } })
  },

  commitments(options: { status?: string; person?: string } = {}) {
    return api.get<Paged<CommitmentRow>>('/social/commitments', {
      query: { status: options.status, person: options.person },
    })
  },

  commitment(commitmentId: string) {
    return api.get<CommitmentDetail>(`/social/commitments/${encodeURIComponent(commitmentId)}`)
  },

  spaces() {
    return api.get<{ items: SocialSpaceRow[]; map: Record<string, string> }>('/social/spaces')
  },

  overview() {
    return api.get<Record<string, unknown>>('/social/overview')
  },
}
