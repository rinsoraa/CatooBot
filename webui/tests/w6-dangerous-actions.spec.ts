/**
 * W6 §37/§38：危险动作必须先有站内确认，确认前的写入一律不发。
 *
 * 覆盖 13 个危险动作：Provider/Model 删除、记忆删除、贴纸/口癖删除、
 * 工具测试、工具缓存清空、世界重置/重新初始化、Runtime action/tick、
 * Router 重置、原始 YAML 应用。凡是后端要求 confirm 串的，确认后请求体
 * 必须带上契约里的值。
 *
 * Provider / Model 删除现已双保险：页面先弹 ConfirmDialog（确认前零请求），
 * 请求体带 confirm=<名字>；级联 force 路径同样先确认，confirm=force。
 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import Advanced from '@/pages/system/Advanced.vue'
import AiFailover from '@/pages/ai/AiFailover.vue'
import AiModels from '@/pages/ai/AiModels.vue'
import AiProviders from '@/pages/ai/AiProviders.vue'
import Media from '@/pages/abilities/Media.vue'
import MemoryDetail from '@/pages/memory/MemoryDetail.vue'
import Runtime from '@/pages/system/Runtime.vue'
import ToolDetail from '@/pages/abilities/ToolDetail.vue'
import Tools from '@/pages/abilities/Tools.vue'
import World from '@/pages/character/World.vue'
import { useToast } from '@/composables/toast'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'
import type { MemoryDetail as MemoryDetailPayload } from '@/api/memory'
import type { AiStatus, ModelItem, ProviderItem } from '@/types/ai'
import type {
  ExpressionRow,
  MemoryRow,
  StickerRow,
  ToolDetail as ToolDetailModel,
  ToolRow,
  ToolTestResult,
  WorldData,
} from '@/types/domain'

const originalFetch = globalThis.fetch

// ------------------------------------------------------------------ fixtures

const PROVIDER: ProviderItem = {
  name: 'alpha',
  type: 'openai_compatible',
  base_url: 'https://api.alpha.com/v1',
  api_key_env: 'CATOOBOT_ALPHA_API_KEY',
  has_key: true,
  models: ['fast'],
  restart_required: false,
}

const MODEL: ModelItem = {
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
}

const AI_STATUS: AiStatus = {
  status: 'ready',
  enabled: true,
  configured: true,
  checks: { has_provider: true, has_credential: true, has_model: true, chat_bound: true },
  providers: { total: 1, with_key: 1, missing_key: [] },
  models: { total: 1, enabled: 1, disabled: 0, usable: 1, cooldown: 0 },
  chat_model: 'fast',
  fallback_chain: ['fast'],
  errors: { rate_limited: 0, server_errors: 0 },
  cooldown_models: [],
}

const MEMORY: MemoryRow = {
  memory_id: 12,
  content: '测试记忆内容',
  summary: '测试记忆',
  layer: 'semantic',
  category: 'fact',
  scope_key: 'user:10001',
  status: 'active',
  importance: 0.5,
  confidence: 0.7,
  created_at: 1700000012,
  updated_at: 1700000112,
  provenance: {},
}

const MEMORY_DETAIL: MemoryDetailPayload = { memory: MEMORY, relations: [], supersedes: [] }

const TOOL: ToolRow = {
  name: 'weather',
  display_name: '天气',
  description: '查询天气',
  category: '生活',
  risk_level: 'low',
  enabled: true,
  requires_credentials: false,
  has_credential: null,
  timeout: 10,
  cache_ttl_seconds: 300,
  calls: 5,
  failures: 1,
  last_used_at: null,
}

const TOOL_DETAIL: ToolDetailModel = {
  ...TOOL,
  input_schema: { type: 'object' },
  output_schema: { type: 'object' },
  metrics: {},
  recent_executions: [],
  permissions_summary: { count: 0, rules: [] },
}

const TOOL_TEST_RESULT: ToolTestResult = {
  ok: true,
  result: { temperature: 20 },
  error: '',
  duration_ms: 12,
  may_have_called_external: true,
  note: 'stubbed',
}

const STICKER: StickerRow = {
  sticker_id: 'st-1',
  file: 'stickers/a.png',
  file_name: 'a.png',
  preview_url: null,
  emotion: 'happy',
  intent: 'greet',
  status: 'active',
  origin: 'collected',
  origin_user: '10001',
  usage_count: 2,
  last_used_at: null,
  created_at: 1700000000,
  safety_status: 'ok',
  valid: true,
}

const EXPRESSION: ExpressionRow = {
  pattern_id: 'p-1',
  pattern: '好耶',
  kind: 'phrase',
  status: 'active',
  group_id: '',
  occurrences: 3,
  speakers: 1,
  first_seen: 1700000000,
  last_seen: 1700000100,
}

const WORLD: WorldData = {
  phase: 'day',
  location: 'home',
  action: { name: 'reading', progress: 0.5 },
  modes: ['normal'],
  needs: {},
  world_revision: 3,
  cognitive_revision: 1,
}

const OVERVIEW = {
  qq: { online: true, self_id: 10001 },
  ai: { enabled: true, current_model: 'fast', models_ok: 1, models_total: 1, requests: 1, errors: 0, rate_limited: 0 },
  world: { phase: 'day', location: 'home', action: { name: 'reading' }, modes: [], needs: {}, world_revision: 1, cognitive_revision: 1 },
  runtime: { scheduler: { running: true, interval_seconds: 30, ticks: 3, catchups: 0 } },
  counts: {},
}

const RUNTIME = { scheduler: { running: true, interval_seconds: 30, ticks: 3, catchups: 0 }, database: { connected: true } }

const RAW_YAML = 'bot:\n  name: CatooBot\n'
const NEW_YAML = 'bot:\n  name: Renamed\n'

// ------------------------------------------------------------------ helpers

const MEMORY_ROUTES: RouteRecordRaw[] = [
  { path: '/memory', name: 'memory', component: { template: '<div />' } },
  { path: '/memory/:memoryId(\\d+)', name: 'memory-detail', component: { template: '<div />' } },
]
const TOOL_ROUTES: RouteRecordRaw[] = [
  { path: '/abilities/tools', name: 'abilities-tools', component: { template: '<div />' } },
  { path: '/abilities/tools/:name', name: 'abilities-tool', component: { template: '<div />' } },
]
const AI_ROUTES: RouteRecordRaw[] = [
  { path: '/ai', name: 'ai-overview', component: { template: '<div />' } },
  { path: '/ai/providers', name: 'ai-providers', component: { template: '<div />' } },
  { path: '/ai/models', name: 'ai-models', component: { template: '<div />' } },
  { path: '/ai/roles', name: 'ai-roles', component: { template: '<div />' } },
  { path: '/ai/failover', name: 'ai-failover', component: { template: '<div />' } },
]

async function mountPage(
  component: Component,
  options: { path?: string; routes?: RouteRecordRaw[] } = {},
): Promise<{ wrapper: VueWrapper; router: Router | null }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  if (options.path && options.routes) {
    const router: Router = createRouter({ history: createMemoryHistory(), routes: options.routes })
    await router.push(options.path)
    await router.isReady()
    const wrapper = mount(component, { global: { plugins: [pinia, router] } })
    await flushAll()
    return { wrapper, router }
  }
  const wrapper = mount(component, { global: { plugins: [pinia] } })
  await flushAll()
  return { wrapper, router: null }
}

/** 非 GET 请求 = 会改变服务端状态的请求。 */
function writes(calls: MockRequest[]): MockRequest[] {
  return calls.filter((call) => call.method !== 'GET')
}

function forceOf(call: MockRequest): string | null {
  return new URL(call.url, 'http://localhost').searchParams.get('force')
}

function pathOf(call: MockRequest): string {
  return call.url.split('?')[0] ?? call.url
}

function dialogVisible(wrapper: VueWrapper): boolean {
  return wrapper.find('[data-test="confirm"]').exists()
}

function unhandled(request: MockRequest): MockReply {
  return fail(404, 'resource.not_found', `未 mock 的请求：${request.method} ${request.url}`)
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
  globalThis.fetch = originalFetch
})

describe('W6 危险动作确认门（§37/§38）', () => {
  it('Provider 删除：先确认（confirm=名字），级联 force 删除再确认（confirm=force）', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/ai/status') return ok(AI_STATUS)
      if (path === '/api/v1/ai/providers' && request.method === 'GET') return ok({ items: [PROVIDER] })
      if (path === '/api/v1/ai/models') return ok({ items: [MODEL] })
      if (path === '/api/v1/ai/roles') return ok({ items: [] })
      if (path === '/api/v1/ai/providers/alpha' && request.method === 'DELETE') {
        if (forceOf(request) === '1') return ok({ deleted: true, models_removed: 1 })
        return fail(
          409,
          'ai.provider_in_use',
          'Provider「alpha」仍被 1 个模型使用：fast；请先删除这些模型，或带 force=1 连带删除',
        )
      }
      return unhandled(request)
    })

    const { wrapper } = await mountPage(AiProviders, { path: '/ai/providers', routes: AI_ROUTES })
    await wrapper.get('[data-test="provider-alpha"] [data-test="delete-provider"]').trigger('click')
    await flushAll()

    // 站内确认框先出现，确认前零写请求
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls), '确认前不应发出删除请求').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const probe = writes(calls)
    expect(probe).toHaveLength(1)
    expect(probe[0]?.method).toBe('DELETE')
    expect(forceOf(probe[0] as MockRequest)).toBeNull()
    expect(probe[0]?.body).toEqual({ confirm: 'alpha' })
    expect(wrapper.find('[data-test="provider-conflict"]').exists()).toBe(true)

    await wrapper.get('[data-test="conflict-force"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper), '级联删除前必须出现确认框').toBe(true)
    expect(writes(calls), '确认前不应发出第二次写请求').toHaveLength(1)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = writes(calls).filter((call) => call.method === 'DELETE')
    expect(deletes).toHaveLength(2)
    expect(forceOf(deletes[1] as MockRequest)).toBe('1')
    expect(deletes[1]?.body).toEqual({ confirm: 'force' })
    wrapper.unmount()
  })

  it('Provider 删除：未被引用也要先确认，确认后 DELETE 带 confirm=名字', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/ai/status') return ok(AI_STATUS)
      if (path === '/api/v1/ai/providers' && request.method === 'GET') return ok({ items: [PROVIDER] })
      if (path === '/api/v1/ai/models') return ok({ items: [] })
      if (path === '/api/v1/ai/roles') return ok({ items: [] })
      if (path === '/api/v1/ai/providers/alpha' && request.method === 'DELETE') {
        return ok({ deleted: true, models_removed: 0 })
      }
      return unhandled(request)
    })

    const { wrapper } = await mountPage(AiProviders, { path: '/ai/providers', routes: AI_ROUTES })
    await wrapper.get('[data-test="provider-alpha"] [data-test="delete-provider"]').trigger('click')
    await flushAll()

    // 确认框先出现，确认前零请求
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('.cb-dialog__message').text()).toContain('删除后无法恢复')
    expect(writes(calls), '确认前不应发出删除请求').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const sent = writes(calls)
    expect(sent).toHaveLength(1)
    expect(sent[0]?.method).toBe('DELETE')
    expect(pathOf(sent[0] as MockRequest)).toBe('/api/v1/ai/providers/alpha')
    expect(forceOf(sent[0] as MockRequest)).toBeNull()
    expect(sent[0]?.body).toEqual({ confirm: 'alpha' })
    wrapper.unmount()
  })

  it('Model 删除：先确认（confirm=名字），级联 force 删除再确认（confirm=force）', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/ai/models' && request.method === 'GET') return ok({ items: [MODEL] })
      if (path === '/api/v1/ai/status') return ok(AI_STATUS)
      if (path === '/api/v1/ai/providers') return ok({ items: [PROVIDER] })
      if (path === '/api/v1/ai/roles') return ok({ items: [] })
      if (path === '/api/v1/ai/models/fast' && request.method === 'DELETE') {
        if (forceOf(request) === '1') return ok({ deleted: true, roles_cleared: ['chat'] })
        return fail(409, 'ai.model_in_use', '模型「fast」仍被角色使用：chat；请先改绑角色，或带 force=1 强制删除')
      }
      return unhandled(request)
    })

    const { wrapper } = await mountPage(AiModels, { path: '/ai/models', routes: AI_ROUTES })
    await wrapper.get('[data-test="model-row-fast"] [data-test="delete-model"]').trigger('click')
    await flushAll()

    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls), '确认前不应发出删除请求').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const probe = writes(calls)
    expect(probe).toHaveLength(1)
    expect(probe[0]?.method).toBe('DELETE')
    expect(forceOf(probe[0] as MockRequest)).toBeNull()
    expect(probe[0]?.body).toEqual({ confirm: 'fast' })
    expect(wrapper.find('[data-test="model-conflict"]').exists()).toBe(true)

    await wrapper.get('[data-test="conflict-force"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper), '强制删除前必须出现确认框').toBe(true)
    expect(writes(calls), '确认前不应发出第二次写请求').toHaveLength(1)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = writes(calls).filter((call) => call.method === 'DELETE')
    expect(deletes).toHaveLength(2)
    expect(forceOf(deletes[1] as MockRequest)).toBe('1')
    expect(deletes[1]?.body).toEqual({ confirm: 'force' })
    wrapper.unmount()
  })

  it('记忆删除：确认前零请求，确认后 POST /memories/12/delete 且 body 带 confirm=delete', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/memories/12') return ok(MEMORY_DETAIL)
      if (path === '/api/v1/memories/12/delete' && request.method === 'POST') return ok({ ok: true })
      if (path === '/api/v1/memories') return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
      if (path === '/api/v1/memories/health') return ok({})
      return unhandled(request)
    })

    const { wrapper } = await mountPage(MemoryDetail, { path: '/memory/12', routes: MEMORY_ROUTES })
    expect(wrapper.text()).toContain('测试记忆内容')

    await wrapper.get('[data-test="detail-delete"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls), '确认前不应发出删除请求').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = writes(calls).filter((call) => pathOf(call).endsWith('/delete'))
    expect(deletes).toHaveLength(1)
    expect(deletes[0]?.method).toBe('POST')
    expect(deletes[0]?.body).toEqual({ confirm: 'delete' })
    wrapper.unmount()
  })

  it('贴纸删除：确认前零请求，确认后 body 带 confirm=delete', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/stickers' && request.method === 'GET') return ok({ items: [STICKER], stats: {}, total: 1 })
      if (path === '/api/v1/expressions') return ok({ items: [], stats: {} })
      if (path === '/api/v1/stickers/st-1/delete') return ok({ sticker_id: 'st-1', action: 'delete', status: 'archived' })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Media)
    await wrapper.get('[data-test="sticker-delete"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls), '确认前不应发出删除请求').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = writes(calls)
    expect(deletes).toHaveLength(1)
    expect(pathOf(deletes[0] as MockRequest)).toBe('/api/v1/stickers/st-1/delete')
    expect(deletes[0]?.body).toEqual({ confirm: 'delete' })
    wrapper.unmount()
  })

  it('口癖删除：确认前零请求，确认后 body 带 confirm=delete', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/stickers') return ok({ items: [], stats: {}, total: 0 })
      if (path === '/api/v1/expressions' && request.method === 'GET') return ok({ items: [EXPRESSION], stats: {} })
      if (path === '/api/v1/expressions/p-1/delete') return ok({ ok: true })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Media)
    await wrapper.get('[data-test="expression-delete"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls)).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = writes(calls)
    expect(deletes).toHaveLength(1)
    expect(pathOf(deletes[0] as MockRequest)).toBe('/api/v1/expressions/p-1/delete')
    expect(deletes[0]?.body).toEqual({ confirm: 'delete' })
    wrapper.unmount()
  })

  it('工具测试：确认前零请求，确认后 body 带 confirm=<工具名>', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/tools/weather' && request.method === 'GET') return ok(TOOL_DETAIL)
      if (path === '/api/v1/tools/weather/test' && request.method === 'POST') return ok(TOOL_TEST_RESULT)
      return unhandled(request)
    })

    const { wrapper } = await mountPage(ToolDetail, { path: '/abilities/tools/weather', routes: TOOL_ROUTES })
    await wrapper.get('[data-test="tool-test"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('真的发起一次外部请求')
    expect(writes(calls), '确认前不应执行工具').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const test = writes(calls)
    expect(test).toHaveLength(1)
    expect(pathOf(test[0] as MockRequest)).toBe('/api/v1/tools/weather/test')
    expect(test[0]?.body).toEqual({ arguments: {}, confirm: 'weather' })
    wrapper.unmount()
  })

  it('工具缓存清空：确认前零请求，确认后 body 带 confirm=clear', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/tools') {
        return ok({
          items: [TOOL],
          policy: { allowed_risk_levels: ['low'], rate_limit: {} },
          stats: { total: 1, enabled_count: 1, calls: 0, failure: 0, cache_hits: 0 },
        })
      }
      if (path === '/api/v1/tools/permissions') return ok({ items: [], total: 0 })
      if (path === '/api/v1/tools/cache/clear') return ok({ cleared: 3, tool: '' })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Tools)
    await wrapper.get('[data-test="tools-clear-cache"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls)).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const clear = writes(calls)
    expect(clear).toHaveLength(1)
    expect(pathOf(clear[0] as MockRequest)).toBe('/api/v1/tools/cache/clear')
    expect(clear[0]?.body).toEqual({ confirm: 'clear', name: '' })
    wrapper.unmount()
  })

  it('世界重置：确认前零请求，确认后 body 带 confirm=reset', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/world' && request.method === 'GET') return ok(WORLD)
      if (path === '/api/v1/world/control/reset' && request.method === 'POST') return ok({ ok: true, phase: 'day' })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(World)
    await wrapper.get('[data-test="world-control-reset"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('不可撤销')
    expect(writes(calls), '确认前不应重置世界').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const control = writes(calls)
    expect(control).toHaveLength(1)
    expect(pathOf(control[0] as MockRequest)).toBe('/api/v1/world/control/reset')
    expect(control[0]?.body).toEqual({ confirm: 'reset' })
    wrapper.unmount()
  })

  it('世界重新初始化：确认前零请求，确认后 body 带 confirm=reinitialize', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/world' && request.method === 'GET') return ok(WORLD)
      if (path === '/api/v1/world/control/reinitialize' && request.method === 'POST') {
        return ok({ ok: true, phase: 'day' })
      }
      return unhandled(request)
    })

    const { wrapper } = await mountPage(World)
    await wrapper.get('[data-test="world-control-reinitialize"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls)).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const control = writes(calls)
    expect(control).toHaveLength(1)
    expect(pathOf(control[0] as MockRequest)).toBe('/api/v1/world/control/reinitialize')
    expect(control[0]?.body).toEqual({ confirm: 'reinitialize' })
    wrapper.unmount()
  })

  it('Runtime action：确认前零请求，确认后才调用运行时动作', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/overview') return ok(OVERVIEW)
      if (path === '/api/v1/runtime' && request.method === 'GET') return ok(RUNTIME)
      if (path === '/api/v1/runtime/actions/reload_persona') return ok({ done: true, action: 'reload_persona', detail: 'ok' })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Runtime)
    await wrapper.get('[data-test="runtime-action-reload_persona"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('重载角色人设')
    expect(writes(calls), '确认前不应执行运行时动作').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const actions = writes(calls)
    expect(actions).toHaveLength(1)
    expect(pathOf(actions[0] as MockRequest)).toBe('/api/v1/runtime/actions/reload_persona')
    wrapper.unmount()
  })

  it('Runtime tick：确认前零请求，确认后才推进一次 tick', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/overview') return ok(OVERVIEW)
      if (path === '/api/v1/runtime' && request.method === 'GET') return ok(RUNTIME)
      if (path === '/api/v1/runtime/tick') return ok({ ran: true, minutes: 15, report: { minutes: 15 }, ticks: 4 })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Runtime)
    await wrapper.get('[data-test="runtime-tick"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('真实调用')
    expect(writes(calls)).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const ticks = writes(calls)
    expect(ticks).toHaveLength(1)
    expect(pathOf(ticks[0] as MockRequest)).toBe('/api/v1/runtime/tick')
    wrapper.unmount()
  })

  it('Router 重置：确认前零请求，确认后 body 带 confirm=reset', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/ai/models' && request.method === 'GET') return ok({ items: [MODEL] })
      if (path === '/api/v1/ai/status') return ok(AI_STATUS)
      if (path === '/api/v1/ai/router/reset' && request.method === 'POST') return ok({ reset: true })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(AiFailover, { path: '/ai/failover', routes: AI_ROUTES })
    await wrapper.get('[data-test="failover-reset"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(writes(calls)).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const reset = writes(calls)
    expect(reset).toHaveLength(1)
    expect(pathOf(reset[0] as MockRequest)).toBe('/api/v1/ai/router/reset')
    expect(reset[0]?.body).toEqual({ confirm: 'reset' })
    wrapper.unmount()
  })

  it('原始 YAML 应用：确认前不发 PUT，确认后 body 带 confirm=raw', async () => {
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/config/raw' && request.method === 'GET') {
        return ok({ yaml: RAW_YAML, path: '/srv/catoobot/config/overrides.yaml' })
      }
      if (path === '/api/v1/config/raw' && request.method === 'PUT') return ok({ saved: true, notes: [] })
      if (path === '/api/v1/config/effective') return ok({ items: [], count: 0 })
      if (path === '/api/v1/config/restart-pending') return ok({ pending: [], since: null })
      return unhandled(request)
    })

    const { wrapper } = await mountPage(Advanced)
    const editor = wrapper.get('[data-test="advanced-editor"] textarea')
    expect((editor.element as HTMLTextAreaElement).value).toBe(RAW_YAML)
    await editor.setValue(NEW_YAML)

    await wrapper.get('[data-test="advanced-save"]').trigger('click')
    await flushAll()
    expect(dialogVisible(wrapper)).toBe(true)
    expect(wrapper.get('.cb-dialog__message').text()).toContain('这会替换 overrides.yaml')
    expect(writes(calls), '确认前不应写入 YAML').toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const puts = writes(calls).filter((call) => call.method === 'PUT')
    expect(puts).toHaveLength(1)
    expect(pathOf(puts[0] as MockRequest)).toBe('/api/v1/config/raw')
    expect(puts[0]?.body).toEqual({ yaml: NEW_YAML, confirm: 'raw' })
    wrapper.unmount()
  })
})
