/** 记忆健康度（W5 §31）：真实数字渲染、未知一律 —、未启用提示、刷新。 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import MemoryHealth from '@/pages/memory/MemoryHealth.vue'
import type { MemoryHealth as MemoryHealthModel } from '@/types/domain'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'

const originalFetch = globalThis.fetch

afterEach(() => {
  globalThis.fetch = originalFetch
})

function healthCalls(calls: MockRequest[]): MockRequest[] {
  return calls.filter(
    (call) => new URL(call.url, 'http://localhost').pathname === '/api/v1/memories/health',
  )
}

async function mountHealth(
  health: Partial<MemoryHealthModel> | null,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const calls = installFetch((request): MockReply => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/memories/health') {
      if (health === null) return fail(500, 'internal.error', '健康度读取失败')
      return ok(health)
    }
    return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
  })
  const pinia = useFreshPinia()
  const wrapper = mount(MemoryHealth, { global: { plugins: [pinia] } })
  await flushAll()
  return { wrapper, calls }
}

describe('MemoryHealth', () => {
  it('renders real numbers, coverage percent and status labels', async () => {
    const { wrapper } = await mountHealth({
      enabled: true,
      total: 12,
      active: 10,
      archived: 2,
      embedded: 8,
      embedding_coverage: 0.667,
      semantic_enabled: true,
      retrieval: { semantic_available: true, top_k: 8 },
      database: { connected: true },
    })

    expect(wrapper.get('[data-test="health-total"]').text()).toContain('12')
    expect(wrapper.get('[data-test="health-active"]').text()).toContain('10')
    expect(wrapper.get('[data-test="health-archived"]').text()).toContain('2')
    expect(wrapper.get('[data-test="health-embedded"]').text()).toContain('8')
    expect(wrapper.get('[data-test="health-coverage"]').text()).toContain('67%')
    expect(wrapper.get('[data-test="health-semantic"]').text()).toContain('已启用')
    expect(wrapper.get('[data-test="health-retrieval"]').text()).toContain('语义 + 关键词')
    expect(wrapper.get('[data-test="health-database"]').text()).toContain('已连接')
  })

  it('shows — for every unknown metric instead of estimating', async () => {
    const { wrapper } = await mountHealth({ enabled: true })
    const grid = wrapper.get('[data-test="health-grid"]').text()
    expect((grid.match(/—/g) ?? []).length).toBeGreaterThanOrEqual(8)
  })

  it('derives the semantic label from retrieval when semantic_enabled is missing', async () => {
    const { wrapper } = await mountHealth({
      enabled: true,
      retrieval: { semantic_available: false },
    })
    expect(wrapper.get('[data-test="health-semantic"]').text()).toContain('未启用')
    expect(wrapper.get('[data-test="health-retrieval"]').text()).toContain('仅关键词')
  })

  it('shows 记忆功能未启用 when the backend disables memory', async () => {
    const { wrapper } = await mountHealth({ enabled: false })
    expect(wrapper.text()).toContain('记忆功能未启用')
    expect(wrapper.find('[data-test="health-grid"]').exists()).toBe(false)
  })

  it('refreshes on demand and surfaces load errors', async () => {
    const { wrapper, calls } = await mountHealth(null)
    expect(wrapper.text()).toContain('健康度读取失败')

    await wrapper.get('[data-test="health-refresh"]').trigger('click')
    await flushAll()
    expect(healthCalls(calls)).toHaveLength(2)
  })
})
