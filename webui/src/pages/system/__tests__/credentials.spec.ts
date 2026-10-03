/** 凭据页（§58-§59）：列表脱敏、更换、删除 409 → 强制删除、ai 域测试。 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { CredentialItem } from '@/api/config'
import { useToast } from '@/composables/toast'
import Credentials from '../Credentials.vue'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ITEMS: CredentialItem[] = [
  { domain: 'ai', ref: 'OPENAI_API_KEY', masked: 'sk-********abcd', configured: true, source: 'dotenv' },
  { domain: 'tool', ref: 'weather_api_key', masked: '', configured: false, source: 'unset' },
]

const PROVIDERS = [
  {
    name: 'openai',
    type: 'openai',
    base_url: 'https://api.openai.com/v1',
    api_key_env: 'OPENAI_API_KEY',
    has_key: true,
    models: ['gpt-4o-mini'],
    restart_required: false,
  },
]

function handler(request: MockRequest): MockReply {
  const url = new URL(request.url, 'http://localhost')
  const path = url.pathname
  if (path === '/api/v1/credentials' && request.method === 'GET') {
    return ok({ items: ITEMS, domains: ['ai', 'embedding', 'onebot', 'web', 'tool'] })
  }
  if (path === '/api/v1/ai/providers') {
    return ok({ items: PROVIDERS })
  }
  if (path === '/api/v1/credentials/ai/OPENAI_API_KEY' && request.method === 'PUT') {
    return ok({ saved: true, masked: 'sk-********wxyz' })
  }
  if (path === '/api/v1/credentials/ai/OPENAI_API_KEY' && request.method === 'DELETE') {
    if (url.searchParams.get('force') === '1') return ok({ deleted: true })
    return fail(
      409,
      'credential.in_use',
      '环境变量 OPENAI_API_KEY 仍被 Provider 使用：openai；确认不再使用后带 force=1 删除',
    )
  }
  if (path === '/api/v1/credentials/test' && request.method === 'POST') {
    return ok({
      ok: true,
      model: 'gpt-4o-mini',
      requested_model: 'gpt-4o-mini',
      provider: 'openai',
      latency_ms: 412,
      http_status: 200,
      http_status_source: 'upstream',
      error_type: '',
      message: '',
      reply: 'pong',
    })
  }
  return fail(404, 'not_found', `未模拟 ${request.method} ${path}`)
}

async function mountPage(): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(handler)
  const wrapper = mount(Credentials, { global: { plugins: [pinia] } })
  await flushAll()
  return { wrapper, calls }
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
})

describe('Credentials 页', () => {
  it('列表只显示脱敏值、状态与来源，Provider 通过 api_key_env 映射', async () => {
    const { wrapper } = await mountPage()

    const rows = wrapper.findAll('[data-test="credential-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('openai')
    expect(rows[0].text()).toContain('OPENAI_API_KEY')
    expect(rows[0].get('[data-test="credential-masked"]').text()).toBe('sk-********abcd')
    expect(rows[0].get('[data-test="credential-status"]').text()).toContain('已配置')
    expect(rows[0].get('[data-test="credential-source"]').text()).toContain('.env（dotenv）')
    expect(rows[1].get('[data-test="credential-status"]').text()).toContain('未配置')
    expect(wrapper.find('[data-test="credential-test"]').exists()).toBe(true)
  })

  it('更换凭据调用 PUT，提交后明文不留在页面', async () => {
    const { wrapper, calls } = await mountPage()

    await wrapper.get('[data-test="credential-replace"]').trigger('click')
    const input = wrapper.get('[data-test="credential-edit-input"] input')
    expect(input.attributes('type')).toBe('password')
    await input.setValue('sk-live-secret-value')

    await wrapper.get('[data-test="credential-save"]').trigger('click')
    await flushAll()

    const put = calls.find((call) => call.method === 'PUT')
    expect(put?.url).toBe('/api/v1/credentials/ai/OPENAI_API_KEY')
    expect(put?.body).toEqual({ value: 'sk-live-secret-value' })
    expect(wrapper.text()).not.toContain('sk-live-secret-value')
    expect(useToast().items.value.some((item) => item.message.includes('凭据已更新'))).toBe(true)
  })

  it('删除遇到 409 credential.in_use 时提示后端 message 并支持强制删除', async () => {
    const { wrapper, calls } = await mountPage()

    await wrapper.get('[data-test="credential-delete"]').trigger('click')
    await flushAll()
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    expect(wrapper.text()).toContain('环境变量 OPENAI_API_KEY 仍被 Provider 使用')
    expect(wrapper.text()).toContain('强制删除')

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const deletes = calls.filter((call) => call.method === 'DELETE')
    expect(deletes).toHaveLength(2)
    expect(deletes[0].url).toBe('/api/v1/credentials/ai/OPENAI_API_KEY')
    expect(deletes[1].url).toContain('force=1')
    expect(useToast().items.value.some((item) => item.message.includes('已强制删除'))).toBe(true)
  })

  it('ai 域 [测试] 用映射后的 Provider 调用并展示 http_status/latency/message', async () => {
    const { wrapper, calls } = await mountPage()

    await wrapper.get('[data-test="credential-test"]').trigger('click')
    await flushAll()

    const post = calls.find((call) => call.method === 'POST' && call.url.includes('/credentials/test'))
    expect(post?.body).toEqual({ provider: 'openai' })

    const result = wrapper.get('[data-test="credential-test-result"]')
    expect(result.text()).toContain('测试成功')
    expect(result.text()).toContain('200')
    expect(result.text()).toContain('412 ms')
    expect(result.text()).toContain('pong')
  })
})
