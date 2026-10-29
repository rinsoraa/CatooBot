/** 工具页（W5 §34-§38）：清单 / 策略摘要 / 权限增删 / 决策预览（只读）。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import Tools from '@/pages/abilities/Tools.vue'
import { installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { ToolRow } from '@/types/domain'

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
  {
    name: 'web_search',
    display_name: '搜索',
    description: '网页搜索',
    category: '信息',
    risk_level: 'medium',
    enabled: false,
    requires_credentials: true,
    has_credential: false,
    timeout: 15,
    cache_ttl_seconds: 60,
    calls: 2,
    failures: 0,
    last_used_at: 1700000000,
  },
]

const PERMISSION = { scope: 'user', ref: '10001', tool_name: 'weather', allowed: false, created_at: 1 }

let requests: MockRequest[] = []
let pinia: Pinia

function handler(request: MockRequest): MockReply {
  if (request.url.includes('/api/v1/tools/decision-debug')) {
    return ok({
      query: '查询天气',
      candidates: [{ name: 'weather', score: 0.9, enabled: true, risk_level: 'low' }],
      rejected: [{ name: 'web_search', reason: 'no relevant intent' }],
      selected: 'weather',
      decision_mode: 'candidates',
      instruction_preview: '可用工具：weather',
    })
  }
  if (request.url.includes('/api/v1/tools/permissions')) {
    if (request.method === 'PUT') return ok({ saved: true })
    if (request.method === 'DELETE') return ok({ cleared: true })
    return ok({ items: [PERMISSION], total: 1 })
  }
  if (request.url.includes('/api/v1/tools/cache/clear')) return ok({ cleared: 7, tool: null })
  if (request.url.endsWith('/api/v1/tools')) {
    return ok({
      items: TOOLS,
      policy: { allowed_risk_levels: ['low', 'medium'], rate_limit: { per_minute: 30 }, max_calls_per_turn: 3 },
      stats: { total: 2, enabled_count: 1, disabled_count: 1, calls: 7, failure: 1, cache_hits: 4, decision_mode: 'candidates' },
    })
  }
  return { status: 404, payload: { ok: false, error: { code: 'x', message: `unmocked ${request.url}` } } }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(Tools, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
  requests = installFetch(handler)
})

describe('Tools 页', () => {
  it('renders the tool list with policy and stats', async () => {
    const wrapper = await mountPage()

    expect(wrapper.findAll('[data-test^="tool-card-"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('天气')
    expect(wrapper.text()).toContain('搜索')
    expect(wrapper.get('[data-test="policy-risk"]').text()).toBe('low、medium')
    expect(wrapper.get('[data-test="policy-rate-limit"]').text()).toContain('per_minute')
    expect(wrapper.get('[data-test="tools-policy"]').text()).toContain('决策模式')
    expect(wrapper.get('[data-test="permissions-table"]').text()).toContain('10001')

    const list = requests.find((request) => request.url.endsWith('/api/v1/tools'))
    expect(list?.method).toBe('GET')
  })

  it('adds and deletes permission rules with a confirm for delete', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="permission-scope-id"]').setValue('20002')
    await wrapper.get('[data-test="permission-tool"]').setValue('web_search')
    await wrapper.get('[data-test="permission-form"]').trigger('submit')
    await settle()

    const put = requests.find((request) => request.method === 'PUT')
    expect(put?.url).toContain('/api/v1/tools/permissions')
    expect(put?.body).toEqual({ scope: 'user', scope_id: '20002', tool: 'web_search', allowed: true })

    await wrapper.get('[data-test="permission-delete"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(requests.some((request) => request.method === 'DELETE')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const del = requests.find((request) => request.method === 'DELETE')
    expect(del?.url).toContain('/api/v1/tools/permissions')
    expect(del?.url).toContain('scope=user')
    expect(del?.url).toContain('scope_id=10001')
    expect(del?.url).toContain('tool=weather')
  })

  it('previews tool selection read-only (GET only, never executes)', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="decision-input"]').setValue('查询天气')
    await wrapper.get('[data-test="decision-form"]').trigger('submit')
    await settle()

    const preview = requests.find((request) => request.url.includes('decision-debug'))
    expect(preview?.method).toBe('GET')
    expect(decodeURIComponent(preview?.url ?? '')).toContain('text=查询天气')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    expect(wrapper.get('[data-test="decision-selected"]').text()).toBe('weather')
    expect(wrapper.get('[data-test="decision-candidate"]').text()).toContain('weather')
    expect(wrapper.get('[data-test="tools-decision"]').text()).toContain('绝不执行工具')
  })

  it('requires a confirm before clearing the cache', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="tools-clear-cache"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const clear = requests.find((request) => request.method === 'POST')
    expect(clear?.url).toContain('/api/v1/tools/cache/clear')
    expect(clear?.body).toEqual({ confirm: 'clear', name: '' })
  })
})
