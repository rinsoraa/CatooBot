/**
 * 世界时间线页（W5 §16、§62、§71-§75）：事件字段、limit 走 URL、
 * 空态 / 重试、话题动作先确认再调用 topicAction。
 * 真实 world store + `globalThis.fetch` 信封 mock。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

import { useToast } from '@/composables/toast'
import WorldTimeline from '@/pages/character/WorldTimeline.vue'
import type { TopicRow, WorldTimelineRow } from '@/types/domain'

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

async function makeRouter(initial = '/character/world/timeline'): Promise<Router> {
  const router = createRouter({ history: createMemoryHistory(), routes: ROUTES })
  await router.push(initial)
  return router
}

// ------------------------------------------------------------------ fixture

function makeRow(overrides: Partial<WorldTimelineRow> = {}): WorldTimelineRow {
  return {
    ts: 1_700_000_000,
    event_type: 'world',
    summary: '她走到客厅',
    location: '客厅',
    action: 'wander',
    revisions: { world: 12, cognitive: 8 },
    ...overrides,
  }
}

function makeTopic(overrides: Partial<TopicRow> = {}): TopicRow {
  return {
    topic_id: 't-1',
    scope: 'user:10001',
    status: 'open',
    text: '周末去看电影',
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    ...overrides,
  }
}

interface HandlerConfig {
  items?: WorldTimelineRow[]
  topics?: TopicRow[]
  timelineFailures?: number
  topicReply?: MockReply
}

function makeHandler(config: HandlerConfig = {}): (request: MockRequest) => MockReply {
  let failures = config.timelineFailures ?? 0
  return (request) => {
    if (request.path === '/api/v1/world/timeline' && request.method === 'GET') {
      if (failures > 0) {
        failures -= 1
        return fail(500, 'internal.error', '读取时间线失败')
      }
      const limit = Number(request.query.get('limit') ?? '100')
      const items = (config.items ?? [makeRow()]).slice(0, limit)
      return ok({ enabled: true, items, count: items.length })
    }
    if (request.path === '/api/v1/world/topics' && request.method === 'GET') {
      const items = config.topics ?? [makeTopic()]
      return ok({ items, count: items.length })
    }
    if (request.path.startsWith('/api/v1/world/topics/') && request.method === 'POST') {
      return config.topicReply ?? ok({ done: true })
    }
    return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
  }
}

interface Mounted {
  wrapper: VueWrapper
  calls: MockRequest[]
  router: Router
}

async function mountTimeline(config: HandlerConfig = {}, initial = '/character/world/timeline'): Promise<Mounted> {
  const pinia = useFreshPinia()
  const calls = installFetch(makeHandler(config))
  const router = await makeRouter(initial)
  const wrapper = mount(WorldTimeline, { global: { plugins: [pinia, router] } })
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

describe('WorldTimeline 页', () => {
  it('渲染时间 / 事件 / 地点 / 动作 / revision 变化，并注明不做每秒一条', async () => {
    const { wrapper } = await mountTimeline()

    expect(wrapper.text()).toContain('按真实事件记录，不做每秒一条')
    expect(wrapper.findAll('[data-test="timeline-item"]').length).toBe(1)
    expect(wrapper.get('[data-test="timeline-time"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="timeline-event"]').text()).toBe('世界')
    expect(wrapper.get('[data-test="timeline-summary"]').text()).toBe('她走到客厅')
    expect(wrapper.get('[data-test="timeline-location"]').text()).toContain('客厅')
    expect(wrapper.get('[data-test="timeline-action"]').text()).toContain('wander')
    expect(wrapper.get('[data-test="timeline-revisions"]').text()).toBe('世界 #12 · 认知 #8')
  })

  it('limit 从 URL 读取并在切换后写回 URL、重新请求', async () => {
    const { wrapper, calls, router } = await mountTimeline({}, '/character/world/timeline?limit=50')

    const first = calls.find((call) => call.path === '/api/v1/world/timeline')
    expect(first?.query.get('limit')).toBe('50')

    await wrapper.get('[data-test="timeline-limit"]').setValue('200')
    await flushAll()

    expect(router.currentRoute.value.query.limit).toBe('200')
    const timelineCalls = calls.filter((call) => call.path === '/api/v1/world/timeline')
    expect(timelineCalls.at(-1)?.query.get('limit')).toBe('200')
  })

  it('空态与错误重试', async () => {
    const failing = await mountTimeline({ timelineFailures: 1 })
    expect(failing.wrapper.get('[role="alert"]').text()).toContain('读取时间线失败')

    await failing.wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()
    expect(failing.wrapper.findAll('[data-test="timeline-item"]').length).toBe(1)

    const empty = await mountTimeline({ items: [] })
    expect(empty.wrapper.get('.cb-empty__title').text()).toBe('暂无世界事件')
  })

  it('话题 resolve 需要确认：确认前无 POST，确认后调用 topicAction 并刷新列表', async () => {
    const { wrapper, calls } = await mountTimeline()

    expect(wrapper.get('[data-test="topic-text"]').text()).toBe('周末去看电影')

    await wrapper.get('[data-test="topic-resolve-t-1"]').trigger('click')
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('闭环')
    expect(calls.filter((call) => call.method === 'POST').length).toBe(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(1)
    expect(posts[0]?.path).toBe('/api/v1/world/topics/t-1/resolve')
    expect(calls.filter((call) => call.path === '/api/v1/world/topics').length).toBe(2)
  })

  it('话题 delete 取消不发送；确认文案说明不可撤销', async () => {
    const { wrapper, calls } = await mountTimeline()

    await wrapper.get('[data-test="topic-delete-t-1"]').trigger('click')
    expect(wrapper.text()).toContain('删除不可撤销')
    await wrapper.get('[data-test="cancel"]').trigger('click')
    await flushAll()
    expect(calls.filter((call) => call.method === 'POST').length).toBe(0)

    await wrapper.get('[data-test="topic-delete-t-1"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts.length).toBe(1)
    expect(posts[0]?.path).toBe('/api/v1/world/topics/t-1/delete')
  })

  it('话题动作失败时显示后端 message', async () => {
    const { wrapper } = await mountTimeline({
      topicReply: fail(409, 'topic.action_failed', '话题动作未生效'),
    })

    await wrapper.get('[data-test="topic-forget-t-1"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="topic-error"]').text()).toContain('话题动作未生效')
  })
})
