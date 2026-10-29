/** 测试台（W4 §36-§37）：发送调用与结果渲染；诊断请求不写记忆/对话/关系。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiTest from '@/pages/ai/AiTest.vue'
import type { ModelItem, ProviderItem, TestResult } from '@/types/ai'

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

function model(name: string): ModelItem {
  return {
    name,
    provider: 'prov',
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

const PROVIDERS: ProviderItem[] = [
  {
    name: 'prov',
    type: 'openai_compatible',
    base_url: 'https://api.example.com/v1',
    api_key_env: 'PROV_KEY',
    has_key: true,
    models: ['fast', 'smart'],
    restart_required: false,
  },
]

function testResult(overrides: Partial<TestResult> = {}): TestResult {
  return {
    ok: true,
    model: 'fast',
    requested_model: 'fast',
    provider: 'prov',
    provider_model: 'id-fast',
    latency_ms: 123.4,
    http_status: 200,
    http_status_source: 'upstream',
    error_type: '',
    message: '',
    response: '当然可以，测试成功！',
    ...overrides,
  }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiTest, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  requests = []
  installFetch((url, method) => {
    if (method === 'POST' && url.includes('/test')) return envelope(testResult())
    if (url.includes('/ai/providers')) return envelope({ items: PROVIDERS })
    if (url.includes('/ai/models') && method === 'GET') {
      return envelope({ items: [model('fast'), model('smart')] })
    }
    return envelope(testResult())
  })
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiTest', () => {
  it('states the diagnostic safety rule and seeds the default prompt/provider/model', async () => {
    const wrapper = await mountPage()
    expect(wrapper.get('[data-test="test-notice"]').text()).toContain('不会写入记忆、对话或关系')

    const prompt = wrapper.get('[data-test="test-prompt"]').element as HTMLTextAreaElement
    expect(prompt.value).toBe('你好，请简单回复一句“测试成功”。')

    const provider = wrapper.get('[data-test="test-provider"]').element as HTMLSelectElement
    const selectedModel = wrapper.get('[data-test="test-model"]').element as HTMLSelectElement
    expect(provider.value).toBe('prov')
    expect(selectedModel.value).toBe('fast')
  })

  it('posts the chosen model and prompt, then renders the result panel', async () => {
    const wrapper = await mountPage()
    await wrapper.get('[data-test="test-prompt"]').setValue('自定义 prompt')
    await wrapper.get('[data-test="test-form"]').trigger('submit')
    await settle()

    const write = requests.find((request) => request.method === 'POST')
    expect(write?.url).toContain('/api/v1/ai/models/fast/test')
    expect(write?.body).toEqual({ prompt: '自定义 prompt' })
    expect(wrapper.get('[data-test="test-result-panel"]').text()).toContain('当然可以，测试成功！')
    expect(wrapper.get('[data-test="test-result-panel"]').text()).toContain('成功')
  })

  it('renders the backend failure message verbatim', async () => {
    installFetch((url, method) => {
      if (method === 'POST' && url.includes('/test')) {
        return envelope(
          testResult({
            ok: false,
            http_status: 502,
            error_type: 'ServerError',
            message: '上游 provider 返回 502',
            response: '',
          }),
        )
      }
      if (url.includes('/ai/providers')) return envelope({ items: PROVIDERS })
      if (url.includes('/ai/models') && method === 'GET') return envelope({ items: [model('fast')] })
      return envelope(testResult())
    })
    const wrapper = await mountPage()
    await wrapper.get('[data-test="test-form"]').trigger('submit')
    await settle()

    const panel = wrapper.get('[data-test="test-result-panel"]').text()
    expect(panel).toContain('失败')
    expect(panel).toContain('上游 provider 返回 502')
    expect(panel).toContain('ServerError')
  })

  it('switching provider resets the model options to that provider', async () => {
    installFetch((url, method) => {
      if (method === 'POST' && url.includes('/test')) return envelope(testResult())
      if (url.includes('/ai/providers')) {
        return envelope({
          items: [
            ...PROVIDERS,
            {
              ...PROVIDERS[0],
              name: 'other',
              models: ['other-model'],
            } satisfies ProviderItem,
          ],
        })
      }
      if (url.includes('/ai/models') && method === 'GET') return envelope({ items: [model('fast')] })
      return envelope(testResult())
    })
    const wrapper = await mountPage()
    await wrapper.get('[data-test="test-provider"]').setValue('other')
    await settle()

    const selectedModel = wrapper.get('[data-test="test-model"]').element as HTMLSelectElement
    expect(selectedModel.value).toBe('other-model')
  })
})
