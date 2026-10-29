/**
 * AiModels（§18-§26）：列表（表格/卡片）、启用开关写服务端、测试模型弹窗、
 * role 引用冲突（ai.model_in_use）与强制删除、创建表单只列有 Key 的 Provider。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import AiModels from '@/pages/ai/AiModels.vue'
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
const DEFAULT_PROMPT = '你好，请简单回复一句“测试成功”。'

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

function makeProviders(withKey: boolean[] = [true, false]): ProviderItem[] {
  return [
    {
      name: 'alpha',
      type: 'openai_compatible',
      base_url: 'https://api.alpha.com/v1',
      api_key_env: 'CATOOBOT_ALPHA_API_KEY',
      has_key: withKey[0] ?? true,
      models: ['fast'],
      restart_required: false,
    },
    {
      name: 'beta',
      type: 'openai_compatible',
      base_url: 'https://api.beta.com/v1',
      api_key_env: 'CATOOBOT_BETA_API_KEY',
      has_key: withKey[1] ?? false,
      models: [],
      restart_required: false,
    },
  ]
}

function makeStatus(models: ModelItem[], providers: ProviderItem[]): AiStatus {
  return {
    status: 'ready',
    enabled: true,
    configured: true,
    checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
    providers: { total: providers.length, with_key: providers.filter((p) => p.has_key).length, missing_key: [] },
    models: { total: models.length, enabled: models.filter((m) => m.enabled).length, disabled: 0, usable: 1, cooldown: 0 },
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
  provider_model: 'deepseek-chat',
  latency_ms: 88,
  http_status: 200,
  http_status_source: 'upstream',
  error_type: '',
  message: '',
  response: '测试成功。',
}

const MODEL_IN_USE: Reply = {
  status: 409,
  error: {
    code: 'ai.model_in_use',
    message: '模型「fast」仍被角色使用：chat；请先改绑角色，或带 force=1 强制删除',
    detail: { roles: ['chat'] },
  },
}

interface MockState {
  providers: ProviderItem[]
  models: ModelItem[]
  failDelete: Reply | null
}

function makeState(withKey: boolean[] = [true, false]): MockState {
  return {
    providers: makeProviders(withKey),
    models: [
      makeModel(),
      makeModel({ name: 'slow', model: 'deepseek-reasoner', enabled: false, order: 1, roles: [], last_error: '上游超时', usage: { calls: 0, failures: 0, prompt_tokens: 0, completion_tokens: 0, avg_latency_ms: 0, max_latency_ms: 0 } }),
    ],
    failDelete: null,
  }
}

function makeHandler(state: MockState): Handler {
  return (call) => {
    const { method, path } = call
    if (path === '/api/v1/ai/status') return { data: makeStatus(state.models, state.providers) }
    if (path === '/api/v1/ai/providers' && method === 'GET') return { data: { items: state.providers } }
    if (path === '/api/v1/ai/models' && method === 'GET') return { data: { items: state.models } }
    if (path === '/api/v1/ai/roles' && method === 'GET') return { data: { items: [] } }
    if (path.startsWith('/api/v1/ai/models/')) {
      const rest = path.slice('/api/v1/ai/models/'.length)
      if (method === 'PUT' && !rest.includes('/')) {
        const name = decodeURIComponent(rest)
        const body = call.body as { provider?: string; model?: string; enabled?: boolean }
        state.models = state.models.map((item) =>
          item.name === name
            ? {
                ...item,
                provider: body.provider ?? item.provider,
                model: body.model ?? item.model,
                enabled: body.enabled ?? item.enabled,
              }
            : item,
        )
        return { data: state.models.find((item) => item.name === name) }
      }
      if (method === 'DELETE') {
        if (state.failDelete) return state.failDelete
        const name = decodeURIComponent(rest)
        state.models = state.models.filter((item) => item.name !== name)
        return { data: { deleted: true } }
      }
      if (method === 'POST' && rest.endsWith('/test')) {
        return { data: TEST_RESULT }
      }
    }
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

async function mountModels(state: MockState, path = '/ai/models'): Promise<{ wrapper: VueWrapper; calls: Call[] }> {
  const calls = installFetch(makeHandler(state))
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = await makeRouter(path)
  const wrapper = mount(AiModels, { global: { plugins: [pinia, router] } })
  await settle()
  return { wrapper, calls }
}

describe('AiModels', () => {
  it('renders the model table with roles, usage, state and last error', async () => {
    const state = makeState()
    const { wrapper } = await mountModels(state)
    const fast = wrapper.get('[data-test="model-row-fast"]')
    expect(fast.text()).toContain('fast')
    expect(fast.text()).toContain('deepseek-chat')
    expect(fast.text()).toContain('alpha')
    expect(fast.text()).toContain('聊天回复')
    expect(fast.text()).toContain('5 次调用 / 1 次失败')
    expect(fast.text()).toContain('就绪')

    const slow = wrapper.get('[data-test="model-row-slow"]')
    expect(slow.text()).toContain('已停用')
    expect(slow.text()).toContain('上游超时')
    wrapper.unmount()
  })

  it('writes the enable switch to the server without optimistic guessing', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountModels(state)
    await wrapper.get('[data-test="toggle-fast"]').trigger('click')
    await settle()

    const put = calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/ai/models/fast')
    expect(put?.body).toEqual({ enabled: false })
    expect(wrapper.get('[data-test="model-row-fast"]').text()).toContain('已停用')
    wrapper.unmount()
  })

  it('tests a model through the test dialog with the default prompt', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountModels(state)
    await wrapper.get('[data-test="model-row-fast"] [data-test="test-model"]').trigger('click')

    const prompt = wrapper.get('[data-test="test-prompt"]').element as HTMLTextAreaElement
    expect(prompt.value).toBe(DEFAULT_PROMPT)

    await wrapper.get('[data-test="test-run"]').trigger('click')
    await settle()

    const post = calls.find((c) => c.method === 'POST' && c.path === '/api/v1/ai/models/fast/test')
    expect(post?.body).toEqual({ prompt: DEFAULT_PROMPT })
    const panel = wrapper.get('[data-test="test-result-panel"]')
    expect(panel.text()).toContain('成功')
    expect(panel.get('[data-test="test-reply"]').text()).toBe('测试成功。')
    wrapper.unmount()
  })

  it('gates deletion behind a confirm dialog and blocks when a role references the model', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountModels(state)
    state.failDelete = MODEL_IN_USE

    await wrapper.get('[data-test="model-row-fast"] [data-test="delete-model"]').trigger('click')
    await settle()

    // §37/§38: 确认框先出现，确认前一个请求都不发
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(0)
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('删除后无法恢复')
    expect(wrapper.text()).toContain('1 个用途（聊天回复）')

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const conflict = wrapper.get('[data-test="model-conflict"]')
    expect(conflict.get('[data-test="conflict-message"]').text()).toBe(MODEL_IN_USE.error?.message)
    expect(conflict.text()).toContain('绑定的用途：聊天回复')

    let deletes = calls.filter((c) => c.method === 'DELETE' && c.path === '/api/v1/ai/models/fast')
    expect(deletes).toHaveLength(1)
    expect(deletes[0]?.query.get('force')).toBeNull()
    expect(deletes[0]?.body).toEqual({ confirm: 'fast' })

    // 强制删除同样要过确认框（confirm 必须是 force）
    state.failDelete = null
    await conflict.get('[data-test="conflict-force"]').trigger('click')
    await settle()
    expect(calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    deletes = calls.filter((c) => c.method === 'DELETE' && c.path === '/api/v1/ai/models/fast')
    expect(deletes).toHaveLength(2)
    expect(deletes[1]?.query.get('force')).toBe('1')
    expect(deletes[1]?.body).toEqual({ confirm: 'force' })
    wrapper.unmount()
  })

  it('creates a model from the ?create=1 deep link with only keyed providers listed', async () => {
    const state = makeState()
    const { wrapper, calls } = await mountModels(state, '/ai/models?create=1')
    const options = wrapper.findAll('[data-test="model-provider-input"] option').map((o) => (o.element as HTMLOptionElement).value)
    expect(options).toEqual(['', 'alpha'])

    await wrapper.get('[data-test="model-name-input"]').setValue('smart')
    await wrapper.get('[data-test="model-id-input"]').setValue('deepseek-reasoner')
    await wrapper.get('[data-test="model-form"] form').trigger('submit')
    await settle()

    const put = calls.find((c) => c.method === 'PUT' && c.path === '/api/v1/ai/models/smart')
    expect(put?.body).toEqual({ provider: 'alpha', model: 'deepseek-reasoner', enabled: true })
    wrapper.unmount()
  })

  it('asks for a provider first when no provider has a key', async () => {
    const state = makeState([false, false])
    const { wrapper } = await mountModels(state, '/ai/models?create=1')
    const empty = wrapper.get('[data-test="model-no-provider"]')
    expect(empty.text()).toContain('请先创建 Provider')
    expect(wrapper.get('[data-test="goto-providers"]').attributes('href')).toBe('/ai/providers?create=1')
    expect(wrapper.get('[data-test="form-save"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })
})
