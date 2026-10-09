/**
 * Phase 6C §五十八：世界页的"接下来的打算（计划）"卡片。
 *
 * 钉死四件事：
 * 1. 展示计划的只读字段（Plan / 版本 / 视野 / 来源 / 触发 / 下一步 / 被选中 / 刷新 / 过期）；
 * 2. 展示候选、打分与**被拒原因**（§三十四/§五十八 要求"为什么不是别的"看得见）；
 * 3. **没有**任何强制选择入口（没有 force select、没有重排按钮，全部请求都是 GET）；
 * 4. 计划与"当前活动"是**两块**、措辞上明确"这是计划，不是现状"（§五十九/§七十二）。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldActivityPlanView, WorldData } from '@/types/domain'

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

async function makeRouter(): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/character', component: { template: '<div />' } },
      { path: '/character/world', component: { template: '<div />' } },
      { path: '/character/world/timeline', component: { template: '<div />' } },
    ],
  })
  await router.push('/character/world')
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

const ACTIVITY = {
  enabled: true,
  character_id: '罐头@1dae9716',
  degraded: '',
  current: null,
  recent: [],
}

const ITEM = {
  activity: 'gaming',
  planned_start: 1_700_003_600,
  planned_end: 1_700_007_200,
  duration: 3600,
  reason: 'FREE',
  priority: 0.3,
  anchor_id: '',
  goal_id: '',
  score: 3.27,
}

const PLAN_ITEM_ANCHOR = {
  ...ITEM,
  activity: 'eating',
  reason: 'ANCHOR',
  priority: 0.8,
  anchor_id: 'lunch',
}

function planView(overrides: Partial<WorldActivityPlanView> = {}): WorldActivityPlanView {
  return {
    enabled: true,
    dirty: false,
    planning_horizon_seconds: 14_400,
    refresh_min_seconds: 300,
    max_future_episodes: 6,
    refresh_count: 3,
    last_refresh_at: 1_700_000_000,
    last_result: { refreshed: true, reason: 'planned', trigger: 'episode_ended' },
    plan_id: 'PLAN-20261008-003',
    plan_version: 3,
    status: 'ACTIVE_PLAN',
    source: 'MIXED',
    trigger: 'episode_ended',
    stale: false,
    coverage_left_seconds: 8_400,
    seconds_since_last_refresh: 240,
    current_item: null,
    next: ITEM,
    upcoming: [PLAN_ITEM_ANCHOR, ITEM],
    selected: {
      activity: 'gaming',
      eligible: true,
      reason: '',
      score: 3.27,
      breakdown: { state_fit: 0.9, flexibility: 0.4 },
      anchor_id: '',
      goal_id: '',
      order: 1,
    },
    candidates: [
      {
        activity: 'gaming',
        eligible: true,
        reason: '',
        score: 3.27,
        breakdown: { state_fit: 0.9, flexibility: 0.4, initiative_fit: 0.8 },
        anchor_id: '',
        goal_id: '',
        intent_id: 'INT-20261009-005',
        order: 1,
      },
    ],
    rejected: [
      {
        activity: 'eating',
        eligible: false,
        reason: 'NOT_MOVEABLE',
        score: 0,
        breakdown: {},
        anchor_id: 'lunch',
        goal_id: '',
        order: 2,
      },
    ],
    anchors: [
      {
        anchor_id: 'lunch',
        activity: 'eating',
        target_time: '12:00',
        window_before: 2700,
        window_after: 2700,
        priority: 'meal',
        hard: true,
        days: null,
        note: '午饭（硬，±45 分钟窗口）',
        phase: 'before',
        fit: 0.5,
      },
    ],
    plan: {
      plan_id: 'PLAN-20261008-003',
      character_id: '罐头@1dae9716',
      plan_version: 3,
      status: 'ACTIVE_PLAN',
      generated_at: 1_700_000_000,
      horizon_start: 1_700_000_000,
      horizon_end: 1_700_014_400,
      horizon_seconds: 14_400,
      source: 'MIXED',
      trigger: 'episode_ended',
      content_hash: 'abc123',
      superseded_by: '',
      items: [ITEM, PLAN_ITEM_ANCHOR],
      candidates: [],
      rejected: [],
      constraints: { state: { energy: 0.8 } },
    },
    ...overrides,
  }
}

async function mountPage(
  plan: WorldActivityPlanView,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/activity') return ok(ACTIVITY)
    if (request.path === '/api/v1/world/activity/plan') return ok(plan)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, {
    global: { plugins: [useFreshPinia(), await makeRouter()] },
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 接下来的打算（Phase 6C）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('展示计划的只读字段（Plan / 版本 / 视野 / 来源 / 触发 / 下一步 / 刷新）', async () => {
    const { wrapper, calls } = await mountPage(planView())
    expect(wrapper.find('[data-test="world-plan"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-plan-id"]').text()).toBe('PLAN-20261008-003')
    expect(wrapper.get('[data-test="world-plan-version"]').text()).toBe('v3')
    expect(wrapper.get('[data-test="world-plan-horizon"]').text()).toContain('240 分钟')
    expect(wrapper.get('[data-test="world-plan-source"]').text()).toContain('MIXED')
    expect(wrapper.get('[data-test="world-plan-next"]').text()).toBe('gaming')
    expect(wrapper.get('[data-test="world-plan-selected"]').text()).toContain('3.27')
    expect(wrapper.get('[data-test="world-plan-refresh"]').text()).toContain('共 3 次')
    expect(wrapper.get('[data-test="world-plan-stale"]').text()).toContain('计划有效')
    expect(calls.some((call) => call.path === '/api/v1/world/activity/plan')).toBe(true)
    // Phase 7B §十二：意图对计划的影响是**只读可观测**的（一条软项 + 一条归因）
    expect(wrapper.find('[data-test="world-plan-candidate-intent"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="world-plan-item-intent"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-plan-candidates"]').text()).toContain('initiative_fit')
  })

  it('展示计划的只读字段（Plan / 版本 / 视野 / 来源 / 触发 / 下一步 / 刷新）', async () => {
    const { wrapper, calls } = await mountPage(planView())
    expect(wrapper.find('[data-test="world-plan"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-plan-id"]').text()).toBe('PLAN-20261008-003')
    expect(wrapper.get('[data-test="world-plan-version"]').text()).toBe('v3')
    expect(wrapper.get('[data-test="world-plan-horizon"]').text()).toContain('240 分钟')
    expect(wrapper.get('[data-test="world-plan-source"]').text()).toContain('MIXED')
    expect(wrapper.get('[data-test="world-plan-next"]').text()).toBe('gaming')
    expect(wrapper.get('[data-test="world-plan-selected"]').text()).toContain('3.27')
    expect(wrapper.get('[data-test="world-plan-refresh"]').text()).toContain('共 3 次')
    expect(wrapper.get('[data-test="world-plan-stale"]').text()).toContain('计划有效')
    expect(calls.some((call) => call.path === '/api/v1/world/activity/plan')).toBe(true)
    // Phase 7B §十二：意图对计划的影响是**只读可观测**的（一条软项 + 一条归因）
    expect(wrapper.find('[data-test="world-plan-candidate-intent"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="world-plan-item-intent"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="world-plan-candidates"]').text()).toContain('initiative_fit')

  })

  it('展示未来安排、候选打分与**被拒原因**（§五十八：为什么不是别的）', async () => {
    const { wrapper } = await mountPage(planView())
    const upcoming = wrapper.get('[data-test="world-plan-upcoming"]').text()
    expect(upcoming).toContain('eating')
    expect(upcoming).toContain('ANCHOR')
    expect(upcoming).toContain('lunch')
    const candidates = wrapper.get('[data-test="world-plan-candidates"]').text()
    expect(candidates).toContain('gaming')
    expect(candidates).toContain('3.27')
    expect(candidates).toContain('state_fit')
    const rejected = wrapper.get('[data-test="world-plan-rejected"]').text()
    expect(rejected).toContain('eating')
    expect(rejected).toContain('NOT_MOVEABLE')
    expect(wrapper.get('[data-test="world-plan-anchors"]').text()).toContain('lunch')
  })

  it('计划与现状分两块，并明说"这是计划、不是现状"（§五十九/§七十二）', async () => {
    const { wrapper } = await mountPage(planView())
    expect(wrapper.find('[data-test="world-activity"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="world-plan"]').exists()).toBe(true)
    const section = wrapper.get('[data-test="world-plan"]').text()
    expect(section).toContain('不是现状')
    expect(wrapper.get('[data-test="world-plan-readonly"]').text()).toContain('不能强制选择')
  })

  it('没有任何强制选择计划入口（只读，全部 GET）', async () => {
    const { wrapper, calls } = await mountPage(planView())
    const html = wrapper.html()
    for (const forbidden of ['Force Select', 'force-select', 'Refresh Plan', 'Replan now']) {
      expect(html).not.toContain(forbidden)
    }
    const section = wrapper.get('[data-test="world-plan"]')
    expect(section.findAll('button').length).toBe(0)
    expect(section.findAll('input').length).toBe(0)
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('没有生效计划时如实说明，不编一个出来', async () => {
    const { wrapper } = await mountPage(planView({ enabled: false, plan: null, next: null }))
    expect(wrapper.find('[data-test="world-plan-facts"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="world-plan-empty"]').text()).toContain('没有生效计划')
  })

  it('计划过期时明说"已过期"（不假装还有效）', async () => {
    const { wrapper } = await mountPage(planView({ stale: true }))
    expect(wrapper.get('[data-test="world-plan-stale"]').text()).toContain('已过期')
  })

  it('Phase 6C.1：Episode 延长后计划"待对齐"时如实显示（不假装已对齐）', async () => {
    const { wrapper } = await mountPage(planView({ dirty: true }))
    expect(wrapper.get('[data-test="world-plan-stale"]').text()).toContain('待对齐')
  })

  it('Phase 6C.1：对齐完成后显示"计划有效"', async () => {
    const { wrapper } = await mountPage(planView({ dirty: false, stale: false }))
    expect(wrapper.get('[data-test="world-plan-stale"]').text()).toBe('计划有效')
  })
})
