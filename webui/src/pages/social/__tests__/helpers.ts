/** `src/pages/social` 与 domain 卡片测试的共享工具：信封 mock + 内存路由 + fixtures。 */

import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'
import { vi } from 'vitest'

import type {
  CommitmentDetail,
  CommitmentRow,
  GroupRow,
  RelationshipRow,
  SocialSessionRow,
  SocialSpaceRow,
  SocialUser,
  SocialUserDetail,
} from '@/types/social'

export interface MockRequest {
  url: string
  path: string
  method: string
  query: URLSearchParams
  body: unknown
}

export interface MockReply {
  status?: number
  payload: unknown
}

export function ok<T>(data: T): MockReply {
  return { payload: { ok: true, data, meta: { request_id: 't' } } }
}

export function fail(status: number, code: string, message: string): MockReply {
  return { status, payload: { ok: false, error: { code, message }, meta: { request_id: 't' } } }
}

const originalFetch = globalThis.fetch

/** 把 `globalThis.fetch` 换成信封 mock，并返回所有请求记录。 */
export function installFetch(handler: (request: MockRequest) => MockReply): MockRequest[] {
  const calls: MockRequest[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, 'http://localhost')
    let body: unknown = null
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    const request: MockRequest = {
      url: raw,
      path: url.pathname,
      method: (init?.method ?? 'GET').toUpperCase(),
      query: url.searchParams,
      body,
    }
    calls.push(request)
    const reply = handler(request)
    const status = reply.status ?? 200
    return {
      ok: status < 400,
      status,
      text: async () => JSON.stringify(reply.payload),
    } as unknown as Response
  })
  globalThis.fetch = mock as unknown as typeof fetch
  return calls
}

export function restoreFetch(): void {
  globalThis.fetch = originalFetch
}

export function useFreshPinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

export async function makeRouter(initial: string, routes: RouteRecordRaw[]): Promise<Router> {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(initial)
  return router
}

export async function flushAll(): Promise<void> {
  await flushPromises()
  await flushPromises()
}

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// ------------------------------------------------------------------- fixtures

export function makeRelationship(overrides: Partial<RelationshipRow> = {}): RelationshipRow {
  return {
    person_id: 'p-1',
    display_name: '小艾',
    relation_type: 'friend',
    trust: 0.72,
    familiarity: 0.5,
    closeness: 0.4,
    social_comfort: 0.6,
    interaction_count: 18,
    last_interaction_at: 1_700_000_000,
    ...overrides,
  }
}

export function makeUser(overrides: Partial<SocialUser> = {}): SocialUser {
  return {
    person_id: 'p-1',
    display_name: '小艾',
    qq: '10001',
    nickname: '艾',
    nickname_override: '',
    interaction_count: 18,
    stage: 'friend',
    initiative_enabled: true,
    notes: '喜欢科幻片',
    tags: ['常聊'],
    last_seen: 1_700_000_000,
    relationship: makeRelationship(),
    open_commitments: 2,
    recent_experience: '一起看了电影',
    spaces: ['space-1'],
    ...overrides,
  }
}

export function makeCommitment(overrides: Partial<CommitmentRow> = {}): CommitmentRow {
  return {
    commitment_id: 'c-1',
    person_id: 'p-1',
    person_name: '小艾',
    kind: 'appointment',
    status: 'active',
    // 真实 wire 值是字符串枚举（explicit / soft）；types/social.ts 把它声明为 number。
    strength: 'explicit' as unknown as number,
    priority: 0.78,
    summary: '周日晚一起看电影',
    target_activity: 'watch_movie',
    time_hint: '周日晚上',
    earliest_at: 1_800_100_000,
    due_at: 1_800_200_000,
    created_at: 1_700_000_000,
    goal_id: 'g-1',
    goal_status: 'active',
    ...overrides,
  }
}

export function makeCommitmentDetail(overrides: Partial<CommitmentDetail> = {}): CommitmentDetail {
  return {
    ...makeCommitment(),
    goal: {
      goal_id: 'g-1',
      kind: 'appointment',
      status: 'active',
      reason: '答应了对方',
      priority: 0.78,
      progress: 0.4,
    },
    action: {
      instance_id: 'a-1',
      definition_id: 'watch_movie',
      name: '看电影',
      status: 'active',
      progress: 0.5,
      space_id: 'space-1',
      planned_end_at: 1_800_150_000,
    },
    outcome: null,
    ...overrides,
  }
}

export function makeGroup(overrides: Partial<GroupRow> = {}): GroupRow {
  return {
    group_id: 'g-1',
    name: '老友群',
    last_seen: 1_700_000_000,
    participation_enabled: true,
    notes: '',
    tags: [],
    interaction_count: 42,
    ...overrides,
  }
}

export function makeSession(overrides: Partial<SocialSessionRow> = {}): SocialSessionRow {
  return {
    person_id: 'p-1',
    person_name: '小艾',
    social_space_id: 'space-1',
    started_at: 1_700_000_000,
    last_activity_at: 1_700_000_500,
    turns: 6,
    interrupted: false,
    ...overrides,
  }
}

export function makeSpace(overrides: Partial<SocialSpaceRow> = {}): SocialSpaceRow {
  return {
    space_id: 'space-1',
    kind: 'private',
    qq_group_id: '',
    name: '私聊空间',
    participants: ['p-1'],
    character_presence: true,
    interest: 0.6,
    ...overrides,
  }
}

export function makeUserDetail(overrides: Partial<SocialUserDetail> = {}): SocialUserDetail {
  return {
    person: makeUser(),
    relationship: makeRelationship(),
    commitments: [makeCommitment()],
    experiences: [{ summary: '一起看了电影', created_at: 1_700_050_000, actors: ['p-1'] }],
    memories: [{ summary: '喜欢科幻片', category: 'preference', created_at: 1_700_060_000 }],
    spaces: ['space-1'],
    ...overrides,
  }
}
