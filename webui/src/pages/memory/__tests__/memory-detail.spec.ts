/** 记忆详情（W5 §29）：字段渲染、provenance 降级、动作确认门、404。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import MemoryDetail from '@/pages/memory/MemoryDetail.vue'
import type { MemoryRow } from '@/types/domain'
import {
  fail,
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

function makeRow(overrides: Partial<MemoryRow> = {}): MemoryRow {
  return {
    memory_id: 7,
    content: '用户喜欢在深夜写代码。',
    summary: '深夜写代码',
    layer: 'semantic',
    category: 'preference',
    scope_key: 'user:10001',
    status: 'active',
    importance: 0.8,
    confidence: 0.7,
    created_at: 1700000000,
    updated_at: 1700000100,
    provenance: {},
    ...overrides,
  }
}

function expectedTime(seconds: number): string {
  const date = new Date(seconds * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
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

interface MountOptions {
  memory?: unknown
  flags?: { detailFails: boolean }
  initial?: string
}

async function mountDetail(options: MountOptions = {}): Promise<{
  wrapper: VueWrapper
  calls: MockRequest[]
  router: Router
}> {
  const row = options.memory ?? { ...makeRow(), source: 'sandbox', final: 0.42 }
  const flags = options.flags ?? { detailFails: false }
  const calls = installFetch((request): MockReply => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/memories/health') return ok({ enabled: true })
    if (url.pathname === '/api/v1/memories') {
      return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
    }
    if (url.pathname === `/api/v1/memories/${7}`) {
      if (flags.detailFails) return fail(404, 'memory.not_found', '记忆不存在：7')
      return ok({ memory: row, relations: [], supersedes: [] })
    }
    if (url.pathname.startsWith('/api/v1/memories/7/')) {
      return ok({ done: true, action: url.pathname.split('/').at(-1), memory_id: 7 })
    }
    return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
  })
  const pinia = useFreshPinia()
  const router = await makeRouter(options.initial ?? '/memory/7')
  const wrapper = mount(MemoryDetail, { global: { plugins: [pinia, router] } })
  await settle()
  return { wrapper, calls, router }
}

function postCalls(calls: MockRequest[], suffix: string): MockRequest[] {
  return calls.filter((call) => new URL(call.url, 'http://localhost').pathname === `/api/v1/memories/7/${suffix}`)
}

describe('MemoryDetail', () => {
  it('renders content, person, provenance fields, source, times and retrieval metadata', async () => {
    const { wrapper } = await mountDetail({
      memory: {
        ...makeRow({ provenance: { episode_key: 'ep-1', experience_id: 'exp-2', source_table: 'experiences' } }),
        source: 'sandbox',
        final: 0.42,
        origin: 'both',
      },
    })

    expect(wrapper.get('[data-test="detail-content"]').text()).toBe('用户喜欢在深夜写代码。')
    expect(wrapper.get('[data-test="detail-person"]').text()).toBe('10001')
    expect(wrapper.get('[data-test="detail-id"]').text()).toBe('7')
    expect(wrapper.get('[data-test="detail-source"]').text()).toBe('sandbox')
    expect(wrapper.get('[data-test="detail-created"]').text()).toBe(expectedTime(1700000000))
    expect(wrapper.get('[data-test="detail-updated"]').text()).toBe(expectedTime(1700000100))

    const provenance = wrapper.get('[data-test="detail-provenance"]').text()
    expect(provenance).toContain('episode_key')
    expect(provenance).toContain('ep-1')
    expect(provenance).toContain('experience_id')
    expect(provenance).toContain('exp-2')
    expect(provenance).toContain('source_table')
    expect(provenance).toContain('experiences')

    const retrieval = wrapper.get('[data-test="detail-retrieval"]').text()
    expect(retrieval).toContain('0.42')
    expect(retrieval).toContain('both')
  })

  it('renders — for every missing provenance field', async () => {
    const { wrapper } = await mountDetail({ memory: makeRow() })
    const rows = wrapper.get('[data-test="detail-provenance"]').findAll('dd')
    expect(rows).toHaveLength(4)
    for (const row of rows) expect(row.text()).toBe('—')
  })

  it('shows memory_id, scope_key and provenance JSON in the expert fold', async () => {
    const { wrapper } = await mountDetail({
      memory: makeRow({ provenance: { episode_key: 'ep-1' } }),
    })

    const raw = wrapper.get('[data-test="detail-expert-json"]').text()
    expect(raw).toContain('"memory_id": 7')
    expect(raw).toContain('"scope_key": "user:10001"')
    expect(raw).toContain('"episode_key": "ep-1"')
  })

  it('archives only after confirmation and disables the button when already archived', async () => {
    const { wrapper, calls } = await mountDetail()

    await wrapper.get('[data-test="detail-archive"]').trigger('click')
    expect(wrapper.get('.cb-dialog__message').text()).toContain('这会修改长期记忆，不是聊天记录')
    await wrapper.get('[data-test="cancel"]').trigger('click')
    expect(postCalls(calls, 'archive')).toHaveLength(0)

    await wrapper.get('[data-test="detail-archive"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    expect(postCalls(calls, 'archive')).toHaveLength(1)
    expect(postCalls(calls, 'archive')[0]?.body).toEqual({})
  })

  it('disables 归档 for an already archived memory', async () => {
    const { wrapper } = await mountDetail({ memory: makeRow({ status: 'archived' }) })
    expect(wrapper.get('[data-test="detail-archive"]').attributes('disabled')).toBeDefined()
  })

  it('re-embeds without a confirmation dialog', async () => {
    const { wrapper, calls } = await mountDetail()

    await wrapper.get('[data-test="detail-reembed"]').trigger('click')
    await settle()

    expect(postCalls(calls, 'reembed')).toHaveLength(1)
    expect(postCalls(calls, 'reembed')[0]?.body).toEqual({})
    expect(wrapper.find('.cb-dialog').exists()).toBe(false)
  })

  it('deletes only after the danger confirmation and sends confirm=delete', async () => {
    const { wrapper, calls, router } = await mountDetail()

    await wrapper.get('[data-test="detail-delete"]').trigger('click')
    expect(wrapper.get('.cb-dialog__message').text()).toBe(
      '这会修改长期记忆，不是聊天记录。删除后无法恢复',
    )
    await wrapper.get('[data-test="cancel"]').trigger('click')
    expect(postCalls(calls, 'delete')).toHaveLength(0)

    await wrapper.get('[data-test="detail-delete"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    expect(postCalls(calls, 'delete')).toHaveLength(1)
    expect(postCalls(calls, 'delete')[0]?.body).toEqual({ confirm: 'delete' })
    expect(router.currentRoute.value.path).toBe('/memory')
  })

  it('edits the content through the textarea and rejects an empty body', async () => {
    const { wrapper, calls } = await mountDetail()

    await wrapper.get('[data-test="detail-edit"]').trigger('click')
    const textarea = wrapper.get('[data-test="detail-edit-input"]')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('用户喜欢在深夜写代码。')

    await textarea.setValue('用户喜欢在凌晨写代码。')
    await wrapper.get('[data-test="detail-edit-save"]').trigger('click')
    await settle()

    expect(postCalls(calls, 'edit')).toHaveLength(1)
    expect(postCalls(calls, 'edit')[0]?.body).toEqual({ content: '用户喜欢在凌晨写代码。' })

    await wrapper.get('[data-test="detail-edit"]').trigger('click')
    await wrapper.get('[data-test="detail-edit-input"]').setValue('   ')
    await wrapper.get('[data-test="detail-edit-save"]').trigger('click')
    await settle()
    expect(postCalls(calls, 'edit')).toHaveLength(1)
  })

  it('shows the 404 message and recovers on retry', async () => {
    const flags = { detailFails: true }
    const { wrapper } = await mountDetail({ flags })
    expect(wrapper.text()).toContain('记忆不存在：7')
    expect(wrapper.find('[data-test="detail-content"]').exists()).toBe(false)

    flags.detailFails = false
    await wrapper.get('[data-test="retry"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-test="detail-content"]').text()).toBe('用户喜欢在深夜写代码。')
  })
})
