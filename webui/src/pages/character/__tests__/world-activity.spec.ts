/**
 * Phase 6A：世界页的"当前活动"卡片（§三十六/§三十七）。
 *
 * 钉死三件事：
 * 1. 只读展示 Episode 的可审计字段（id / 状态 / 开始 / 计划结束 / 时长 / 来源 / 关联任务 / 原因）；
 * 2. **没有**任何修改入口（没有 start / cancel / extend 按钮）—— WebUI 不得修改 Episode；
 * 3. 没有活动能力 / 没有 Episode 时如实说明，不编一个出来。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldActivityView, WorldData } from '@/types/domain'

interface MockRequest {
  path: string
  method: string
  query: URLSearchParams
}

interface MockReply {
  status?: number
  payload: unknown
}

function ok<T>(data: T): MockReply {
  return { payload: { ok: true, data, meta: { request_id: 't' } } }
}

const originalFetch = globalThis.fetch

function installFetch(handler: (request: MockRequest) => MockReply): MockRequest[] {
  const calls: MockRequest[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const url = new URL(raw, 'http://localhost')
    const request: MockRequest = {
      path: url.pathname,
      method: (init?.method ?? 'GET').toUpperCase(),
      query: url.searchParams,
    }
    calls.push(request)
    const reply = handler(request)
    const status = reply.status ?? 200
    return {
      ok: status < 400,
      status,
      text: async () => JSON.stringify(reply.payload),
    } as unknown as Response
  }) as unknown as typeof fetch
  return calls
}

function useFreshPinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

async function flushAll(): Promise<void> {
  await flushPromises()
  await flushPromises()
  await flushPromises()
}

async function makeRouter(initial = '/character/world'): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/character', component: { template: '<div />' } },
      { path: '/character/world', component: { template: '<div />' } },
      { path: '/character/world/timeline', component: { template: '<div />' } },
    ],
  })
  await router.push(initial)
  return router
}

const WORLD: WorldData = {
  phase: 'day',
  location: '客厅',
  action: null,
  modes: [],
  needs: { critical: [], pressing: [], bands: {} },
  world_revision: 3,
  cognitive_revision: 1,
}

const EPISODE = {
  episode_id: 'ACT-20261008-007',
  character_id: '罐头@1dae9716',
  activity_type: 'virtual_life',
  activity_name: 'reading',
  status: 'ACTIVE',
  location: '客厅',
  social_state: 'alone',
  tags: [],
  started_at: 1_700_000_000,
  planned_end_at: 1_700_003_600,
  ended_at: 0,
  max_end_at: 1_700_009_000,
  min_duration: 1200,
  typical_duration: 3600,
  max_duration: 9000,
  transition_reason: 'SCHEDULED',
  source: 'ROUTINE',
  parent_episode_id: '',
  related_task_id: '',
  extension_count: 0,
  observation: {},
  created_at: 1_700_000_000,
  updated_at: 1_700_000_000,
}

function activity(overrides: Partial<WorldActivityView> = {}): WorldActivityView {
  return {
    enabled: true,
    character_id: '罐头@1dae9716',
    degraded: '',
    current: { ...EPISODE },
    recent: [{ ...EPISODE }],
    ...overrides,
  }
}

async function mountPage(view: WorldActivityView): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/activity') return ok(view)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, {
    global: { plugins: [useFreshPinia(), await makeRouter()] },
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 当前活动（Phase 6A）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('展示当前 Episode 的可审计字段', async () => {
    const { wrapper, calls } = await mountPage(activity())
    expect(wrapper.find('[data-test="world-activity"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-activity-id"]').text()).toBe('ACT-20261008-007')
    expect(wrapper.get('[data-test="world-activity-name"]').text()).toContain('reading')
    expect(wrapper.get('[data-test="world-activity-status"]').text()).toBe('ACTIVE')
    expect(wrapper.get('[data-test="world-activity-source"]').text()).toBe('ROUTINE')
    expect(wrapper.get('[data-test="world-activity-reason"]').text()).toBe('SCHEDULED')
    expect(wrapper.get('[data-test="world-activity-duration"]').text()).toContain('小时')
    expect(wrapper.get('[data-test="world-activity-task"]').text()).toBe('—')
    // 最近活动表 ≤10 条
    expect(wrapper.get('[data-test="world-activity-recent"]').text()).toContain('ACT-20261008-007')
    expect(calls.some((call) => call.path === '/api/v1/world/activity')).toBe(true)
  })

  it('展示决策只读字段（Phase 6B）：Elapsed / Window / Decision / Reason / 一致性', async () => {
    const { wrapper, calls } = await mountPage(
      activity({
        decision: {
          episode_id: 'ACT-20261008-007',
          current_activity: 'reading',
          status: 'ACTIVE',
          elapsed_seconds: 1200,
          planned_end_at: 1_700_003_600,
          transition_window_seconds: 300,
          transition_pending: true,
          extension_count: 1,
          max_extensions: 2,
          last_decision: {
            trace_id: 'dec_abc',
            decision: 'EXTEND',
            reason_code: 'TRANSITION_WINDOW',
            next_activity_hint: 'gaming',
            extension_seconds: 3600,
            trigger: 'TIME_EXPIRED',
            elapsed: 1200,
            guard_results: { extension: { ok: true } },
            decided_at: 1_700_001_200,
          },
        },
        consistency: { ok: false, checked: 1, errors: [{ rule: 'x' }], warnings: [] },
      }),
    )
    expect(wrapper.get('[data-test="world-activity-elapsed"]').text()).toContain('20 分钟')
    expect(wrapper.get('[data-test="world-activity-window"]').text()).toContain('5 分钟')
    expect(wrapper.get('[data-test="world-activity-window"]').text()).toContain('已进入')
    expect(wrapper.get('[data-test="world-activity-decision"]').text()).toBe('EXTEND')
    expect(wrapper.get('[data-test="world-activity-decision-reason"]').text()).toBe(
      'TRANSITION_WINDOW',
    )
    expect(wrapper.get('[data-test="world-activity-next-hint"]').text()).toBe('gaming')
    expect(wrapper.get('[data-test="world-activity-max-extensions"]').text()).toContain('1 / 2')
    expect(wrapper.get('[data-test="world-activity-consistency"]').text()).toContain('ERROR')
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('没有决策时如实显示"还没做过决策"（不编一个）', async () => {
    const { wrapper } = await mountPage(activity())
    expect(wrapper.get('[data-test="world-activity-decision"]').text()).toContain('还没做过决策')
    expect(wrapper.get('[data-test="world-activity-next-hint"]').text()).toBe('—')
    expect(wrapper.find('[data-test="world-activity-decision-reason"]').exists()).toBe(true)
  })

  it('没有任何修改 Episode 的入口（只读）', async () => {
    const { wrapper, calls } = await mountPage(activity())
    const html = wrapper.html()
    for (const forbidden of ['Start Episode', 'Cancel Episode', 'Extend Episode', 'Force Transition']) {
      expect(html).not.toContain(forbidden)
    }
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('没有 Episode 时如实说明，不编一个出来', async () => {
    const { wrapper } = await mountPage(activity({ current: null, recent: [] }))
    expect(wrapper.find('[data-test="world-activity-facts"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="world-activity-empty"]').text()).toContain('没有活动片段')
    expect(wrapper.find('[data-test="world-activity-recent"]').exists()).toBe(false)
  })

  it('活动能力没装配时也如实说明（enabled=false）', async () => {
    const { wrapper } = await mountPage(
      activity({ enabled: false, current: null, recent: [], character_id: '' }),
    )
    expect(wrapper.find('[data-test="world-activity-facts"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="world-activity-empty"]').exists()).toBe(true)
  })

  it('活动层降级时明说降级，不假装正常', async () => {
    const { wrapper } = await mountPage(activity({ degraded: 'OperationalError' }))
    expect(wrapper.get('[data-test="world-activity-degraded"]').text()).toContain('OperationalError')
  })
})
