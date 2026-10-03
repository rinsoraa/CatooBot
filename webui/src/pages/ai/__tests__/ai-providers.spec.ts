/**
 * AiProviders（§8-§17 / §61）：卡片列表、创建（Provider + 凭据两次写入）、
 * 深链 ?create=1、测试连接、409 删除冲突与强制删除。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import AiProviders from '@/pages/ai/AiProviders.vue'
import type { AiStatus, ModelItem, ProviderItem, TestResult } from '@/types/ai'

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
        ? { ok: false, error: reply.error ?? { code: 'internal.error', message: '请求失败' }, meta: { request_id: 't' } }
        : { ok: true, data: reply.data ?? {}, meta: { request_id: 't' } }
    return { ok: status < 400, status, text: async () => JSON.stringify(envelope) } as unknown as Response
  }) as typeof fetch
  return calls
}

afterEach(() => {
  globalThis.fetch = originalFetch
})

function makeModel(overrides: Partial<ModelItem> = {}): ModelItem {
  return {
    name: 'fast',
    provider: 'alpha',
    model: 'deepseek-chat',
    enabled: true,
    order: 0,
    roles: ['chat'],
    live: true,
    in_cooldown: false,
    cooldown_until: 0,
    cooldown_remaining_seconds: 0,
    failure_count: 0,
    last_error: null,
    usage: {
      calls: 5,
      failures: 1,
      prompt_tokens: 10,
      completion_tokens: 20,
      avg_latency_ms: 120,
      max_latency_ms: 300,
    },
    ...overrides,
  }
}

function makeProviders(): ProviderItem[] {
  return [
    {
      name: 'alpha',
      type: 'openai_compatible',
      base_url: 'https://api.alpha.com/v1',
      api_key_env: 'CATOOBOT_ALPHA_API_KEY',
      has_key: true,
      models: ['fast'],
      restart_required: false,
    },
    {
      name: 'beta',
      type: 'openai_compatible',
      base_url: 'https://api.beta.com/v1',
      api_key_env: 'CATOOBOT_BETA_API_KEY',
      has_key: false,
      models: [],
      restart_required: false,
    },
  ]
}

function makeStatus(providers: ProviderItem[]): AiStatus {
  return {
    status: 'ready',
    enabled: true,
    configured: true,
    checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
    providers: { total: providers.length, with_key: providers.filter((p) => p.has_key).length, missing_key: [] },
    models: { total: 1, enabled: 1, disabled: 0, usable: 1, cooldown: 0 },
    chat_model: 'fast',
    fallback_chain: ['fast'],
    errors: { rate_limited: 0, server_errors: 0 },
    cooldown_models: [],
  }
}

const TEST_RESULT: TestResult = {
  ok: true,
  model: 'fast',
  requested_model: 'fast',
  provider: 'alpha',
  latency_ms: 123,
  http_status: 200,
  http_status_source: 'upstream',
  error_type: '',
  message: '',
  reply: 'pong',
}

const PROVIDER_IN_USE: Reply = {
  status: 409,
  error: {
    code: 'ai.provider_in_use',
    message: 'Provider「alpha」仍被 1 个模型使用：fast；请先删除这些模型，或带 force=1 连带删除',
    detail: { models: ['fast'] },
  },
}

interface MockState {
  providers: ProviderItem[]
  models: ModelItem[]
  credentials: { ref: string; value: string }[]
  failDelete: Reply | null
}

function makeState(): MockState {
  return { providers: makeProviders(), models: [makeModel({ last_error: '连接超时' })], credentials: [], failDelete: null }
}

function makeHandler(state: MockState): Handler {
  return (call) => {
    const { method, path } = call
    if (path === '/api/v1/ai/status') return { data: makeStatus(state.providers) }
    if (path === '/api/v1/ai/providers' && method === 'GET') return { data: { items: state.providers } }
    if (path.startsWith('/api/v1/ai/providers/')) {
      const name = decodeURIComponent(path.slice('/api/v1/ai/providers/'.length))
      if (method === 'PUT') {
        const body = call.body as { type?: string; base_url?: string; api_key_env?: string }
        const existing = state.providers.find((item) => item.name === name)
        if (existing) {
          existing.type = body.type ?? existing.type
          existing.base_url = body.base_url ?? existing.base_url
          existing.api_key_env = body.api_key_env ?? existing.api_key_env
        } else {
          state.providers.push({
            name,
            type: body.type ?? 'openai_compatible',
            base_url: body.base_url ?? '',
            api_key_env: body.api_key_env ?? '',
            has_key: false,
            models: [],
            restart_required: false,
          })
        }
        return { data: state.providers.find((item) => item.name === name) }
      }
      if (method === 'DELETE') {
        if (state.failDelete) return state.failDelete
        state.providers = state.providers.filter((item) => item.name !== name)
        state.models = state.models.filter((item) => item.provider !== name)
        return { data: { deleted: true, models_removed: 1 } }
      }
    }
    if (path === '/api/v1/ai/models' && method === 'GET') return { data: { items: state.models } }
    if (path === '/api/v1/ai/roles') return { data: { items: [] } }
    if (path === '/api/v1/credentials' && method === 'GET') return { data: { items: [] } }
    if (path.startsWith('/api/v1/credentials/ai/') && method === 'PUT') {
      const ref = decodeURIComponent(path.slice('/api/v1/credentials/ai/'.length))
      const body = call.body as { value?: string }
      state.credentials.push({ ref, value: body.value ?? '' })
      state.providers = state.providers.map((item) =>
        item.api_key_env === ref ? { ...item, has_key: true } : item,
      )
      return { data: { saved: true, ref, masked: 'sk-***', restart_required: false } }
    }
    if (path === '/api/v1/credentials/test') return { data: TEST_RESULT }
    return { data: {} }
  }
}

async function makeRouter(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/ai', component: { template: '<div />' } },
      { path: '/ai/providers', component: { template: '<div />' } },
      { path: '/ai/models', component: { template: '<div />' } },
      { path: '/ai/roles', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

async function settle(): Promise<void> {
  await flushPromises()
  await flushPromises()
  await flushPromises()
}

async function mountProviders(state: MockState, path = '/ai/providers'): Promise<{ wrapper: VueWrapper; calls: Call[] }> {
  const calls = installFetch(makeHandler(state))
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = await makeRouter(path)
  const wrapper = mount(AiProviders, { global: { plugins: [pinia, router] } })
  await settle()
  return { wrapper, calls }
}

describe('AiProviders', () => {
  it('renders provider cards with key status, model count and last error', async () => {
    const state = makeState()
    const { wrapper } = await mountProviders(state)
    const alpha = wrapper.get('[data-test="provider-alpha"]')
    expect(alpha.text()).toContain('alpha')
    expect(alpha.text()).toContain('openai_compatible')
    expect(alpha.text()).toContain('https://api.alpha.com/v1')
    expect(alpha.text()).toContain('已配置（CATOOBOT_ALPHA_API_KEY）')
    expect(alpha.text()).toContain('1 个')
    expect(alpha.text()).toContain('连接超时')

    const beta = wrapper.get('[data-test="provider-beta"]')
    expect(beta.text()).toContain('缺少 API Key')
    expect(beta.text()).toContain('未配置（CATOOBOT_BETA_API_KEY）')
    expect(beta.text()).toContain('0 个')
    wrapper.unmount()
  })

  it('creates a provider and saves the API key with two writes', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountProviders(state)
    await wrapper.get('[data-test="add-provider"]').trigger('click')

    await wrapper.get('[data-test="provider-name-input"]').setValue('gamma')
    await wrapper.get('[data-test="provider-base-url-input"]').setValue('https://api.gamma.com/v1')
    await flushPromises()
    // 环境变量名由名称派生并展示给用户
    expect((wrapper.get('[data-test="provider-env-input"]').element as HTMLInputElement).value).toBe(
      'CATOOBOT_GAMMA_API_KEY',
    )
    await wrapper.get('[data-test="provider-form"] [data-test="secret-input"]').setValue('sk-gamma-secret')
    await wrapper.get('[data-test="provider-form"] form').trigger('submit')
    await settle()

    const providerPut = calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/ai/providers/gamma')
    expect(providerPut).toBeDefined()
    expect(providerPut?.body).toEqual({
      type: 'openai_compatible',
      base_url: 'https://api.gamma.com/v1',
      api_key_env: 'CATOOBOT_GAMMA_API_KEY',
    })

    const credentialPut = calls.find(
      (c) => c.method === 'PUT' && c.path === '/api/v1/credentials/ai/CATOOBOT_GAMMA_API_KEY',
    )
    expect(credentialPut).toBeDefined()
    expect(credentialPut?.body).toEqual({ value: 'sk-gamma-secret' })
    expect(wrapper.find('[data-test="provider-form"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('creates a provider without a key using a single write', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountProviders(state)
    await wrapper.get('[data-test="add-provider"]').trigger('click')
    await wrapper.get('[data-test="provider-name-input"]').setValue('gamma')
    await wrapper.get('[data-test="provider-base-url-input"]').setValue('https://api.gamma.com/v1')
    await wrapper.get('[data-test="provider-form"] form').trigger('submit')
    await settle()

    expect(calls.filter((c) => c.method === 'PUT' && c.path.startsWith('/api/v1/ai/providers/'))).toHaveLength(1)
    expect(calls.filter((c) => c.method === 'PUT' && c.path.startsWith('/api/v1/credentials/'))).toHaveLength(0)
    wrapper.unmount()
  })

  it('opens the create form from the ?create=1 deep link', async () => {
    const state = makeState()
    const { wrapper } = await mountProviders(state, '/ai/providers?create=1')
    const form = wrapper.get('[data-test="provider-form"]')
    expect(form.text()).toContain('添加 Provider')
    wrapper.unmount()
  })

  it('tests a connection through the credentials endpoint and shows the result', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountProviders(state)
    await wrapper.get('[data-test="provider-alpha"] [data-test="test-provider"]').trigger('click')
    await settle()

    const testCall = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/credentials/test')
    expect(testCall?.body).toEqual({ provider: 'alpha', model: 'fast' })
    const panel = wrapper.get('[data-test="test-result-panel"]')
    expect(panel.text()).toContain('成功')
    expect(panel.text()).toContain('123 ms')
    expect(panel.get('[data-test="test-reply"]').text()).toBe('pong')
    wrapper.unmount()
  })

  it('gates deletion behind a confirm dialog and surfaces the 409 provider_in_use conflict', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountProviders(state)
    state.failDelete = PROVIDER_IN_USE

    await wrapper.get('[data-test="provider-alpha"] [data-test="delete-provider"]').trigger('click')
    await settle()

    // §37/§38: 确认框先出现，确认前一个请求都不发
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('删除后无法恢复')
    expect(wrapper.text()).toContain('1 个模型（fast）')

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const conflict = wrapper.get('[data-test="provider-conflict"]')
    expect(conflict.text()).toContain('仍被 1 个模型使用：fast')
    expect(conflict.text()).toContain('仍有 1 个模型')
    expect(conflict.get('[data-test="conflict-message"]').text()).toBe(PROVIDER_IN_USE.error?.message)

    let deletes = calls.filter((c) => c.method === 'DELETE' && c.path === '/api/v1/ai/providers/alpha')
    expect(deletes).toHaveLength(1)
    expect(deletes[0]?.query.get('force')).toBeNull()
    expect(deletes[0]?.body).toEqual({ confirm: 'alpha' })

    // 强制删除同样要过确认框（confirm 必须是 force）
    state.failDelete = null
    await conflict.get('[data-test="conflict-force"]').trigger('click')
    await settle()
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    deletes = calls.filter((c) => c.method === 'DELETE' && c.path === '/api/v1/ai/providers/alpha')
    expect(deletes).toHaveLength(2)
    expect(deletes[1]?.query.get('force')).toBe('1')
    expect(deletes[1]?.body).toEqual({ confirm: 'force' })
    expect(wrapper.find('[data-test="provider-conflict"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
