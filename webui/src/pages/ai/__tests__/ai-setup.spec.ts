/**
 * 配置向导（W4 §67-§70、§106）：未完成步骤定位；只有 ready + 本会话测试成功
 * 才显示「✓ AI Ready」，只看 Provider 数量不算完成。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiSetup from '@/pages/ai/AiSetup.vue'
import type { AiHealth, AiStatus, ModelItem, ProviderItem, TestResult } from '@/types/ai'

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

function makeStatus(health: AiHealth, checks: AiStatus['checks'], chatModel: string): AiStatus {
  return {
    status: health,
    enabled: true,
    configured: true,
    checks,
    providers: { total: 1, with_key: checks.has_credential ? 1 : 0, missing_key: [] },
    models: { total: checks.has_model ? 1 : 0, enabled: checks.has_model ? 1 : 0, disabled: 0, usable: checks.has_model ? 1 : 0, cooldown: 0 },
    chat_model: chatModel,
    fallback_chain: chatModel ? [chatModel] : [],
    errors: { rate_limited: 0, server_errors: 0 },
    cooldown_models: [],
  }
}

const PROVIDER: ProviderItem = {
  name: 'openai',
  type: 'openai_compatible',
  base_url: 'https://api.example.com/v1',
  api_key_env: 'CATOOBOT_OPENAI_API_KEY',
  has_key: true,
  models: ['fast'],
  restart_required: false,
}

function model(name: string): ModelItem {
  return {
    name,
    provider: 'openai',
    model: `id-${name}`,
    enabled: true,
    order: 0,
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

function testResult(ok: boolean, message = ''): TestResult {
  return {
    ok,
    model: 'fast',
    requested_model: 'fast',
    provider: 'openai',
    provider_model: 'id-fast',
    latency_ms: 88,
    http_status: ok ? 200 : 502,
    http_status_source: 'upstream',
    error_type: ok ? '' : 'ServerError',
    message,
    response: ok ? 'pong' : '',
  }
}

/** provider 已存在但 Key 未配置 → 应定位到第 ② 步。 */
const STATUS_PARTIAL = makeStatus(
  'not_configured',
  { has_provider: true, has_credential: false, has_model: false, chat_bound: false },
  '',
)

const STATUS_READY = makeStatus(
  'ready',
  { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
  'fast',
)

const STATUS_DEGRADED = makeStatus(
  'degraded',
  { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
  'fast',
)

function installDefault(handler?: (url: string, method: string) => Response | null): void {
  installFetch((url, method) => {
    const custom = handler?.(url, method)
    if (custom) return custom
    if (url.includes('/ai/status')) return envelope(STATUS_READY)
    if (url.includes('/ai/providers')) return envelope({ items: [PROVIDER] })
    if (url.includes('/ai/models') && method === 'GET') return envelope({ items: [model('fast')] })
    if (url.includes('/credentials')) {
      return envelope({
        items: [
          { domain: 'ai', ref: 'CATOOBOT_OPENAI_API_KEY', masked: 'sk-***abcd', configured: true },
        ],
      })
    }
    if (url.includes('/test') && method === 'POST') return envelope(testResult(true))
    return envelope({})
  })
}

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiSetup, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  requests = []
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiSetup', () => {
  it('auto-locates the first incomplete step from status.checks', async () => {
    installDefault((url) =>
      url.includes('/ai/status') ? envelope(STATUS_PARTIAL) : null,
    )
    const wrapper = await mountPage()

    const keyStep = wrapper.get('[data-test="setup-step-key"]')
    expect(keyStep.attributes('aria-current')).toBe('step')
    expect(keyStep.classes()).toContain('setup__step--active')
    // Provider 已完成，Key/模型/测试还没完成
    expect(wrapper.get('[data-test="setup-done-provider"]').text()).toContain('已完成')
    expect(wrapper.find('[data-test="setup-ready"]').exists()).toBe(false)
  })

  it('shows ✓ AI Ready only after ready status AND a successful test in this session', async () => {
    installDefault()
    const wrapper = await mountPage()

    // 后端 ready 但本会话还没测试 → 不显示 Ready
    expect(wrapper.find('[data-test="setup-ready"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="setup-step-test"]').attributes('aria-current')).toBe('step')

    const target = wrapper.get('[data-test="setup-test-model"]').element as HTMLSelectElement
    expect(target.value).toBe('fast')

    await wrapper.get('[data-test="setup-run-test"]').trigger('click')
    await settle()

    const write = requests.find((request) => request.method === 'POST')
    expect(write?.url).toContain('/api/v1/ai/models/fast/test')
    expect(write?.body).toEqual({ prompt: 'ping' })

    const ready = wrapper.get('[data-test="setup-ready"]')
    expect(ready.text()).toContain('✓ AI Ready')
  })

  it('does not show ✓ AI Ready when the backend status is not ready, even after a test passes', async () => {
    installDefault((url) => (url.includes('/ai/status') ? envelope(STATUS_DEGRADED) : null))
    const wrapper = await mountPage()

    await wrapper.get('[data-test="setup-run-test"]').trigger('click')
    await settle()

    expect(wrapper.find('[data-test="setup-ready"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="setup-status"]').text()).toContain('降级')
  })

  it('keeps the step incomplete and shows the backend message when the test fails', async () => {
    installDefault((url, method) =>
      url.includes('/test') && method === 'POST'
        ? envelope(testResult(false, '上游 provider 返回 502'))
        : null,
    )
    const wrapper = await mountPage()

    await wrapper.get('[data-test="setup-run-test"]').trigger('click')
    await settle()

    expect(wrapper.find('[data-test="setup-ready"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="test-result-panel"]').text()).toContain('上游 provider 返回 502')
    expect(wrapper.get('[data-test="setup-step-test"]').text()).toContain('待完成')
  })
})
