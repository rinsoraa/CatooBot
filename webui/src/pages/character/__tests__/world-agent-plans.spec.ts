/**
 * Phase 7D §九：世界页的「任务计划」只读卡片。
 *
 * 钉死三件事：
 * 1. 只读展示计划（来源 / 目标 / 步骤 / 版本与重规划预算 / 关联任务实时状态）；
 * 2. **没有任何批准 / 执行 / 确认按钮**，这一页发出的请求**全是 GET**（批准只在 QQ）；
 * 3. 没有计划时如实说明，不编一个出来。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import World from '@/pages/character/World.vue'
import type { WorldAgentPlansView, WorldData } from '@/types/domain'

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

function plansView(overrides: Partial<WorldAgentPlansView> = {}): WorldAgentPlansView {
  return {
    enabled: true,
    execution_layer: 'TASK_RUNTIME',
    plans: [
      {
        plan_id: 'AP-20261009-001',
        source: 'USER',
        objective: '跟着我',
        status: 'LINKED',
        proposal_id: '',
        intent_id: '',
        task_id: 'task_1',
        target: {
          status: 'VERIFIED',
          player_name: 'Rinsora',
          server_id: 'mc-1a2b3c',
          uuid_suffix: '7d76',
          reason: '',
        },
        version: 1,
        steps: [{ step_id: 'step_1', tool: 'minecraft_follow_player', risk: 'LOW' }],
        checks: [{ check: 'identity', ok: true, detail: {} }],
        risk_summary: { max_risk: 'LOW' },
        reason: '',
        replans: 0,
        replan_budget: 2,
        approver_user_id: '',
        created_at: 1_700_000_000,
        expires_at: 1_700_000_600,
        task_state: 'PENDING_CONFIRMATION',
      },
      {
        plan_id: 'AP-20261009-002',
        source: 'LIFE',
        objective: '想去 Minecraft 收集一点橡木',
        status: 'READY_FOR_APPROVAL',
        proposal_id: 'TP-20261009-003',
        intent_id: 'INT-20261009-018',
        task_id: '',
        target: {
          status: 'VERIFIED',
          player_name: '',
          server_id: '',
          uuid_suffix: '',
          reason: 'no_player_target',
        },
        version: 1,
        steps: [
          { step_id: 'step_1', tool: 'minecraft_dig', risk: 'MEDIUM' },
          { step_id: 'step_2', tool: 'minecraft_pickup_item', risk: 'MEDIUM' },
        ],
        checks: [],
        risk_summary: { max_risk: 'MEDIUM' },
        reason: '',
        replans: 1,
        replan_budget: 2,
        approver_user_id: '',
        created_at: 1_700_000_100,
        expires_at: 1_700_000_700,
        task_state: '',
      },
    ],
    ...overrides,
  }
}

async function mountPage(
  view: WorldAgentPlansView,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request) => {
    if (request.path === '/api/v1/world') return ok(WORLD)
    if (request.path === '/api/v1/world/agent-plans') return ok(view)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'no' } } }
  })
  const wrapper = mount(World, {
    global: { plugins: [useFreshPinia(), await makeRouter()] },
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

describe('世界页 · 任务计划（Phase 7D）', () => {
  beforeEach(() => {
    globalThis.fetch = originalFetch
  })

  it('只读展示计划与关联任务的实时状态', async () => {
    const { wrapper, calls } = await mountPage(plansView())
    expect(wrapper.find('[data-test="world-agent-plans"]').exists()).toBe(true)
    const rows = wrapper.findAll('[data-test="world-agent-plan-row"]')
    expect(rows).toHaveLength(2)
    const first = rows[0].text()
    expect(first).toContain('AP-20261009-001')
    expect(first).toContain('USER')
    expect(first).toContain('LINKED')
    expect(first).toContain('PENDING_CONFIRMATION')
    expect(first).toContain('Rinsora')
    expect(first).toContain('minecraft_follow_player(LOW)')
    expect(calls.some((call) => call.path === '/api/v1/world/agent-plans')).toBe(true)
  })

  it('LIFE 计划显示批准预算与重规划状态', async () => {
    const { wrapper } = await mountPage(plansView())
    const second = wrapper.findAll('[data-test="world-agent-plan-row"]')[1].text()
    expect(second).toContain('LIFE')
    expect(second).toContain('READY_FOR_APPROVAL')
    expect(second).toContain('重规划 1/2')
  })

  it('没有任何批准 / 执行 / 确认按钮（也不发出任何非 GET 请求）', async () => {
    const { wrapper, calls } = await mountPage(plansView())
    const html = wrapper.html()
    for (const forbidden of ['Approve', 'Execute', 'Run Plan', 'Confirm Plan', 'Start Plan']) {
      expect(html).not.toContain(forbidden)
    }
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('没有计划时如实说明', async () => {
    const { wrapper } = await mountPage(plansView({ plans: [] }))
    expect(wrapper.findAll('[data-test="world-agent-plan-row"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="world-agent-plans-empty"]').exists()).toBe(true)
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
    expect(wrapper.find('[data-test="world-agent-plans-error"]').exists()).toBe(true)
  })
})
