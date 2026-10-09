/**
 * Phase 7C §十二：世界页的「任务提案（她准备怎样做）」只读卡片。
 *
 * 钉死四件事：
 * 1. 只读展示 §十二 列的每一项（Proposal ID / Source / Objective / Related Intent / Target /
 *    Required Capabilities / Capability Gaps / Risk Summary / Status / Expiry）与执行层 `NONE`；
 * 2. **没有任何执行入口**（没有 Execute / Confirm / Start / Run / Approve 按钮），
 *    而且这一页发出的请求**全是 GET**；
 * 3. 没启用 / 降级 / 没有提案时如实说明，不编一个出来；
 * 4. 目标只显示 UUID 后四位，绝不回显完整 UUID。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldData, WorldProposalsView } from '@/types/domain'

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

function proposals(overrides: Partial<WorldProposalsView> = {}): WorldProposalsView {
  return {
    enabled: true,
    execution_layer: 'NONE',
    minecraft_online: true,
    created_total: 2,
    merged_total: 1,
    degraded_reason: '',
    open: 1,
    proposals: [
      {
        proposal_id: 'TP-20261009-001',
        source: 'LIFE',
        objective: '想去 Minecraft 收集一点橡木 想起那片树林了',
        intent_id: 'INT-20261009-001',
        initiator: '罐头@1dae9716',
        target: {
          status: 'VERIFIED',
          player_name: '',
          server_id: '',
          uuid_suffix: '',
          reason: 'no_player_target',
        },
        required_capabilities: ['minecraft_find_blocks', 'minecraft_dig'],
        capability_gaps: [
          { capability_id: 'minecraft_dig', gap: 'PARTIALLY_SUPPORTED', reason: 'unavailable_now' },
        ],
        feasibility: 'PARTIALLY_SUPPORTED',
        risk_summary: { max_risk: 'MEDIUM', would_require_confirmation: true },
        status: 'NEEDS_MORE_INFORMATION',
        reason: 'capability_gap',
        created_at: 1_700_000_000,
        expires_at: 1_700_021_600,
        updated_at: 1_700_000_000,
        terminal: false,
      },
      {
        proposal_id: 'TP-20261009-002',
        source: 'USER',
        objective: '跟着我',
        intent_id: '',
        initiator: '2731431246',
        target: {
          status: 'VERIFIED',
          player_name: 'Rinsora',
          server_id: 'mc-1a2b3c',
          uuid_suffix: '9f3a',
          reason: '',
        },
        required_capabilities: ['minecraft_follow_player'],
        capability_gaps: [],
        feasibility: 'SUPPORTED',
        risk_summary: { max_risk: 'LOW', would_require_confirmation: false },
        status: 'READY_FOR_FUTURE_EXECUTION',
        reason: 'capabilities_supported',
        created_at: 1_700_000_100,
        expires_at: 1_700_021_700,
        updated_at: 1_700_000_100,
        terminal: false,
      },
    ],
    ...overrides,
  }
}

async function mountPage(
  view: WorldProposalsView,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/proposals') return ok(view)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, {
    global: { plugins: [useFreshPinia(), await makeRouter()] },
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 任务提案（Phase 7C）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('只读展示提案与执行层 NONE', async () => {
    const { wrapper, calls } = await mountPage(proposals())
    expect(wrapper.find('[data-test="world-proposals"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="world-proposals-execution"]').text()).toBe('NONE')
    expect(wrapper.get('[data-test="world-proposals-enabled"]').text()).toContain('运行中')
    expect(wrapper.get('[data-test="world-proposals-enabled"]').text()).toContain('1')
    expect(wrapper.get('[data-test="world-proposals-totals"]').text()).toContain('新提 2')
    expect(wrapper.get('[data-test="world-proposals-totals"]').text()).toContain('去重合并 1')
    expect(calls.some((call) => call.path === '/api/v1/world/proposals')).toBe(true)
  })

  it('逐条展示 §十二 要求的每一项', async () => {
    const { wrapper } = await mountPage(proposals())
    const rows = wrapper.findAll('[data-test="world-proposal-row"]')
    expect(rows).toHaveLength(2)
    const first = rows[0].text()
    expect(first).toContain('TP-20261009-001')
    expect(first).toContain('LIFE')
    expect(first).toContain('想去 Minecraft 收集一点橡木')
    expect(first).toContain('INT-20261009-001')
    expect(first).toContain('minecraft_find_blocks')
    expect(first).toContain('minecraft_dig：PARTIALLY_SUPPORTED')
    expect(first).toContain('NEEDS_MORE_INFORMATION')
    expect(first).toContain('最高 MEDIUM')
    expect(first).toContain('需要确认')
    const second = rows[1].text()
    expect(second).toContain('USER')
    expect(second).toContain('—（用户请求）')
    expect(second).toContain('Rinsora')
  })

  it('目标只显示 UUID 后四位，绝不回显完整 UUID / 凭据', async () => {
    const { wrapper } = await mountPage(proposals())
    const html = wrapper.html()
    expect(html).toContain('9f3a')
    expect(html).not.toContain('2731431246—')
    // 不需要指定玩家的提案如实说明
    expect(wrapper.findAll('[data-test="world-proposal-row"]')[0].text()).toContain(
      '不需要指定玩家',
    )
  })

  it('没有任何执行入口（也不发出任何非 GET 请求）', async () => {
    const { wrapper, calls } = await mountPage(proposals())
    const html = wrapper.html()
    for (const forbidden of ['Execute', 'Confirm', 'Start Task', 'Run Task', 'Approve']) {
      expect(html).not.toContain(forbidden)
    }
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('未启用时如实说明', async () => {
    const { wrapper } = await mountPage(
      proposals({ enabled: false, proposals: [], open: 0, execution_layer: 'NONE' }),
    )
    expect(wrapper.get('[data-test="world-proposals-enabled"]').text()).toContain('未启用')
    expect(wrapper.find('[data-test="world-proposals-empty"]').exists()).toBe(true)
  })

  it('降级时如实说明原因', async () => {
    const { wrapper } = await mountPage(proposals({ degraded_reason: 'OperationalError' }))
    expect(wrapper.get('[data-test="world-proposals-enabled"]').text()).toContain('降级')
    expect(wrapper.get('[data-test="world-proposals-enabled"]').text()).toContain(
      'OperationalError',
    )
  })

  it('没有提案时不编造一行出来', async () => {
    const { wrapper } = await mountPage(proposals({ proposals: [], open: 0 }))
    expect(wrapper.findAll('[data-test="world-proposal-row"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="world-proposals-empty"]').exists()).toBe(true)
  })

  it('接口失败时显示错误而不是空白', async () => {
    installFetch((request) => {
      if (request.path === '/api/v1/world') return ok(WORLD)
      return { status: 500, payload: { ok: false, error: { code: 'x', message: 'boom' } } }
    })
    const wrapper = mount(World, {
      global: { plugins: [useFreshPinia(), await makeRouter()] },
    })
    await flushAll()
    expect(wrapper.find('[data-test="world-proposals-error"]').exists()).toBe(true)
  })
})
