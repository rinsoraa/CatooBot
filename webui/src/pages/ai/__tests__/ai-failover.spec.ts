/** 故障转移页（W4 §31-§34、§104、§35/§71/§105）：排序保存、429 tooltip、重置二次确认。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiFailover from '@/pages/ai/AiFailover.vue'
import type { ToastItem } from '@/composables/toast'
import { useToast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import type { AiStatus, ModelItem } from '@/types/ai'

interface RecordedRequest {
  url: string
  method: string
  body: unknown
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
    JSON.stringify({ ok: false, error: { code: 'ai.invalid_order', message }, meta: { request_id: 't' } }),
    { status, headers: { 'Content-Type': 'application/json' } },
  )
}

function installFetch(handler: (url: string, method: string, body: unknown) => Response): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = null
    if (typeof init?.body === 'string' && init.body.length > 0) {
      try {
        body = JSON.parse(init.body) as unknown
      } catch {
        body = null
      }
    }
    requests.push({ url, method, body })
    return handler(url, method, body)
  }) as unknown as typeof fetch
}

function model(name: string, order: number): ModelItem {
  return {
    name,
    provider: `provider-${name}`,
    model: `id-${name}`,
    enabled: true,
    order,
    roles: [],
    live: true,
    in_cooldown: false,
    cooldown_until: 0,
    cooldown_remaining_seconds: 0,
    failure_count: 0,
    last_error: null,
    usage: {
      calls: 0,
      failures: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      avg_latency_ms: 0,
      max_latency_ms: 0,
    },
  }
}

const MODELS = [model('fast', 0), model('smart', 1), model('vision', 2)]

const STATUS: AiStatus = {
  status: 'ready',
  enabled: true,
  configured: true,
  checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
  providers: { total: 1, with_key: 1, missing_key: [] },
  models: { total: 3, enabled: 3, disabled: 0, usable: 3, cooldown: 0 },
  chat_model: 'fast',
  fallback_chain: ['fast', 'smart', 'vision'],
  errors: { rate_limited: 0, server_errors: 0 },
  cooldown_models: [],
}

function defaultHandler(putFails = false) {
  return (url: string, method: string, body: unknown): Response => {
    if (url.includes('/ai/models') && method === 'GET') return envelope({ items: MODELS })
    if (url.includes('/ai/status')) return envelope(STATUS)
    if (url.endsWith('/ai/models/order') && method === 'PUT') {
      if (putFails) return failure(400, 'order 必须是当前全部模型别名的一个排列')
      return envelope({ order: (body as { order: string[] }).order })
    }
    if (url.includes('/ai/router/reset') && method === 'POST') return envelope({ reset: true })
    return failure(404, `未 mock 的请求：${method} ${url}`)
  }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiFailover, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

function lastToast(): ToastItem | undefined {
  return useToast().items.value.at(-1)
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
  requests = []
  installFetch(defaultHandler())
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiFailover', () => {
  it('explains the router boundary and renders 429/5xx tooltips', async () => {
    const wrapper = await mountPage()
    expect(wrapper.get('[data-test="failover-explain"]').text()).toContain(
      '故障转移由 Model Router 执行；这里只配置顺序与启停',
    )

    const tip429 = wrapper.get('[data-test="tip-429"]')
    expect(tip429.attributes('tabindex')).toBe('0')
    expect(tip429.text()).toContain('请求过多')
    expect(tip429.text()).toContain('cooldown')
    expect(tip429.text()).toContain('Model Router 决定')

    const tip5xx = wrapper.get('[data-test="tip-5xx"]')
    expect(tip5xx.text()).toContain('服务端错误')
    expect(tip5xx.text()).toContain('Model Router 决定')
  })

  it('saves the locally reordered chain through aiStore.reorder', async () => {
    const wrapper = await mountPage()
    const rows = wrapper.findAll('[data-test="fb-row"]')
    expect(rows.map((row) => row.get('[data-test="fb-name"]').text())).toEqual([
      'fast',
      'smart',
      'vision',
    ])

    await rows[0]?.get('[data-test="fb-down"]').trigger('click')
    expect(wrapper.find('[data-test="fb-dirty"]').exists()).toBe(true)
    await wrapper.get('[data-test="fb-save"]').trigger('click')
    await settle()

    const write = requests.find((request) => request.method === 'PUT')
    expect(write?.url).toContain('/api/v1/ai/models/order')
    expect(write?.body).toEqual({ order: ['smart', 'fast', 'vision'] })
    expect(lastToast()?.message).toBe('顺序已保存')
  })

  it('shows the backend message when saving the order fails', async () => {
    installFetch(defaultHandler(true))
    const wrapper = await mountPage()
    await wrapper.get('[data-test="fb-row"] [data-test="fb-down"]').trigger('click')
    await wrapper.get('[data-test="fb-save"]').trigger('click')
    await settle()

    expect(lastToast()?.kind).toBe('error')
    expect(wrapper.text()).toContain('order 必须是当前全部模型别名的一个排列')
    expect(useAiStore().error).toContain('order 必须是当前全部模型别名的一个排列')
  })

  it('requires a second confirmation before resetting the router memory', async () => {
    const wrapper = await mountPage()
    await wrapper.get('[data-test="failover-reset"]').trigger('click')
    await settle()

    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain(
      '清除当前运行时 cooldown / 临时状态；不会删除模型配置',
    )
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const reset = requests.find((request) => request.method === 'POST')
    expect(reset?.url).toContain('/api/v1/ai/router/reset')
    expect(reset?.body).toEqual({ confirm: 'reset' })
    expect(lastToast()?.message).toBe('Router 内存状态已重置')
  })
})
