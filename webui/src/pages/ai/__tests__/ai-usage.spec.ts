/** 用量页（W4 §64-§65、§112）：过滤参数出参、tokens 为 0 显示 —、空/错误态。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiUsage from '@/pages/ai/AiUsage.vue'
import type { UsageRow } from '@/types/ai'

interface RecordedRequest {
  url: string
  method: string
}

const originalFetch = globalThis.fetch
let requests: RecordedRequest[] = []
let pinia: Pinia

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ ok: true, data, meta: { request_id: 't' } }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function failure(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ ok: false, error: { code: 'internal.error', message }, meta: { request_id: 't' } }),
    { status, headers: { 'Content-Type': 'application/json' } },
  )
}

function installFetch(handler: (url: string, method: string) => Response): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    requests.push({ url, method: (init?.method ?? 'GET').toUpperCase() })
    return handler(url, (init?.method ?? 'GET').toUpperCase())
  }) as unknown as typeof fetch
}

const ROWS: UsageRow[] = [
  {
    key: 'fast',
    calls: 10,
    failures: 2,
    rate_limited: 1,
    server_errors: 1,
    tokens: 0,
    avg_latency_ms: 124.6,
    max_latency_ms: 512.2,
  },
  {
    key: 'smart',
    calls: 3,
    failures: 0,
    rate_limited: 0,
    server_errors: 0,
    tokens: 1234,
    avg_latency_ms: 0,
    max_latency_ms: 0,
  },
]

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiUsage, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  requests = []
  installFetch((url) => {
    if (url.includes('/ai/usage')) return envelope({ items: ROWS })
    return failure(404, `未 mock 的请求：${url}`)
  })
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiUsage', () => {
  it('loads with days=7 and group_by=model, then renders the columns with 0 tokens as —', async () => {
    const wrapper = await mountPage()
    expect(requests[0]?.url).toContain('/api/v1/ai/usage?days=7&group_by=model')

    const header = wrapper.get('[data-test="usage-table"] thead').text()
    for (const label of ['Key', '请求数', '成功', '失败', '429', '5xx', 'Tokens', '平均延迟', '最大延迟']) {
      expect(header).toContain(label)
    }

    const first = wrapper.findAll('[data-test="usage-row"]')[0]
    expect(first?.text()).toContain('fast')
    expect(first?.text()).toContain('8') // 成功 = 10 - 2
    expect(first?.get('[data-test="usage-tokens"]').text()).toBe('—')
    expect(first?.text()).toContain('125 ms')

    const second = wrapper.findAll('[data-test="usage-row"]')[1]
    expect(second?.get('[data-test="usage-tokens"]').text()).toBe('1234')
  })

  it('sends the selected days and group_by values on filter change', async () => {
    const wrapper = await mountPage()
    await wrapper.get('[data-test="usage-days"]').setValue('1')
    await settle()
    expect(requests.at(-1)?.url).toContain('days=1&group_by=model')

    await wrapper.get('[data-test="usage-group"]').setValue('provider')
    await settle()
    expect(requests.at(-1)?.url).toContain('days=1&group_by=provider')
  })

  it('shows the empty state when the backend returns no rows', async () => {
    installFetch((url) => {
      if (url.includes('/ai/usage')) return envelope({ items: [] })
      return failure(404, `未 mock 的请求：${url}`)
    })
    const wrapper = await mountPage()
    expect(wrapper.find('[data-test="usage-table"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('暂无用量数据')
  })

  it('shows the backend message when usage loading fails', async () => {
    installFetch((url) => {
      if (url.includes('/ai/usage')) return failure(500, '用量记录器读取失败')
      return failure(404, `未 mock 的请求：${url}`)
    })
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('用量记录器读取失败')
  })
})
