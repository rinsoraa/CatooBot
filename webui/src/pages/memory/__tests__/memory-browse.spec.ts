/** 记忆浏览（W5 §27/§28）：分页 offset、total null 文案、两种空态、错误重试、防抖。 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import MemoryBrowse from '@/pages/memory/MemoryBrowse.vue'
import type { MemoryRow } from '@/types/domain'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  wait,
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
    layer: 'semantic',
    category: 'fact',
    scope_key: 'user:10001',
    status: 'active',
    importance: 0.5,
    confidence: 0.7,
    created_at: 1700000000 + id,
    updated_at: 1700000100 + id,
    provenance: {},
    ...overrides,
  }
}

interface RecordedCall {
  path: string
  query: URLSearchParams
}

function memoriesCalls(calls: MockRequest[]): RecordedCall[] {
  return calls
    .map((call) => {
      const url = new URL(call.url, 'http://localhost')
      return { path: url.pathname, query: url.searchParams }
    })
    .filter((call) => call.path === '/api/v1/memories')
}

async function makeRouter(initial: string): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/memory', name: 'memory', component: { template: '<div />' } },
      {
        path: '/memory/:memoryId(\\d+)',
        name: 'memory-detail',
        component: { template: '<div />' },
      },
    ],
  })
  await router.push(initial)
  await router.isReady()
  return router
}

async function mountBrowse(
  handler: (request: MockRequest) => MockReply,
  initial = '/memory',
): Promise<{ wrapper: VueWrapper; calls: MockRequest[]; router: Router }> {
  const calls = installFetch(handler)
  const pinia = useFreshPinia()
  const router = await makeRouter(initial)
  const wrapper = mount(MemoryBrowse, { global: { plugins: [pinia, router] } })
  await flushAll()
  return { wrapper, calls, router }
}

describe('MemoryBrowse', () => {
  it('loads the first page with the default status filter and renders cards', async () => {
    const items = [makeRow(1), makeRow(2)]
    const { wrapper, calls } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        return ok({ items, total: 2, limit: 20, offset: 0, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    const first = memoriesCalls(calls)[0]
    expect(first?.query.get('status')).toBe('active')
    expect(first?.query.get('limit')).toBe('20')
    expect(first?.query.get('offset')).toBe('0')

    expect(wrapper.findAll('[data-test="memory-card"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('记忆 1')
    expect(wrapper.get('[data-test="memory-total"]').text()).toBe('共 2 条')
  })

  it('pages with offset in the URL and disables 上一页 on the first page', async () => {
    const all = Array.from({ length: 45 }, (_, index) => makeRow(index + 1))
    const { wrapper, calls, router } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        const limit = Number(url.searchParams.get('limit') ?? '20')
        const offset = Number(url.searchParams.get('offset') ?? '0')
        return ok({
          items: all.slice(offset, offset + limit),
          total: all.length,
          limit,
          offset,
          next_cursor: null,
        })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.get('[data-test="memory-prev"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="memory-next"]').trigger('click')
    await flushAll()

    expect(router.currentRoute.value.query.offset).toBe('20')
    expect(memoriesCalls(calls).at(-1)?.query.get('offset')).toBe('20')
    expect(wrapper.get('[data-test="memory-range"]').text()).toBe('第 21–40 条')

    await wrapper.get('[data-test="memory-prev"]').trigger('click')
    await flushAll()

    expect(router.currentRoute.value.query.offset).toBeUndefined()
    expect(memoriesCalls(calls).at(-1)?.query.get('offset')).toBe('0')
    expect(wrapper.get('[data-test="memory-range"]').text()).toBe('第 1–20 条')
  })

  it('shows 还有更多 instead of a fabricated total when total is null', async () => {
    const items = Array.from({ length: 20 }, (_, index) => makeRow(index + 1))
    const { wrapper, router } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        const offset = Number(url.searchParams.get('offset') ?? '0')
        return ok({ items, total: null, limit: 20, offset, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.get('[data-test="memory-total"]').text()).toBe('还有更多')
    expect(wrapper.text()).not.toContain('共 20 条')
    expect(wrapper.get('[data-test="memory-next"]').attributes('disabled')).toBeUndefined()

    await wrapper.get('[data-test="memory-next"]').trigger('click')
    await flushAll()
    expect(router.currentRoute.value.query.offset).toBe('20')
  })

  it('shows the empty state when there are no memories at all', async () => {
    const { wrapper } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.text()).toContain('还没有长期记忆')
    expect(wrapper.find('[data-test="memory-list"]').exists()).toBe(false)
  })

  it('shows the no-match empty state when filters return nothing', async () => {
    const { wrapper } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    }, '/memory?q=%E7%8C%AB')

    expect(wrapper.text()).toContain('没有匹配的记忆')
    expect((wrapper.get('[data-test="filter-q"]').element as HTMLInputElement).value).toBe('猫')
  })

  it('shows the backend message and retries the request', async () => {
    let failing = true
    const { wrapper, calls } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        if (failing) return fail(503, 'memory.unavailable', '记忆服务暂时不可用')
        return ok({ items: [makeRow(1)], total: 1, limit: 20, offset: 0, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(wrapper.text()).toContain('记忆服务暂时不可用')

    failing = false
    await wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()

    expect(memoriesCalls(calls)).toHaveLength(2)
    expect(wrapper.findAll('[data-test="memory-card"]')).toHaveLength(1)
  })

  it('debounces keyword typing into a single request', async () => {
    const { wrapper, calls, router } = await mountBrowse((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/memories') {
        return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
      }
      return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
    })

    expect(memoriesCalls(calls)).toHaveLength(1)

    const input = wrapper.get('[data-test="filter-q"]')
    await input.setValue('猫')
    await input.setValue('猫咪')
    await wait(400)
    await flushAll()

    const withQuery = memoriesCalls(calls).filter((call) => call.query.get('q') !== null)
    expect(withQuery).toHaveLength(1)
    expect(withQuery[0]?.query.get('q')).toBe('猫咪')
    expect(router.currentRoute.value.query.q).toBe('猫咪')
  })
})
