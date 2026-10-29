/** ToolCard（W5 §34-§35）：开关经服务端确认、restart 提示、null 凭据显示「—」。 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import ToolCard from '@/components/domain/ToolCard.vue'
import { installFetch, ok, useFreshPinia, type MockRequest } from '@/components/config/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { ToolRow } from '@/types/domain'

const tool = (overrides: Partial<ToolRow> = {}): ToolRow => ({
  name: 'weather',
  display_name: '天气',
  description: '查询实时天气',
  category: '生活',
  risk_level: 'low',
  enabled: true,
  requires_credentials: true,
  has_credential: null,
  timeout: 10,
  cache_ttl_seconds: 300,
  calls: 12,
  failures: 2,
  last_used_at: 1700000000,
  ...overrides,
})

let requests: MockRequest[] = []

function install(restartRequired = false, enabled = true): void {
  requests = installFetch((request) => {
    if (request.url.includes('/api/v1/tools/weather') && request.method === 'PATCH') {
      return ok({ ...tool({ enabled }), applied: ['enabled'], restart_required: restartRequired })
    }
    return { status: 404, payload: { ok: false, error: { code: 'x', message: 'unmocked' } } }
  })
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

beforeEach(() => {
  useFreshPinia()
  useToast().clear()
})

describe('ToolCard', () => {
  it('renders the tool fields and shows 「—」 for a null credential state', () => {
    install()
    const wrapper = mount(ToolCard, { props: { tool: tool() } })

    expect(wrapper.get('h3').text()).toBe('天气')
    expect(wrapper.text()).toContain('weather')
    expect(wrapper.text()).toContain('查询实时天气')
    expect(wrapper.text()).toContain('低风险')
    expect(wrapper.get('[data-test="tool-counts"]').text()).toBe('12 / 2')
    expect(wrapper.get('[data-test="tool-last-used"]').text()).toContain('2023-11-1')
    // has_credential === null → 不适用，显示「—」
    expect(wrapper.get('[data-test="tool-credential"]').text()).toBe('—')
  })

  it('patches enable state through the server and emits updated', async () => {
    install(false, false)
    const wrapper = mount(ToolCard, { props: { tool: tool({ enabled: true }) } })

    await wrapper.get('[data-test="tool-toggle-weather"]').trigger('click')
    await settle()

    const patch = requests.find((request) => request.method === 'PATCH')
    expect(patch?.url).toContain('/api/v1/tools/weather')
    expect(patch?.body).toEqual({ enabled: false })
    expect(wrapper.get('[data-test="tool-toggle-weather"]').attributes('aria-checked')).toBe('false')
    expect(wrapper.emitted('updated')).toHaveLength(1)
    expect(wrapper.find('[data-test="tool-restart-hint"]').exists()).toBe(false)
  })

  it('shows 「⚠ 重启后生效」 when the server answers restart_required', async () => {
    install(true, false)
    const wrapper = mount(ToolCard, { props: { tool: tool({ enabled: true }) } })

    await wrapper.get('[data-test="tool-toggle-weather"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-test="tool-restart-hint"]').text()).toContain('⚠ 重启后生效')
  })

  it('keeps the previous state and toasts when the patch fails', async () => {
    requests = installFetch((request) => {
      if (request.method === 'PATCH') {
        return { status: 500, payload: { ok: false, error: { code: 'tools.unknown', message: '工具不存在' } } }
      }
      return { status: 404, payload: { ok: false, error: { code: 'x', message: 'unmocked' } } }
    })
    const wrapper = mount(ToolCard, { props: { tool: tool({ enabled: true }) } })

    await wrapper.get('[data-test="tool-toggle-weather"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-test="tool-toggle-weather"]').attributes('aria-checked')).toBe('true')
    expect(useToast().items.value.at(-1)?.detail).toBe('工具不存在')
  })
})
