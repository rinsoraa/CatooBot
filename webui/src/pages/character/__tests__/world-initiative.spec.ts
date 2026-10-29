/**
 * Phase 7A §四十九：世界页的「意图（她想去做什么）」只读卡片。
 *
 * 钉死三件事：
 * 1. 只读展示 LifeIntent 的可审计字段（status / source / priority / created / expires /
 *    suppression_reason / related_goal / related_activity）与执行层 `NONE`；
 * 2. **没有任何执行入口**（没有 Execute / Send / Confirm / Run / Force 按钮），
 *    而且这一页发出的请求**全是 GET**；
 * 3. 没启用 / 降级 / 没有念头时如实说明，不编一个出来。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldInitiativeView, WorldData } from '@/types/domain'

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

const INTENT = {
  intent_id: 'INT-20261009-001',
  character_id: '罐头@1dae9716',
  intent_type: 'MINECRAFT_INTEREST',
  title: '有点想回 Minecraft 推进「小城」',
  description: '想起那个还没做完的项目。',
  source: 'GOAL',
  origin: 'goal:G-1(complete_project)',
  priority: 0.83,
  created_at: 1_700_000_000,
  expires_at: 1_700_021_600,
  related_activity: '',
  related_goal: 'G-1',
  related_memory: '',
  related_player: '',
  related_task: '',
  status: 'PROPOSED',
  suppression_reason: '',
  resolution_reason: '',
  confidence: 0.6,
  fingerprint: '罐头@1dae9716|MINECRAFT_INTEREST|g-1||mc_goal:g-1|471111',
  execution_class: 'VIRTUAL_ONLY',
  tags: ['goal', 'virtual_interest'],
}

function initiative(overrides: Partial<WorldInitiativeView> = {}): WorldInitiativeView {
  return {
    enabled: true,
    character_id: '罐头@1dae9716',
    execution_layer: 'NONE',
    degraded: '',
    last_check_at: 1_700_000_000,
    checks: 12,
    current: { ...INTENT },
    candidates: [
      {
        intent_type: 'MINECRAFT_INTEREST',
        title: INTENT.title,
        fingerprint: INTENT.fingerprint,
        priority: 0.83,
        allowed: true,
        reason: '',
        checks: [{ guard: 'SLEEPING', ok: true, detail: {} }],
      },
      {
        intent_type: 'REST',
        title: '有点累了，想歇会儿',
        fingerprint: 'f2',
        priority: 0.6,
        allowed: false,
        reason: 'LOWER_PRIORITY',
        checks: [],
      },
    ],
    recent: [{ ...INTENT }],
    suppressed: [
      {
        ...INTENT,
        intent_id: 'INT-20261009-002',
        intent_type: 'SOCIAL',
        title: '想看看大家在聊什么（服务器）',
        status: 'SUPPRESSED',
        suppression_reason: 'SLEEPING',
      },
    ],
    cooldown: {
      minutes: 20,
      seconds_remaining: 640,
      max_proposals_per_hour: 3,
      proposals_last_hour: 1,
    },
    guards: {},
    history: [],
    ...overrides,
  }
}

async function mountPage(view: WorldInitiativeView): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/initiative') return ok(view)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, {
    global: { plugins: [useFreshPinia(), await makeRouter()] },
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 意图（Phase 7A）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('只读展示当前意图与执行层 NONE', async () => {
    const { wrapper, calls } = await mountPage(initiative())
    expect(wrapper.find('[data-test="world-initiative"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-initiative-execution"]').text()).toBe('NONE')
    expect(wrapper.get('[data-test="world-initiative-enabled"]').text()).toContain('运行中')
    expect(wrapper.get('[data-test="world-initiative-current"]').text()).toContain('MINECRAFT_INTEREST')
    expect(wrapper.get('[data-test="world-initiative-cooldown"]').text()).toContain('640 秒')
    expect(wrapper.get('[data-test="world-initiative-cooldown"]').text()).toContain('1/3')
    expect(calls.some((call) => call.path === '/api/v1/world/initiative')).toBe(true)
  })

  it('展示候选裁决与被抑制的意图（含抑制原因）', async () => {
    const { wrapper } = await mountPage(initiative())
    const candidates = wrapper.get('[data-test="world-initiative-candidates"]').text()
    expect(candidates).toContain('已提出')
    expect(candidates).toContain('被抑制（LOWER_PRIORITY）')
    const suppressed = wrapper.get('[data-test="world-initiative-suppressed"]').text()
    expect(suppressed).toContain('SLEEPING')
  })

  it('没有任何执行入口（也不发出任何非 GET 请求）', async () => {
    const { wrapper, calls } = await mountPage(initiative())
    const html = wrapper.html()
    for (const forbidden of ['Execute', 'Send', 'Confirm', 'Run Intent', 'Force']) {
      expect(html).not.toContain(forbidden)
    }
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('没有念头时如实说明，不编一个出来', async () => {
    const { wrapper } = await mountPage(
      initiative({ current: null, candidates: [], suppressed: [], recent: [] }),
    )
    expect(wrapper.get('[data-test="world-initiative-empty"]').text()).toContain('没有任何念头')
  })

  it('意图层没启用时如实说明', async () => {
    const { wrapper } = await mountPage(
      initiative({ enabled: false, current: null, candidates: [], suppressed: [] }),
    )
    expect(wrapper.get('[data-test="world-initiative-enabled"]').text()).toContain('未启用')
  })

  it('降级时明说降级，不假装正常', async () => {
    const { wrapper } = await mountPage(initiative({ degraded: 'context_error' }))
    expect(wrapper.get('[data-test="world-initiative-enabled"]').text()).toContain('context_error')
  })
})
