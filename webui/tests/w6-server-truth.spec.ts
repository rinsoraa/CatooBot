/**
 * W6 §58/§59/§60：写操作后必须重新从服务端取真实状态。
 *
 * 五个代表流程：社交群参与开关、AI 模型启停、记忆归档、工具权限新增、
 * 配置应用。每个流程都断言调用顺序是「mutation → GET(refetch)」，并且
 * 当重取结果与本地请求/乐观预期不一致时，界面显示的是服务端返回的值。
 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import type { Component } from 'vue'
import { createMemoryHistory, createRouter, type RouteRecordRaw } from 'vue-router'
import { afterEach, describe, expect, it } from 'vitest'

import AiModels from '@/pages/ai/AiModels.vue'
import SocialGroups from '@/pages/social/SocialGroups.vue'
import Tools from '@/pages/abilities/Tools.vue'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'
import { useAiStore } from '@/stores/ai'
import { useConfigStore } from '@/stores/config'
import { useMemoryStore } from '@/stores/memory'
import { useSocialStore } from '@/stores/social'
import type { ApplyResult, EffectiveField } from '@/types/config'
import type { MemoryRow, ToolRow } from '@/types/domain'
import type { GroupRow } from '@/types/social'
import type { AiStatus, ModelItem, ProviderItem } from '@/types/ai'

const originalFetch = globalThis.fetch

const AI_ROUTES: RouteRecordRaw[] = [
  { path: '/ai', name: 'ai-overview', component: { template: '<div />' } },
  { path: '/ai/models', name: 'ai-models', component: { template: '<div />' } },
  { path: '/ai/providers', name: 'ai-providers', component: { template: '<div />' } },
  { path: '/ai/roles', name: 'ai-roles', component: { template: '<div />' } },
]

function pathOf(call: MockRequest): string {
  return call.url.split('?')[0] ?? call.url
}

function indexOf(calls: MockRequest[], predicate: (call: MockRequest) => boolean, from = 0): number {
  for (let index = from; index < calls.length; index += 1) {
    const call = calls[index]
    if (call && predicate(call)) return index
  }
  return -1
}

function unhandled(request: MockRequest): MockReply {
  return fail(404, 'resource.not_found', `未 mock 的请求：${request.method} ${request.url}`)
}

/** 挂载带内存路由的页面。 */
async function mountPage(
  component: Component,
  pinia: Pinia,
  path: string,
  routes: RouteRecordRaw[],
): Promise<VueWrapper> {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [pinia, router] } })
  await flushAll()
  return wrapper
}

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('W6 服务端真相（§58/§59/§60）', () => {
  it('社交群参与开关：PATCH 之后 GET 重取，界面显示服务端返回的参与状态', async () => {
    const GROUP: GroupRow = {
      group_id: 'g-1',
      name: '老友群',
      last_seen: 1700000000,
      participation_enabled: true,
      notes: '',
      tags: [],
      interaction_count: 42,
    }
    let groupGets = 0
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/social/groups' && request.method === 'GET') {
        groupGets += 1
        // 重取时服务端仍返回「已参与」：客户端不得自己改成请求里的 false。
        return ok({ items: [{ ...GROUP, participation_enabled: true }], total: 1 })
      }
      if (path === '/api/v1/social/groups/g-1' && request.method === 'PATCH') {
        return ok({ ...GROUP, participation_enabled: true })
      }
      return unhandled(request)
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = await mountPage(SocialGroups, pinia, '/social/groups', [
      { path: '/social/groups', name: 'social-groups', component: { template: '<div />' } },
    ])
    expect(wrapper.get('[data-test="group-toggle"]').text()).toContain('已参与')

    await wrapper.get('[data-test="group-toggle"]').trigger('click')
    await flushAll()

    const patchIndex = indexOf(calls, (call) => call.method === 'PATCH')
    const refetchIndex = indexOf(
      calls,
      (call) => pathOf(call) === '/api/v1/social/groups' && call.method === 'GET',
      patchIndex + 1,
    )
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(refetchIndex, 'PATCH 之后必须重新 GET 群列表').toBeGreaterThan(patchIndex)
    expect(calls[patchIndex]?.body).toEqual({ participation_enabled: false })
    expect(groupGets).toBe(2)

    // 服务端说 true，界面就必须是 true（无乐观更新）。
    expect(useSocialStore(pinia).groups[0]?.participation_enabled).toBe(true)
    expect(wrapper.get('[data-test="group-toggle"]').text()).toContain('已参与')
    wrapper.unmount()
  })

  it('AI 模型启停：PUT 之后 GET 重取，界面显示服务端返回的 enabled', async () => {
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
    const STATUS: AiStatus = {
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
    let modelGets = 0
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/ai/models' && request.method === 'GET') {
        modelGets += 1
        // 重取时服务端仍是 enabled=true：UI 不得停留在请求里的 false。
        return ok({ items: [{ ...MODEL, enabled: true }] })
      }
      if (path === '/api/v1/ai/models/fast' && request.method === 'PUT') {
        return ok({ ...MODEL, enabled: false })
      }
      if (path === '/api/v1/ai/status') return ok(STATUS)
      if (path === '/api/v1/ai/providers') return ok({ items: [PROVIDER] })
      if (path === '/api/v1/ai/roles') return ok({ items: [] })
      return unhandled(request)
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = await mountPage(AiModels, pinia, '/ai/models', AI_ROUTES)
    expect(wrapper.get('[data-test="toggle-fast"]').attributes('aria-checked')).toBe('true')

    await wrapper.get('[data-test="toggle-fast"]').trigger('click')
    await flushAll()

    const putIndex = indexOf(calls, (call) => call.method === 'PUT')
    const refetchIndex = indexOf(
      calls,
      (call) => pathOf(call) === '/api/v1/ai/models' && call.method === 'GET',
      putIndex + 1,
    )
    expect(putIndex).toBeGreaterThanOrEqual(0)
    expect(refetchIndex, 'PUT 之后必须重新 GET 模型列表').toBeGreaterThan(putIndex)
    expect(calls[putIndex]?.body).toEqual({ enabled: false })
    expect(modelGets).toBeGreaterThanOrEqual(2)

    expect(useAiStore(pinia).models[0]?.enabled).toBe(true)
    expect(wrapper.get('[data-test="toggle-fast"]').attributes('aria-checked')).toBe('true')
    expect(wrapper.get('[data-test="model-row-fast"]').text()).toContain('已启用')
    wrapper.unmount()
  })

  it('记忆归档：POST 之后 GET 重取，列表显示服务端返回的状态而不是本地猜测', async () => {
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
    let memoryGets = 0
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/memories' && request.method === 'GET') {
        memoryGets += 1
        // 服务端重取仍返回 active（例如归档未生效）：UI 必须如实显示 active。
        return ok({ items: [{ ...MEMORY, status: 'active' }], total: 1, limit: 20, offset: 0, next_cursor: null })
      }
      if (path === '/api/v1/memories/12/archive' && request.method === 'POST') return ok({ ok: true })
      if (path === '/api/v1/memories/health') return ok({})
      return unhandled(request)
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useMemoryStore(pinia)
    await store.load()
    expect(store.items[0]?.status).toBe('active')

    const done = await store.act(12, 'archive', {})
    expect(done).toBe(true)

    const postIndex = indexOf(calls, (call) => call.method === 'POST')
    const refetchIndex = indexOf(
      calls,
      (call) => pathOf(call) === '/api/v1/memories' && call.method === 'GET',
      postIndex + 1,
    )
    expect(postIndex).toBeGreaterThanOrEqual(0)
    expect(refetchIndex, '归档之后必须重新 GET 记忆列表').toBeGreaterThan(postIndex)
    expect(memoryGets).toBe(2)

    // 服务端说 active，store 就必须是 active（没有本地写入最终状态）。
    expect(store.items[0]?.status).toBe('active')
  })

  it('工具权限新增：PUT 之后 GET 重取，表格显示服务端返回的规则', async () => {
    const TOOLS: ToolRow[] = [
      {
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
      },
    ]
    let permissionGets = 0
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/tools') {
        return ok({
          items: TOOLS,
          policy: { allowed_risk_levels: ['low'], rate_limit: {} },
          stats: { total: 1, enabled_count: 1, calls: 0, failure: 0, cache_hits: 0 },
        })
      }
      if (path === '/api/v1/tools/permissions' && request.method === 'GET') {
        permissionGets += 1
        // 初始为空；保存后的重取返回 allowed=false 的规则：表格必须显示「拒绝」。
        if (permissionGets === 1) return ok({ items: [], total: 0 })
        return ok({
          items: [{ scope: 'user', ref: '20002', tool_name: 'weather', allowed: false, created_at: 1 }],
          total: 1,
        })
      }
      if (path === '/api/v1/tools/permissions' && request.method === 'PUT') return ok({ saved: true })
      return unhandled(request)
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = await mountPage(Tools, pinia, '/abilities/tools', [
      { path: '/abilities/tools', name: 'abilities-tools', component: { template: '<div />' } },
    ])
    expect(wrapper.find('[data-test="permissions-empty"]').exists()).toBe(true)

    await wrapper.get('[data-test="permission-scope-id"]').setValue('20002')
    await wrapper.get('[data-test="permission-tool"]').setValue('weather')
    await wrapper.get('[data-test="permission-form"]').trigger('submit')
    await flushAll()

    const putIndex = indexOf(calls, (call) => call.method === 'PUT')
    const refetchIndex = indexOf(
      calls,
      (call) => pathOf(call) === '/api/v1/tools/permissions' && call.method === 'GET',
      putIndex + 1,
    )
    expect(putIndex).toBeGreaterThanOrEqual(0)
    expect(refetchIndex, '保存权限后必须重新 GET 权限列表').toBeGreaterThan(putIndex)
    expect(permissionGets).toBe(2)

    expect(calls[putIndex]?.body).toEqual({ scope: 'user', scope_id: '20002', tool: 'weather', allowed: true })
    // 请求 allowed=true，服务端回 false —— 界面显示服务端的「拒绝」。
    expect(wrapper.get('[data-test="permissions-table"]').text()).toContain('拒绝')
    wrapper.unmount()
  })

  it('配置应用：PATCH 之后重取 effective / restart-pending，store 显示服务端的值', async () => {
    const SERVER_ROW: EffectiveField = {
      key: 'ai.temperature',
      label: '温度',
      area: 'ai',
      level: 'basic',
      type: 'float',
      usage_status: 'ACTIVE',
      hot_reload: true,
      restart_required: false,
      hidden: false,
      value: 0.3,
      source: 'overrides',
    }
    const APPLY_RESULT: ApplyResult = {
      saved: ['ai.temperature'],
      hot_reload: ['ai.temperature'],
      restart_required: [],
      effective: [true],
      values: [{ ...SERVER_ROW, value: 0.9 }],
      notes: [],
      warnings: [],
    }
    let effectiveGets = 0
    const calls = installFetch((request) => {
      const path = pathOf(request)
      if (path === '/api/v1/config/effective' && request.method === 'GET') {
        effectiveGets += 1
        // 第一次是初始值 0.7；应用后的重取返回服务端真实值 0.3。
        return ok({
          items: [effectiveGets === 1 ? { ...SERVER_ROW, value: 0.7 } : SERVER_ROW],
          count: 1,
        })
      }
      if (path === '/api/v1/config/restart-pending') return ok({ pending: ['ai.temperature'], since: 1700000200 })
      if (path === '/api/v1/config' && request.method === 'PATCH') return ok(APPLY_RESULT)
      return unhandled(request)
    })

    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useConfigStore(pinia)
    await store.loadEffective()
    expect(store.effectiveByKey.get('ai.temperature')?.value).toBe(0.7)

    store.setValue('ai.temperature', 0.9)
    expect(store.isDirty).toBe(true)

    const result = await store.apply()
    expect(result).not.toBeNull()

    const patchIndex = indexOf(calls, (call) => call.method === 'PATCH')
    const refetchIndex = indexOf(
      calls,
      (call) => pathOf(call) === '/api/v1/config/effective' && call.method === 'GET',
      patchIndex + 1,
    )
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(refetchIndex, '应用后必须重取 effective').toBeGreaterThan(patchIndex)
    expect(effectiveGets).toBe(2)
    expect(calls[patchIndex]?.body).toEqual({ values: { 'ai.temperature': 0.9 } })

    // 草稿里是 0.9，服务端返回 0.3 —— store 与 UI 一律以服务端为准。
    expect(store.effectiveByKey.get('ai.temperature')?.value).toBe(0.3)
    expect(store.restartPending.pending).toEqual(['ai.temperature'])
    expect(store.isDirty).toBe(false)
  })
})
