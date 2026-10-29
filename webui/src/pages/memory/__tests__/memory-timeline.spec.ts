/** 记忆时间线（W5 §108）：日期分组、Expert episode_key、limit 走 URL、空态与重试。 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import MemoryTimeline from '@/pages/memory/MemoryTimeline.vue'
import type { MemoryRow } from '@/types/domain'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

function makeRow(id: number, overrides: Partial<MemoryRow> = {}): MemoryRow {
  return {
    memory_id: id,
    content: `记忆内容 ${id}`,
    summary: `记忆 ${id}`,
    layer: 'episodic',
    category: 'event',
    scope_key: 'user:10001',
    status: 'active',
    importance: 0.5,
    confidence: 0.7,
    created_at: 1700000000,
    updated_at: 1700000100,
    provenance: {},
    ...overrides,
  }
}

function dateKey(seconds: number): string {
  const date = new Date(seconds * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

interface RecordedCall {
  path: string
  query: URLSearchParams
}

function timelineCalls(calls: MockRequest[]): RecordedCall[] {
  return calls
    .map((call) => {
      const url = new URL(call.url, 'http://localhost')
      return { path: url.pathname, query: url.searchParams }
    })
    .filter((call) => call.path === '/api/v1/memories/timeline')
}

async function makeRouter(initial: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/memory', name: 'memory', component: { template: '<div />' } },
      { path: '/memory/timeline', name: 'memory-timeline', component: { template: '<div />' } },
    ],
  })
  await router.push(initial)
  await router.isReady()
  return router
}

async function mountTimeline(
  handler: (request: MockRequest) => MockReply,
  initial = '/memory/timeline',
): Promise<{ wrapper: VueWrapper; calls: MockRequest[]; router: Router }> {
  const calls = installFetch(handler)
  const pinia = useFreshPinia()
  const router = await makeRouter(initial)
  const wrapper = mount(MemoryTimeline, { global: { plugins: [pinia, router] } })
  await flushAll()
  return { wrapper, calls, router }
}

describe('MemoryTimeline', () => {
  it('groups items by day and shows summary, type and person', async () => {
    const firstDay = 1700000000
    const secondDay = firstDay + 172800
    const items = [
      makeRow(1, { created_at: firstDay }),
      makeRow(2, { created_at: firstDay + 60 }),
      makeRow(3, { created_at: secondDay }),
    ]
    const { wrapper } = await mountTimeline((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories/timeline') return ok({ items })
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    const dates = wrapper.findAll('[data-test="timeline-date"]').map((node) => node.text())
    expect(dates).toEqual([dateKey(firstDay), dateKey(secondDay)])
    expect(wrapper.findAll('[data-test="timeline-item"]')).toHaveLength(3)

    const firstItem = wrapper.findAll('[data-test="timeline-item"]')[0]
    expect(firstItem?.text()).toContain('记忆 1')
    expect(firstItem?.text()).toContain('情景')
    expect(firstItem?.text()).toContain('事件')
    expect(firstItem?.text()).toContain('用户 10001')
  })

  it('reveals episode_key only when expert mode is checked', async () => {
    const { wrapper } = await mountTimeline((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories/timeline') {
        return ok({ items: [makeRow(1, { provenance: { episode_key: 'ep-9' } })] })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.find('[data-test="memory-episode"]').exists()).toBe(false)

    await wrapper.get('[data-test="timeline-expert"]').setValue(true)
    expect(wrapper.get('[data-test="memory-episode"]').text()).toContain('ep-9')
  })

  it('reads limit from the URL and writes limit changes back to it', async () => {
    const { wrapper, calls, router } = await mountTimeline((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories/timeline') return ok({ items: [] })
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    }, '/memory/timeline?limit=50')

    expect(timelineCalls(calls)[0]?.query.get('limit')).toBe('50')

    await wrapper.get('[data-test="timeline-limit"]').setValue('100')
    await flushAll()

    expect(router.currentRoute.value.query.limit).toBe('100')
    expect(timelineCalls(calls).at(-1)?.query.get('limit')).toBe('100')

    await wrapper.get('[data-test="timeline-limit"]').setValue('200')
    await flushAll()

    expect(router.currentRoute.value.query.limit).toBeUndefined()
    expect(timelineCalls(calls).at(-1)?.query.get('limit')).toBe('200')
  })

  it('shows the empty state when the timeline has no items', async () => {
    const { wrapper } = await mountTimeline((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories/timeline') return ok({ items: [] })
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.text()).toContain('暂无记忆时间线')
    expect(wrapper.find('[data-test="timeline-group"]').exists()).toBe(false)
  })

  it('shows the backend message and retries', async () => {
    let failing = true
    const { wrapper, calls } = await mountTimeline((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories/timeline') {
        if (failing) return fail(503, 'memory.unavailable', '时间线读取失败')
        return ok({ items: [makeRow(1)] })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.text()).toContain('时间线读取失败')

    failing = false
    await wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()

    expect(timelineCalls(calls)).toHaveLength(2)
    expect(wrapper.findAll('[data-test="timeline-item"]')).toHaveLength(1)
  })
})
