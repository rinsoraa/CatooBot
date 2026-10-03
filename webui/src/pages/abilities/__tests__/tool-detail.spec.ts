/** 工具详情（W5 §35-§37）：schema/设置/指标；测试必须确认后才 POST，且 body 带 confirm。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import ToolDetail from '@/pages/abilities/ToolDetail.vue'
import { fail, installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { makeRouter } from '@/pages/system/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { ToolDetail as ToolDetailData } from '@/types/domain'
import type { RouteRecordRaw } from 'vue-router'

const DETAIL: ToolDetailData = {
  name: 'weather',
  display_name: '天气',
  description: '查询实时天气',
  category: '生活',
  risk_level: 'low',
  enabled: true,
  requires_credentials: true,
  has_credential: false,
  timeout: 10,
  cache_ttl_seconds: 300,
  calls: 3,
  failures: 1,
  last_used_at: 1700000000,
  input_schema: { type: 'object', properties: { city: { type: 'string' } } },
  output_schema: { type: 'object' },
  when_to_use: '用户询问天气时',
  when_not_to_use: '历史天气',
  limitations: '只支持当前天气',
  settings: { provider: 'open_meteo' },
  metrics: { calls: 3, success: 2, failure: 1 },
  recent_executions: [
    { created_at: 1700000000, status: 'ok', cache_hit: 0, duration_ms: 120, error_type: '', result_summary: '晴' },
  ],
  permissions_summary: {
    count: 1,
    rules: [{ scope: 'user', ref: '10001', tool_name: 'weather', allowed: false }],
  },
}

function routes(): RouteRecordRaw[] {
  return [{ path: '/abilities/tools/:name', name: 'abilities-tool', component: { template: '<div />' } }]
}

let requests: MockRequest[] = []
let pinia: Pinia

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(name = 'weather', handler?: (request: MockRequest) => MockReply): Promise<VueWrapper> {
  requests = installFetch(
    handler ??
      ((request) => {
        if (request.url.includes('/api/v1/tools/weather/test') && request.method === 'POST') {
          return ok({
            ok: true,
            result: { temperature: 21 },
            error: null,
            duration_ms: 12,
            may_have_called_external: true,
            note: '测试结果不会发送到 QQ',
          })
        }
        if (request.url.includes('/api/v1/tools/weather') && request.method === 'PATCH') {
          return ok({ ...DETAIL, timeout: 20, cache_ttl_seconds: 120, applied: ['timeout', 'cache_ttl_seconds'], restart_required: false })
        }
        if (request.url.includes(`/api/v1/tools/${name}`)) return ok(DETAIL)
        return fail(404, 'tools.unknown', `unmocked ${request.url}`)
      }),
  )
  const router = await makeRouter(`/abilities/tools/${name}`, routes())
  const wrapper = mount(ToolDetail, { global: { plugins: [pinia, router] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
})

describe('ToolDetail', () => {
  it('renders docs, schemas, metrics and recent executions', async () => {
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('用户询问天气时')
    expect(wrapper.text()).toContain('历史天气')
    expect(wrapper.get('[data-test="tool-input-schema"]').text()).toContain('"city"')
    expect(wrapper.get('[data-test="tool-permissions"]').text()).toContain('10001')
    expect(wrapper.get('[data-test="tool-metrics"]').text()).toContain('success')
    expect(wrapper.get('[data-test="execution-row"]').text()).toContain('ok')
  })

  it('saves timeout and cache TTL through toolsApi.update', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="tool-timeout"]').setValue('20')
    await wrapper.get('[data-test="tool-cache-ttl"]').setValue('120')
    await wrapper.get('[data-test="tool-settings-form"]').trigger('submit')
    await settle()

    const patch = requests.find((request) => request.method === 'PATCH')
    expect(patch?.url).toContain('/api/v1/tools/weather')
    expect(patch?.body).toEqual({ timeout: 20, cache_ttl_seconds: 120 })
  })

  it('only posts /{name}/test after the confirm dialog, with confirm in the body', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="tool-test"]').trigger('click')
    await settle()

    expect(wrapper.get('[role="dialog"]').text()).toContain('不会发送 QQ 消息')
    expect(wrapper.get('[role="dialog"]').text()).toContain('真的发起一次外部请求')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const test = requests.find((request) => request.method === 'POST')
    expect(test?.url).toContain('/api/v1/tools/weather/test')
    expect(test?.body).toEqual({ arguments: {}, confirm: 'weather' })
    expect(wrapper.get('[data-test="tool-test-external"]').text()).toContain('外部服务')
    expect(wrapper.get('[data-test="tool-test-duration"]').text()).toContain('12')
  })

  it('renders an ErrorState when the tool is unknown (404)', async () => {
    const wrapper = await mountPage('ghost', () => fail(404, 'tools.unknown', '工具不存在：ghost'))

    expect(wrapper.text()).toContain('工具不存在或已被移除：ghost')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
})
