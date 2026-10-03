/**
 * 当前角色页（W5 §6-§9、§62、§67-§75、§145）：状态条字段、当前状态、
 * Bible 字段绝不渲染、只读声明 / 查看世界链接、失败重试。
 * 真实 Pinia store + `globalThis.fetch` 信封 mock。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import type { CharacterPayload } from '@/api/world'
import { useToast } from '@/composables/toast'
import Character from '@/pages/character/Character.vue'
import type { WorldData } from '@/types/domain'
import type { OverviewData, RuntimeData } from '@/types/runtime'

interface MockRequest {
  url: string
  path: string
  method: string
  query: URLSearchParams
  body: unknown
}

interface MockReply {
  status?: number
  payload: unknown
}

function ok<T>(data: T): MockReply {
  return { payload: { ok: true, data, meta: { request_id: 't' } } }
}

function fail(status: number, code: string, message: string): MockReply {
  return { status, payload: { ok: false, error: { code, message }, meta: { request_id: 't' } } }
}

const originalFetch = globalThis.fetch

function installFetch(handler: (request: MockRequest) => MockReply): MockRequest[] {
  const calls: MockRequest[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
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

const ROUTES = [
  { path: '/character', component: { template: '<div />' } },
  { path: '/character/world', component: { template: '<div />' } },
  { path: '/character/world/timeline', component: { template: '<div />' } },
]

async function makeRouter(initial = '/character'): Promise<Router> {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(initial)
  return router
}

// ------------------------------------------------------------------ fixtures

function makeWorld(): WorldData {
  return {
    phase: 'day',
    location: '客厅',
    action: { name: '看书', progress: 0.4, started_at: 1_700_000_000, planned_end_at: 1_700_000_600 },
    modes: ['focus'],
    needs: { critical: [], pressing: ['hunger'] },
    world_revision: 12,
    cognitive_revision: 8,
  }
}

const PERSONA: Record<string, unknown> = {
  name: '小灯',
  identity: { name: '小灯', location: '房间', background: 'SECRET-BIBLE-背景' },
  personality: { traits: ['SECRET-TRAIT'] },
  speaking_style: { language: 'zh-CN', tone: 'SECRET-TONE' },
  behavior_rules: { rules: ['SECRET-RULE'] },
  system_prompt: 'SECRET-PROMPT',
}

function makeCharacter(): CharacterPayload {
  return {
    persona: PERSONA,
    state: {
      mood: 'happy',
      energy: 0.5,
      activity: '看书',
      current_focus: '',
      location: '',
      social_state: 'alone',
      schedule_state: 'awake',
    },
    source: 'database',
  }
}

function makeOverview(): OverviewData {
  return {
    qq: { online: true, self_id: 10001 },
    ai: { enabled: true, models_ok: 2, models_total: 3 },
    world: {},
    runtime: { uptime_seconds: 3720, scheduler: { running: true } },
    counts: {},
  }
}

const RUNTIME: RuntimeData = { uptime_seconds: 3720, scheduler: { running: true } }

function makeHandler(
  characterReply?: (request: MockRequest) => MockReply,
): (request: MockRequest) => MockReply {
  return (request) => {
    if (request.path === '/api/v1/character' && request.method === 'GET') {
      return characterReply ? characterReply(request) : ok(makeCharacter())
    }
    if (request.path === '/api/v1/world' && request.method === 'GET') return ok(makeWorld())
    if (request.path === '/api/v1/overview' && request.method === 'GET') return ok(makeOverview())
    if (request.path === '/api/v1/runtime' && request.method === 'GET') return ok(RUNTIME)
    return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
  }
}

interface Mounted {
  wrapper: VueWrapper
  calls: MockRequest[]
  router: Router
}

async function mountCharacter(
  reply: (request: MockRequest) => MockReply = makeHandler(),
  initial = '/character',
): Promise<Mounted> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial)
  const wrapper = mount(Character, { global: { plugins: [pinia, router] } })
  await flushAll()
  return { wrapper, calls, router }
}

enableAutoUnmount(afterEach)

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('Character 页', () => {
  it('状态条渲染 persona 名字与 overview 的在线 / Runtime / AI / QQ', async () => {
    const { wrapper, calls } = await mountCharacter()

    expect(wrapper.get('[data-test="character-name"]').text()).toBe('小灯')
    expect(wrapper.get('[data-test="character-online"]').text()).toBe('在线')
    expect(wrapper.get('[data-test="character-uptime"]').text()).toBe('1小时 2分')
    expect(wrapper.get('[data-test="character-ai"]').text()).toBe('已启用')
    expect(wrapper.get('[data-test="character-qq"]').text()).toBe('10001')

    expect(calls.some((call) => call.path === '/api/v1/character')).toBe(true)
    expect(calls.some((call) => call.path === '/api/v1/overview')).toBe(true)
  })

  it('「当前状态」卡渲染 mood / energy / activity，空字段显示「—」', async () => {
    const { wrapper } = await mountCharacter()

    expect(wrapper.get('[data-test="character-state-mood"]').text()).toBe('开心')
    expect(wrapper.get('[data-test="character-state-energy"]').text()).toBe('50%')
    expect(wrapper.get('[data-test="character-state-activity"]').text()).toBe('看书')
    expect(wrapper.get('[data-test="character-state-current_focus"]').text()).toBe('—')
    expect(wrapper.get('[data-test="character-state-location"]').text()).toBe('—')
  })

  it('Bible / 背景类 persona 字段绝不渲染，只在 Expert 折叠区标注「已隐藏」', async () => {
    const { wrapper } = await mountCharacter()

    const text = wrapper.text()
    expect(text).toContain('小灯')
    for (const secret of ['SECRET-BIBLE-背景', 'SECRET-TRAIT', 'SECRET-TONE', 'SECRET-RULE', 'SECRET-PROMPT']) {
      expect(text).not.toContain(secret)
    }

    const hidden = wrapper.get('[data-test="character-hidden"]').text()
    expect(hidden).toContain('已隐藏')
    expect(hidden).toContain('identity')
    expect(hidden).toContain('system_prompt')
    expect(wrapper.get('[data-test="character-source"]').text()).toContain('database')
  })

  it('渲染只读声明与 [查看世界] 链接，且不发出任何写请求', async () => {
    const { wrapper, calls } = await mountCharacter()

    expect(wrapper.get('[data-test="character-readonly"]').text()).toContain(
      '角色页面只展示运行状态；修改请到系统设置',
    )
    expect(wrapper.get('[data-test="character-view-world"]').attributes('href')).toBe(
      '/character/world',
    )
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('读取失败显示错误态，重试后恢复', async () => {
    let failures = 1
    const { wrapper, calls } = await mountCharacter(
      makeHandler(() => {
        if (failures > 0) {
          failures -= 1
          return fail(500, 'internal.error', '读取角色失败')
        }
        return ok(makeCharacter())
      }),
    )

    expect(wrapper.get('[role="alert"]').text()).toContain('读取角色失败')
    expect(wrapper.get('[data-test="character-name"]').text()).toBe('—')

    await wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="character-name"]').text()).toBe('小灯')
    expect(calls.filter((call) => call.path === '/api/v1/character').length).toBe(2)
  })
})
