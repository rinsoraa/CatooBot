/**
 * 世界页（W5 §10-§16、§62、§67-§75、§145）：总览 / needs_full / goals_full、
 * InterruptedAction 只读、附加块按需显示、Expert 折叠区、管理员操作二次确认。
 * 真实 world store + `globalThis.fetch` 信封 mock。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { useToast } from '@/composables/toast'
import World from '@/pages/character/World.vue'
import type { WorldData } from '@/types/domain'

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

async function makeRouter(initial = '/character/world'): Promise<Router> {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(initial)
  return router
}

// ------------------------------------------------------------------ fixture

function makeWorld(overrides: Partial<WorldData> = {}): WorldData {
  return {
    phase: 'day',
    location: '客厅',
    action: {
      name: '看书',
      definition_id: 'reading',
      detail: '读一本小说',
      reason_code: 'need_relief',
      space_id: 'living_room',
      goal_id: 'g-1',
      goal_step: '读完这一章',
      progress: 0.4,
      started_at: 1_700_000_000,
      planned_end_at: 1_700_000_600,
    },
    modes: ['focus'],
    needs: { critical: [], pressing: ['hunger'], bands: { hunger: 'strong' } },
    world_revision: 12,
    cognitive_revision: 8,
    needs_full: [
      {
        key: 'hunger',
        label: '饥饿',
        level: 0.82,
        band: 'strong',
        growth: 0.05,
        critical: false,
        pressing: true,
      },
      {
        key: 'sleepiness',
        label: '困倦',
        level: 0.4,
        band: 'soft',
        growth: 0.02,
        critical: false,
        pressing: false,
      },
    ],
    goals_full: [
      {
        goal_id: 'g-1',
        title: '读完这本书',
        status: 'active',
        priority: 0.8,
        progress: 0.35,
        current_step: '翻到下一页',
        target_commitment: 'c-1',
        dedupe_key: 'd-1',
      },
    ],
    spaces: [{ space_id: 'living_room', name: '客厅', kind: 'room' }],
    objects: [{ object_id: 'obj-1', name: '沙发', kind: 'furniture', space_id: 'living_room' }],
    inventories: { fridge: { name: '冰箱', items: { 牛奶: 2 }, capacity: 10 } },
    pet: {
      name: '咪咪',
      species: '猫',
      activity: 'sleeping',
      location: '沙发',
      mood: '平静',
      hunger: 0.3,
      energy: 0.6,
      affection: 0.7,
      line: '咪咪在沙发上打盹',
    },
    social_spaces: [
      { space_id: 'sp-1', name: '线上读书会', kind: 'simulated', participants: ['p-1'], character_presence: 'active' },
    ],
    action_defs: [{ definition_id: 'reading', name: '看书', space_id: 'living_room', needs: ['focus'] }],
    modes_defs: [{ key: 'focus', label: '专注', description: '安静做事' }],
    interrupted: {
      active: true,
      definition_id: 'gaming',
      remaining_minutes: 12.5,
      reason: '有人找她聊天',
      progress: 0.4,
    },
    ...overrides,
  }
}

const EMPTY_EXTRAS: Partial<WorldData> = {
  spaces: [],
  objects: [],
  inventories: {},
  pet: null,
  social_spaces: [],
}

interface MountConfig {
  world?: WorldData
  controlReply?: MockReply
  behaviorTestReply?: MockReply
  behaviorPreviewReply?: MockReply
  behaviorTriggerReply?: (action: string) => MockReply
}

function makePreviewResult() {
  return {
    ok: true,
    time: '深夜 23:30',
    period: 'night',
    sleeping: false,
    dnd: true,
    state: { mood: 'quiet', activity: '看书', energy: 0.4 },
    relationship: 'familiar',
    delay: 3.5,
    chunks: ['好呀。', '等我看一下。'],
    initiative: {
      would_consider: true,
      blocked_by: '',
      reason: '未完成的项目',
      probability: 0.35,
      simulated_roll: 0.12,
    },
  }
}

function makeHandler(config: MountConfig = {}): (request: MockRequest) => MockReply {
  const world = config.world ?? makeWorld()
  return (request) => {
    if (request.path === '/api/v1/world' && request.method === 'GET') return ok(world)
    if (request.path.startsWith('/api/v1/world/control/') && request.method === 'POST') {
      return config.controlReply ?? ok({ ok: true, reason: '', phase: 'paused' })
    }
    if (request.path === '/api/v1/behavior/test-response' && request.method === 'POST') {
      return (
        config.behaviorTestReply ??
        ok({
          ok: true,
          reply: '今天挺好的，你呢？',
          delay: 1.5,
          chunks: ['今天挺好的，', '你呢？'],
          state: { mood: 'happy', activity: '看书', energy: 0.6 },
          time: '下午',
        })
      )
    }
    if (request.path === '/api/v1/behavior/preview' && request.method === 'POST') {
      return config.behaviorPreviewReply ?? ok(makePreviewResult())
    }
    if (request.path.startsWith('/api/v1/behavior/triggers/') && request.method === 'POST') {
      const action = request.path.split('/').pop() ?? ''
      return (
        config.behaviorTriggerReply?.(action) ??
        ok({ action, result: { ok: true, state: { mood: 'happy', energy: 0.5, activity: '看书' } } })
      )
    }
    return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
  }
}

interface Mounted {
  wrapper: VueWrapper
  calls: MockRequest[]
  router: Router
}

async function mountWorld(config: MountConfig = {}): Promise<Mounted> {
  const pinia = useFreshPinia()
  const calls = installFetch(makeHandler(config))
  const router = await makeRouter()
  const wrapper = mount(World, { global: { plugins: [pinia, router] } })
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

describe('World 页', () => {
  it('渲染世界总览、needs_full 与 goals_full', async () => {
    const { wrapper } = await mountWorld()

    expect(wrapper.get('[data-test="world-action"]').text()).toBe('看书')
    expect(wrapper.get('[data-test="world-location"]').text()).toBe('客厅')

    expect(wrapper.findAll('[data-test="need-row"]').length).toBe(2)
    expect(wrapper.get('[data-test="need-label"]').text()).toBe('饥饿')
    expect(wrapper.findAll('[data-test="need-band"]')[0]?.text()).toBe('强烈')

    expect(wrapper.findAll('[data-test="goal-card"]').length).toBe(1)
    expect(wrapper.get('[data-test="goal-title"]').text()).toBe('读完这本书')
    expect(wrapper.get('[data-test="goal-status"]').text()).toBe('进行中')
    expect(wrapper.get('[data-test="goal-percent"]').text()).toBe('35%')
  })

  it('interrupted.active 时渲染只读打断卡（definition / remaining / reason / progress）', async () => {
    const { wrapper } = await mountWorld()
    const card = wrapper.get('[data-test="world-interrupted"]')

    expect(card.get('[data-test="interrupted-definition"]').text()).toBe('gaming')
    expect(card.get('[data-test="interrupted-remaining"]').text()).toContain('12.5')
    expect(card.get('[data-test="interrupted-reason"]').text()).toBe('有人找她聊天')
    expect(card.get('[data-test="interrupted-progress"]').text()).toBe('40%')
    expect(card.get('[data-test="interrupted-readonly"]').text()).toContain('只读')
  })

  it('没有 interrupted 时打断卡不显示', async () => {
    const { wrapper } = await mountWorld({ world: makeWorld({ interrupted: null }) })
    expect(wrapper.find('[data-test="world-interrupted"]').exists()).toBe(false)
  })

  it('空间 / 物件 / 库存 / 宠物 / 社交空间有数据时显示', async () => {
    const { wrapper } = await mountWorld()

    expect(wrapper.get('[data-test="space-name"]').text()).toBe('客厅')
    expect(wrapper.get('[data-test="object-name"]').text()).toBe('沙发')
    expect(wrapper.get('[data-test="inventory-name"]').text()).toBe('冰箱')
    expect(wrapper.get('[data-test="world-pet"]').text()).toContain('咪咪')
    expect(wrapper.get('[data-test="social-space-name"]').text()).toBe('线上读书会')
  })

  it('空间 / 物件 / 库存 / 宠物无数据时不渲染对应卡片，社交空间显示空态', async () => {
    const { wrapper } = await mountWorld({ world: makeWorld(EMPTY_EXTRAS) })

    for (const test of ['world-spaces', 'world-objects', 'world-inventory', 'world-pet']) {
      expect(wrapper.find(`[data-test="${test}"]`).exists()).toBe(false)
    }
    expect(wrapper.get('[data-test="world-social-spaces"]').text()).toContain('暂无社交空间')
  })

  it('Expert 折叠区包含 action_defs、modes_defs 与原始 JSON', async () => {
    const { wrapper } = await mountWorld()
    const expert = wrapper.get('[data-test="world-expert"]')

    expect(expert.get('[data-test="world-action-defs"]').text()).toContain('reading')
    expect(expert.get('[data-test="world-modes-defs"]').text()).toContain('focus')
    expect(expert.get('[data-test="world-expert-json"]').text()).toContain('"needs_full"')
  })

  it('管理员操作：确认前不发 POST，确认后才按 action 作为 confirm 调用控制接口', async () => {
    const { wrapper, calls } = await mountWorld()

    expect(calls.filter((call) => call.method === 'POST').length).toBe(0)

    await wrapper.get('[data-test="world-control-reset"]').trigger('click')
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('重置不可撤销')
    expect(calls.filter((call) => call.method === 'POST').length).toBe(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(1)
    expect(posts[0]?.path).toBe('/api/v1/world/control/reset')
    expect(posts[0]?.body).toEqual({ confirm: 'reset' })
    expect(calls.filter((call) => call.path === '/api/v1/world' && call.method === 'GET').length).toBe(2)
  })

  it('暂停操作确认后发送 confirm=pause，取消则不发送', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="world-control-pause"]').trigger('click')
    await wrapper.get('[data-test="cancel"]').trigger('click')
    await flushAll()
    expect(calls.filter((call) => call.method === 'POST').length).toBe(0)

    await wrapper.get('[data-test="world-control-pause"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(1)
    expect(posts[0]?.path).toBe('/api/v1/world/control/pause')
    expect(posts[0]?.body).toEqual({ confirm: 'pause' })
  })

  it('控制失败时显示后端 message', async () => {
    const { wrapper } = await mountWorld({
      controlReply: fail(500, 'sandbox.control_failed', '控制失败：世界正忙'),
    })

    await wrapper.get('[data-test="world-control-reinitialize"]').trigger('click')
    expect(wrapper.text()).toContain('重新初始化不可撤销')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="world-control-error"]').text()).toContain('控制失败：世界正忙')
  })
})

describe('World 页 · 行为调试（v0.8 对话行为迁移）', () => {
  it('试跑回复：提交原文并渲染 reply / delay / chunks / state', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="behavior-test-input"] textarea').setValue('今天过得怎么样？')
    await wrapper.get('[data-test="behavior-test-run"]').trigger('click')
    await flushAll()

    const post = calls.find((call) => call.path === '/api/v1/behavior/test-response')
    expect(post?.method).toBe('POST')
    expect(post?.body).toEqual({ text: '今天过得怎么样？' })

    const result = wrapper.get('[data-test="behavior-test-result"]')
    expect(result.get('[data-test="behavior-test-reply"]').text()).toContain('今天挺好的')
    expect(result.get('[data-test="behavior-test-delay"]').text()).toContain('1.5')
    expect(result.get('[data-test="behavior-test-chunks"]').text()).toContain('你呢？')
    expect(result.get('[data-test="behavior-test-state"]').text()).toContain('mood=happy')
  })

  it('空文本不发请求，只提示先输入', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="behavior-test-run"]').trigger('click')
    await flushAll()

    expect(calls.filter((call) => call.path === '/api/v1/behavior/test-response')).toHaveLength(0)
    const { items } = useToast()
    expect(items.value.some((item) => item.kind === 'warning' && item.message.includes('先输入'))).toBe(true)
  })

  it('后端拒绝试跑时展示其 message', async () => {
    const { wrapper } = await mountWorld({
      behaviorTestReply: fail(400, 'behavior.text_required', '测试回复需要 text'),
    })

    await wrapper.get('[data-test="behavior-test-input"] textarea').setValue('你好')
    await wrapper.get('[data-test="behavior-test-run"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="behavior-test-error"]').text()).toContain('测试回复需要 text')
    expect(wrapper.find('[data-test="behavior-test-result"]').exists()).toBe(false)
  })

  it('mood_up 直接触发：无需确认即可 POST', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="behavior-trigger-mood_up"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.path === '/api/v1/behavior/triggers/mood_up')
    expect(posts).toHaveLength(1)
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="behavior-trigger-result"]').text()).toContain('mood=happy')
  })

  it('reset_state：确认前零请求，确认后才发出 POST', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="behavior-trigger-reset_state"]').trigger('click')
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('不可撤销')
    expect(calls.filter((call) => call.path.startsWith('/api/v1/behavior/triggers/'))).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.path === '/api/v1/behavior/triggers/reset_state')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.method).toBe('POST')
    expect(posts[0]?.body).toBeNull()
  })

  it('触发失败展示后端 message', async () => {
    const { wrapper } = await mountWorld({
      behaviorTriggerReply: () => fail(400, 'behavior.trigger_unknown', '未知的行为动作：nope'),
    })

    await wrapper.get('[data-test="behavior-trigger-mood_down"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="behavior-trigger-error"]').text()).toContain('未知的行为动作')
  })

  it('行为模拟器：提交表单字段并渲染延迟 / 主动判定', async () => {
    const { wrapper, calls } = await mountWorld()

    await wrapper.get('[data-test="behavior-preview-sim-time"] input').setValue('23:30')
    await wrapper.get('[data-test="behavior-preview-mood"] input').setValue('quiet')
    await wrapper.get('[data-test="behavior-preview-activity"] input').setValue('看书')
    await wrapper.get('[data-test="behavior-preview-topic"] input').setValue('未完成的项目')
    await wrapper.get('[data-test="behavior-preview-run"]').trigger('click')
    await flushAll()

    const post = calls.find((call) => call.path === '/api/v1/behavior/preview')
    expect(post?.method).toBe('POST')
    expect(post?.body).toEqual({
      sim_time: '23:30',
      mood: 'quiet',
      activity: '看书',
      relationship: 'familiar',
      topic: '未完成的项目',
    })

    const result = wrapper.get('[data-test="behavior-preview-result"]')
    expect(result.get('[data-test="behavior-preview-delay"]').text()).toContain('3.5')
    expect(result.get('[data-test="behavior-preview-chunks"]').text()).toContain('等我看一下。')
    expect(result.get('[data-test="behavior-preview-initiative"]').text()).toContain('会考虑')
  })
})
