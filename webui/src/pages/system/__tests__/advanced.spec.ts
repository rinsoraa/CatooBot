/** 高级 YAML 页（§54-§57、§72、§99-§100）：确认前不发 PUT、失败展示后端 message。 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useToast } from '@/composables/toast'
import Advanced from '../Advanced.vue'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const RAW_YAML = 'bot:\n  name: CatooBot\n'
const NEW_YAML = 'bot:\n  name: Renamed\n'

function makeHandler(state: { put: MockReply }) {
  return (request: MockRequest): MockReply => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/config/raw' && request.method === 'GET') {
      return ok({ yaml: RAW_YAML, path: '/srv/catoobot/config/overrides.yaml' })
    }
    if (url.pathname === '/api/v1/config/raw' && request.method === 'PUT') {
      return state.put
    }
    return fail(404, 'not_found', `未模拟 ${request.method} ${url.pathname}`)
  }
}

async function mountPage(state: { put: MockReply }): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(makeHandler(state))
  const wrapper = mount(Advanced, { global: { plugins: [pinia] } })
  await flushAll()
  return { wrapper, calls }
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
})

describe('Advanced 页', () => {
  it('加载 YAML 原文；确认对话框通过前不发 PUT，确认后才发', async () => {
    const { wrapper, calls } = await mountPage({ put: ok({ saved: true, notes: [] }) })

    const textarea = wrapper.get('[data-test="advanced-editor"] textarea')
    expect((textarea.element as HTMLTextAreaElement).value).toBe(RAW_YAML)
    expect(wrapper.get('[data-test="advanced-warning"]').text()).toContain(
      '错误配置可能导致 Bot 无法启动',
    )
    expect(wrapper.get('[data-test="advanced-secrets-note"]').text()).toContain('不会显示任何 Secret')
    expect(wrapper.get('[data-test="advanced-secrets-note"]').text()).toContain('不会导出 API Key')

    await textarea.setValue(NEW_YAML)
    await wrapper.get('[data-test="advanced-save"]').trigger('click')
    await flushAll()

    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.get('.cb-dialog__message').text()).toContain('这会替换 overrides.yaml')
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(0)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const put = calls.find((call) => call.method === 'PUT')
    expect(put?.url).toBe('/api/v1/config/raw')
    expect(put?.body).toEqual({ yaml: NEW_YAML, confirm: 'raw' })
    expect(useToast().items.value.some((item) => item.message.includes('原始配置已保存'))).toBe(true)
  })

  it('后端 YAML 校验失败时原样展示错误信息', async () => {
    const { wrapper } = await mountPage({
      put: fail(422, 'config.invalid_value', '校验失败：mapping values are not allowed here'),
    })

    await wrapper.get('[data-test="advanced-editor"] textarea').setValue(NEW_YAML)
    await wrapper.get('[data-test="advanced-save"]').trigger('click')
    await flushAll()
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="advanced-error"]').text()).toContain(
      'mapping values are not allowed here',
    )
    expect(useToast().items.value.some((item) => item.message.includes('保存失败'))).toBe(true)
  })
})
