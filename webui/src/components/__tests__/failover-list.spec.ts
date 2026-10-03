/** FailoverList（W4 §31-§34、§104）：顺序渲染 / 上移下移 / 保存 emit / 第 1 项标记。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import FailoverList from '@/components/ai/FailoverList.vue'
import type { ModelItem } from '@/types/ai'

function model(name: string, overrides: Partial<ModelItem> = {}): ModelItem {
  return {
    name,
    provider: `provider-${name}`,
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
    ...overrides,
  }
}

const ITEMS = [
  model('fast'),
  model('smart', { order: 1, in_cooldown: true }),
  model('vision', { order: 2, enabled: false }),
]
const ORDER = ['fast', 'smart', 'vision']

function mountList(order: string[] = ORDER) {
  return mount(FailoverList, { props: { items: ITEMS, order } })
}

describe('FailoverList', () => {
  it('renders the chain in order with provider, status badge and the first-item marker', () => {
    const wrapper = mountList()
    const rows = wrapper.findAll('[data-test="fb-row"]')
    expect(rows).toHaveLength(3)
    expect(rows.map((row) => row.get('[data-test="fb-name"]').text())).toEqual([
      'fast',
      'smart',
      'vision',
    ])
    expect(rows[0]?.text()).toContain('provider-fast')
    expect(rows[0]?.text()).toContain('已启用')
    expect(rows[1]?.text()).toContain('冷却中')
    expect(rows[2]?.text()).toContain('已停用')

    expect(rows[0]?.find('[data-test="fb-chat"]').text()).toBe('当前聊天模型')
    expect(rows[1]?.find('[data-test="fb-chat"]').exists()).toBe(false)
  })

  it('emits update:order on 上移/下移 and disables the boundary buttons', async () => {
    const wrapper = mountList()
    const rows = wrapper.findAll('[data-test="fb-row"]')

    expect(rows[0]?.get('[data-test="fb-up"]').attributes('disabled')).toBeDefined()
    expect(rows[2]?.get('[data-test="fb-down"]').attributes('disabled')).toBeDefined()
    expect(rows[0]?.get('[data-test="fb-down"]').attributes('aria-label')).toBe('下移 fast')

    await rows[0]?.get('[data-test="fb-down"]').trigger('click')
    expect(wrapper.emitted('update:order')?.[0]).toEqual([['smart', 'fast', 'vision']])

    await rows[2]?.get('[data-test="fb-up"]').trigger('click')
    expect(wrapper.emitted('update:order')?.[1]).toEqual([['fast', 'vision', 'smart']])
  })

  it('marks the local change as 未保存 and emits save with the current order', async () => {
    const wrapper = mountList()
    expect(wrapper.find('[data-test="fb-dirty"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="fb-save"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="fb-row"] [data-test="fb-down"]').trigger('click')

    // v-model:order is owned by the parent: feed the emitted order back in.
    const emitted = wrapper.emitted('update:order')?.[0]?.[0]
    expect(emitted).toEqual(['smart', 'fast', 'vision'])
    await wrapper.setProps({ order: emitted as string[] })

    expect(wrapper.get('[data-test="fb-dirty"]').text()).toBe('未保存')
    const save = wrapper.get('[data-test="fb-save"]')
    expect(save.attributes('disabled')).toBeUndefined()
    await save.trigger('click')
    expect(wrapper.emitted('save')?.[0]).toEqual([['smart', 'fast', 'vision']])
  })

  it('flags a local order that differs from the saved models as 未保存', () => {
    const wrapper = mountList(['vision', 'fast', 'smart'])
    expect(wrapper.find('[data-test="fb-dirty"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="fb-chat"]').exists()).toBe(true)
  })
})
