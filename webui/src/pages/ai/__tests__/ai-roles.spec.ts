/** 模型用途页（W4 §27-§30、§103）：切换调用、重启/生效两种提示、未配置标记。 */
import { flushPromises, mount, RouterLinkStub, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiRoles from '@/pages/ai/AiRoles.vue'
import type { ToastItem } from '@/composables/toast'
import { useToast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import type { AiStatus, ModelItem, RoleItem } from '@/types/ai'

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
    JSON.stringify({ ok: false, error: { code: 'ai.invalid_value', message }, meta: { request_id: 't' } }),
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

const ROLES: RoleItem[] = [
  { role: 'chat', key: 'ai.models[0].name', model: 'fast', source: 'models', restart_required: false },
  { role: 'vision', key: 'media.vision_model', model: '', source: 'default', restart_required: true },
  { role: 'planner', key: 'agent.planner.model', model: 'smart', source: 'overrides', restart_required: true },
]

const STATUS: AiStatus = {
  status: 'ready',
  enabled: true,
  configured: true,
  checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
  providers: { total: 1, with_key: 1, missing_key: [] },
  models: { total: 2, enabled: 2, disabled: 0, usable: 2, cooldown: 0 },
  chat_model: 'fast',
  fallback_chain: ['fast', 'smart'],
  errors: { rate_limited: 0, server_errors: 0 },
  cooldown_models: [],
}

function lastToast(): ToastItem | undefined {
  return useToast().items.value.at(-1)
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiRoles, {
    global: { plugins: [pinia], stubs: { RouterLink: RouterLinkStub } },
  })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
  requests = []
  installFetch((url, method) => {
    if (url.includes('/ai/roles') && method === 'GET') return envelope({ items: ROLES })
    if (url.includes('/ai/models') && method === 'GET') return envelope({ items: [model('fast'), model('smart')] })
    if (url.includes('/ai/status')) return envelope(STATUS)
    if (url.includes('/ai/roles/chat') && method === 'PUT') {
      return envelope({ role: 'chat', key: 'ai.models[0].name', model: 'smart', source: 'overrides', restart_required: false })
    }
    if (url.includes('/ai/roles/planner') && method === 'PUT') {
      return envelope({ role: 'planner', key: 'agent.planner.model', model: 'fast', source: 'overrides', restart_required: true })
    }
    return failure(404, `未 mock 的请求：${method} ${url}`)
  })
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiRoles', () => {
  it('renders nine-style role cards with Chinese labels, 未配置, and the chain semantics link', async () => {
    const wrapper = await mountPage()
    const cards = wrapper.findAll('[data-test="role-card"]')
    expect(cards).toHaveLength(3)
    expect(wrapper.text()).toContain('聊天回复')
    expect(wrapper.text()).toContain('图片理解')
    expect(wrapper.text()).toContain('任务规划（Planner）')
    expect(wrapper.get('[data-role="vision"]').get('[data-test="role-unset"]').text()).toBe('⚠ 未配置')

    const text = wrapper.get('[data-test="roles-intro"]').text()
    expect(text).toContain('默认聊天模型 = 故障转移链的第一个模型')
    expect(text).toContain('调整顺序')

    const links = wrapper.findAllComponents(RouterLinkStub)
    expect(links.length).toBeGreaterThanOrEqual(2)
    for (const link of links) expect(link.props('to')).toEqual({ name: 'ai-failover' })
  })

  it('calls setRole and toasts 已保存，重启后生效 for restart-required roles', async () => {
    const wrapper = await mountPage()
    const planner = wrapper.get('[data-role="planner"]')
    await planner.get('[data-test="role-select"]').setValue('fast')
    await settle()

    const write = requests.find((request) => request.method === 'PUT')
    expect(write?.url).toContain('/api/v1/ai/roles/planner')
    expect(write?.body).toEqual({ model: 'fast' })
    expect(lastToast()?.message).toBe('已保存，重启后生效')
  })

  it('toasts 已保存 and shows ● 已生效 for hot roles', async () => {
    const wrapper = await mountPage()
    const chat = wrapper.get('[data-role="chat"]')
    await chat.get('[data-test="role-select"]').setValue('smart')
    await settle()

    const write = requests.find((request) => request.method === 'PUT')
    expect(write?.url).toContain('/api/v1/ai/roles/chat')
    expect(write?.body).toEqual({ model: 'smart' })
    expect(lastToast()?.message).toBe('已保存')
    expect(wrapper.get('[data-role="chat"]').get('[data-test="roles-applied"]').text()).toContain('● 已生效')
  })

  it('shows the backend message when the role change is rejected', async () => {
    installFetch((url, method) => {
      if (url.includes('/ai/roles') && method === 'GET') return envelope({ items: ROLES })
      if (url.includes('/ai/models') && method === 'GET') return envelope({ items: [model('fast')] })
      if (url.includes('/ai/status')) return envelope(STATUS)
      if (url.includes('/ai/roles/chat') && method === 'PUT') {
        return failure(400, 'chat 角色不能为空：默认模型必须是一个已有模型')
      }
      return failure(404, `未 mock 的请求：${method} ${url}`)
    })
    const wrapper = await mountPage()
    await wrapper.get('[data-role="chat"] [data-test="role-select"]').setValue('')
    await settle()

    expect(lastToast()?.kind).toBe('error')
    expect(lastToast()?.message).toContain('chat 角色不能为空')
    expect(useAiStore().error).toContain('chat 角色不能为空')
  })
})
