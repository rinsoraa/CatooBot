/**
 * AI 概览（§6/§7/§116）：四种健康状态、四类缺失提示、全部就绪、指标与冷却倒计时。
 * 真实 pinia store + fetch mock，返回 W2 信封。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import AiOverview from '@/pages/ai/AiOverview.vue'
import type { AiStatus } from '@/types/ai'

interface Call {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

interface Reply {
  status?: number
  data?: unknown
  error?: { code: string; message: string; field?: string; detail?: unknown }
}

type Handler = (call: Call) => Reply

const originalFetch = globalThis.fetch

function installFetch(handler: Handler): Call[] {
  const calls: Call[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = new URL(String(input), 'http://localhost')
    const call: Call = {
      method: (init?.method ?? 'GET').toUpperCase(),
      path: url.pathname,
      query: url.searchParams,
      body: init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined,
    }
    calls.push(call)
    const reply = handler(call)
    const status = reply.status ?? 200
    const envelope =
      status >= 400
        ? {
            ok: false,
            error: reply.error ?? { code: 'internal.error', message: '请求失败' },
            meta: { request_id: 't' },
          }
        : { ok: true, data: reply.data ?? {}, meta: { request_id: 't' } }
    return {
      ok: status < 400,
      status,
      text: async () => JSON.stringify(envelope),
    } as unknown as Response
  }) as typeof fetch
  return calls
}

afterEach(() => {
  globalThis.fetch = originalFetch
})

function makeStatus(overrides: Partial<AiStatus> = {}, checks: Partial<AiStatus['checks']> = {}): AiStatus {
  return {
    status: 'ready',
    enabled: true,
    configured: true,
    checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true, ...checks },
    providers: { total: 1, with_key: 1, missing_key: [] },
    models: { total: 2, enabled: 2, disabled: 0, usable: 2, cooldown: 0 },
    chat_model: 'fast',
    fallback_chain: ['fast', 'smart'],
    errors: { rate_limited: 0, server_errors: 0 },
    cooldown_models: [],
    ...overrides,
  }
}

async function makeRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/ai', component: { template: '<div />' } },
      { path: '/ai/providers', component: { template: '<div />' } },
      { path: '/ai/models', component: { template: '<div />' } },
      { path: '/ai/roles', component: { template: '<div />' } },
      { path: '/ai/setup', component: { template: '<div />' } },
    ],
  })
  await router.push('/ai')
  await router.isReady()
  return router
}

async function settle(): Promise<void> {
  await flushPromises()
  await flushPromises()
  await flushPromises()
}

async function mountOverview(status: AiStatus | null): Promise<VueWrapper> {
  installFetch((call) => {
    if (call.path === '/api/v1/ai/status') {
      if (!status) return { status: 500, error: { code: 'internal.error', message: '服务暂不可用' } }
      return { data: status }
    }
    if (call.path === '/api/v1/ai/providers') return { data: { items: [] } }
    if (call.path === '/api/v1/ai/models') return { data: { items: [] } }
    if (call.path === '/api/v1/ai/roles') return { data: { items: [] } }
    return { data: {} }
  })
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = await makeRouter()
  const wrapper = mount(AiOverview, { global: { plugins: [pinia, router] } })
  await settle()
  return wrapper
}

describe('AiOverview', () => {
  it('shows the ready badge with Chinese text for ready', async () => {
    const wrapper = await mountOverview(makeStatus({ status: 'ready' }))
    expect(wrapper.get('[data-test="ai-status-badge"]').text()).toContain('就绪')
    expect(wrapper.get('[data-test="next-ready"]').text()).toBe('✓ AI Ready')
    wrapper.unmount()
  })

  it('shows 降级 for degraded', async () => {
    const wrapper = await mountOverview(makeStatus({ status: 'degraded' }))
    expect(wrapper.get('[data-test="ai-status-badge"]').text()).toContain('降级')
    wrapper.unmount()
  })

  it('shows 不可用 for unavailable', async () => {
    const wrapper = await mountOverview(makeStatus({ status: 'unavailable' }))
    expect(wrapper.get('[data-test="ai-status-badge"]').text()).toContain('不可用')
    wrapper.unmount()
  })

  it('shows 尚未配置 for not_configured', async () => {
    const wrapper = await mountOverview(makeStatus({ status: 'not_configured' }))
    expect(wrapper.get('[data-test="ai-status-badge"]').text()).toContain('尚未配置')
    wrapper.unmount()
  })

  it('renders every metric from the status payload', async () => {
    const wrapper = await mountOverview(
      makeStatus({
        providers: { total: 3, with_key: 2, missing_key: ['beta'] },
        models: { total: 4, enabled: 3, disabled: 1, usable: 2, cooldown: 1 },
        chat_model: 'fast',
        fallback_chain: ['fast', 'smart'],
        errors: { rate_limited: 17, server_errors: 23 },
      }),
    )
    const metrics = wrapper.get('[data-test="ai-metrics"]').text()
    expect(metrics).toContain('2/3')
    expect(metrics).toContain('3/4')
    expect(metrics).toContain('缺少 Key：beta')
    expect(metrics).toContain('fast')
    expect(metrics).toContain('fast → smart')
    expect(metrics).toContain('17')
    expect(metrics).toContain('23')
    wrapper.unmount()
  })

  it('shows — for every metric when the status is unavailable', async () => {
    const wrapper = await mountOverview(null)
    const metrics = wrapper.get('[data-test="ai-metrics"]').text()
    expect(metrics.match(/—/g)?.length ?? 0).toBeGreaterThanOrEqual(7)
    expect(wrapper.get('[data-test="ai-status-badge"]').text()).toContain('状态未知')
    wrapper.unmount()
  })

  it('guides to the provider form when has_provider is false', async () => {
    const wrapper = await mountOverview(makeStatus({}, { has_provider: false }))
    const step = wrapper.get('[data-test="next-provider"]')
    expect(step.text()).toContain('尚未配置 AI Provider')
    expect(wrapper.get('[data-test="next-provider-action"]').attributes('href')).toBe('/ai/providers?create=1')
    expect(wrapper.find('[data-test="next-ready"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('guides to credentials when has_credential is false', async () => {
    const wrapper = await mountOverview(makeStatus({}, { has_credential: false }))
    expect(wrapper.get('[data-test="next-credential"]').text()).toContain('Provider 缺少 API Key')
    expect(wrapper.get('[data-test="next-credential-action"]').attributes('href')).toBe('/ai/providers')
    wrapper.unmount()
  })

  it('guides to the model form when has_model is false', async () => {
    const wrapper = await mountOverview(makeStatus({}, { has_model: false }))
    expect(wrapper.get('[data-test="next-model"]').text()).toContain('尚未配置模型')
    expect(wrapper.get('[data-test="next-model-action"]').attributes('href')).toBe('/ai/models?create=1')
    wrapper.unmount()
  })

  it('guides to roles when chat_bound is false', async () => {
    const wrapper = await mountOverview(makeStatus({}, { chat_bound: false }))
    expect(wrapper.get('[data-test="next-chat"]').text()).toContain('尚未设置默认聊天模型')
    expect(wrapper.get('[data-test="next-chat-action"]').attributes('href')).toBe('/ai/roles')
    wrapper.unmount()
  })

  it('shows ✓ AI Ready and no missing steps when all checks pass', async () => {
    const wrapper = await mountOverview(makeStatus())
    expect(wrapper.get('[data-test="next-ready"]').text()).toBe('✓ AI Ready')
    expect(wrapper.find('[data-test="next-provider"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="next-model"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('always offers the setup wizard and counts cooldown seconds down', async () => {
    const seededRemaining = 90
    const wrapper = await mountOverview(
      makeStatus({ models: { total: 2, enabled: 2, disabled: 0, usable: 1, cooldown: 1 }, cooldown_models: [{ name: 'slow', cooldown_until: 0, remaining_seconds: seededRemaining }] }),
    )
    expect(wrapper.get('[data-test="run-setup"]').attributes('href')).toBe('/ai/setup')
    const cooldowns = wrapper.get('[data-test="ai-cooldowns"]').text()
    expect(cooldowns).toContain('slow')
    const match = cooldowns.match(/约 (\d+) 秒后恢复/)
    expect(match).not.toBeNull()
    const remaining = Number(match?.[1] ?? '0')
    expect(remaining).toBeGreaterThan(0)
    expect(remaining).toBeLessThanOrEqual(90)
    wrapper.unmount()
  })
})
