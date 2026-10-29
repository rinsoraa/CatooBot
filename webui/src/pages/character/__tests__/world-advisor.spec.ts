/**
 * Phase 6D §八十九/§九十：世界页的「模型顾问」只读卡片。
 *
 * 钉死三件事：
 * 1. 只读展示顾问状态（启用/不可用、provider·model、超时、最近回执、延迟、已问 cycle 数）；
 * 2. **没有**任何"让模型再想一次 / 强制采纳 / 否决"的入口（全部请求都是 GET，卡片里没有按钮）；
 * 3. 没启用时如实说"未启用（纯规则）"，不假装模型在参与。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldActivityAdvisorView, WorldData } from '@/types/domain'

interface MockRequest {
  path: string
  method: string
  query: URLSearchParams
}
interface MockReply {
  status?: number
  payload: unknown
}

const originalFetch = globalThis.fetch

function ok<T>(data: T): MockReply {
  return { payload: { ok: true, data, meta: { request_id: 't' } } }
}

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
  world_revision: 1,
  cognitive_revision: 1,
}

const ACTIVITY = { enabled: true, character_id: 'c', degraded: '', current: null, recent: [] }
const PLAN = {
  enabled: true,
  dirty: false,
  planning_horizon_seconds: 14_400,
  refresh_min_seconds: 300,
  max_future_episodes: 6,
  refresh_count: 1,
  last_refresh_at: 0,
  plan: null,
  current_item: null,
  next: null,
  upcoming: [],
  candidates: [],
  rejected: [],
  selected: null,
  anchors: [],
  stale: false,
}

function advisorView(overrides: Partial<WorldActivityAdvisorView> = {}): WorldActivityAdvisorView {
  return {
    enabled: true,
    available: true,
    provider: 'router',
    model: 'deepseek-v4.1-flash',
    timeout_ms: 1500,
    calls: 3,
    last_failure: '',
    last_latency_ms: 412,
    attempted_cycles: 2,
    last_receipt: {
      episode_id: 'ACT-20261008-007',
      cycle_id: 'activity:ACT-20261008-007:1791477546',
      attempted: true,
      provider: 'router',
      model: 'deepseek-v4.1-flash',
      latency_ms: 412,
      proposal: { decision: 'extend', extension_seconds: 1200, reason_code: 'high_focus' },
      accepted: true,
      fallback_used: false,
    },
    ...overrides,
  }
}

async function mountPage(
  advisor: WorldActivityAdvisorView,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/activity') return ok(ACTIVITY)
    if (request.path === '/api/v1/world/activity/plan') return ok(PLAN)
    if (request.path === '/api/v1/world/activity/advisor') return ok(advisor)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, { global: { plugins: [useFreshPinia(), await makeRouter()] } })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 模型顾问（Phase 6D，只读）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('展示顾问状态、provider/model、超时与最近回执', async () => {
    const { wrapper, calls } = await mountPage(advisorView())
    expect(wrapper.find('[data-test="world-advisor"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-advisor-enabled"]').text()).toContain('已启用')
    expect(wrapper.get('[data-test="world-advisor-model"]').text()).toContain(
      'deepseek-v4.1-flash',
    )
    expect(wrapper.get('[data-test="world-advisor-timeout"]').text()).toContain('1500')
    expect(wrapper.get('[data-test="world-advisor-receipt"]').text()).toContain('extend')
    expect(wrapper.get('[data-test="world-advisor-receipt"]').text()).toContain('已采纳')
    expect(wrapper.get('[data-test="world-advisor-latency"]').text()).toContain('412')
    expect(wrapper.get('[data-test="world-advisor-cycles"]').text()).toContain('2')
    expect(calls.some((call) => call.path === '/api/v1/world/activity/advisor')).toBe(true)
  })

  it('规则回退时如实显示"规则回退 + 原因"（不假装被采纳）', async () => {
    const { wrapper } = await mountPage(
      advisorView({
        last_receipt: {
          attempted: true,
          proposal: { decision: 'extend', extension_seconds: 7200 },
          accepted: false,
          rejection_reason: 'RULE_REJECTED',
          fallback_used: true,
        },
      }),
    )
    const text = wrapper.get('[data-test="world-advisor-receipt"]').text()
    expect(text).toContain('规则回退')
    expect(text).toContain('RULE_REJECTED')
  })

  it('没启用时如实说"未启用（纯规则）"', async () => {
    const { wrapper } = await mountPage(
      advisorView({ enabled: false, available: false, calls: 0, last_receipt: {} }),
    )
    expect(wrapper.get('[data-test="world-advisor-enabled"]').text()).toContain('未启用')
    expect(wrapper.get('[data-test="world-advisor-receipt"]').text()).toContain('还没问过')
  })

  it('没有任何"强制采纳 / 让模型再想一次"的入口（只读，全部 GET）', async () => {
    const { wrapper, calls } = await mountPage(advisorView())
    const html = wrapper.html()
    for (const forbidden of [
      'Ask Model',
      'Force Accept',
      'Force Reject',
      'force-accept',
      'ask-model',
    ]) {
      expect(html).not.toContain(forbidden)
    }
    const section = wrapper.get('[data-test="world-advisor"]')
    expect(section.findAll('button').length).toBe(0)
    expect(section.findAll('input').length).toBe(0)
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })
})
