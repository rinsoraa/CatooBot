/** 记忆卡片（W5 §26/§29）：截断/展开、类型与 scope 展示、Expert provenance。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MemoryCard from '@/components/domain/MemoryCard.vue'
import type { MemoryRow } from '@/types/domain'

function makeMemory(overrides: Partial<MemoryRow> = {}): MemoryRow {
  return {
    memory_id: 7,
    content: '用户喜欢在深夜写代码。',
    summary: '深夜写代码',
    layer: 'semantic',
    category: 'preference',
    scope_key: 'user:10001',
    status: 'active',
    importance: 0.8,
    confidence: 0.7,
    created_at: 1700000000,
    updated_at: 1700000100,
    provenance: {},
    ...overrides,
  }
}

function expectedTime(seconds: number): string {
  const date = new Date(seconds * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

describe('MemoryCard', () => {
  it('renders time, type, scope and importance, and expands a truncated body', async () => {
    const wrapper = mount(MemoryCard, {
      props: { memory: makeMemory({ summary: '', content: '甲'.repeat(200) }) },
    })

    expect(wrapper.get('[data-test="memory-time"]').text()).toBe(expectedTime(1700000000))
    expect(wrapper.get('[data-test="memory-layer"]').text()).toBe('语义')
    expect(wrapper.get('[data-test="memory-category"]').text()).toBe('偏好')
    expect(wrapper.get('[data-test="memory-scope"]').text()).toBe('用户 10001')
    expect(wrapper.get('[data-test="memory-importance"]').text()).toContain('80%')

    const collapsed = wrapper.get('[data-test="memory-text"]').text()
    expect(collapsed.endsWith('…')).toBe(true)
    expect(collapsed.length).toBe(141)

    await wrapper.get('[data-test="memory-toggle"]').trigger('click')
    expect(wrapper.get('[data-test="memory-text"]').text()).toBe('甲'.repeat(200))
    expect(wrapper.get('[data-test="memory-toggle"]').text()).toBe('收起')

    await wrapper.get('[data-test="memory-toggle"]').trigger('click')
    expect(wrapper.get('[data-test="memory-text"]').text().endsWith('…')).toBe(true)
  })

  it('prefers the summary and shows no toggle for short text', () => {
    const wrapper = mount(MemoryCard, {
      props: { memory: makeMemory({ summary: '简短摘要', content: '很长的原始内容'.repeat(30) }) },
    })

    expect(wrapper.get('[data-test="memory-text"]').text()).toBe('简短摘要')
    expect(wrapper.find('[data-test="memory-toggle"]').exists()).toBe(false)
  })

  it('shows episode_key only in expert mode', () => {
    const memory = makeMemory({ provenance: { episode_key: 'ep-42' } })

    const plain = mount(MemoryCard, { props: { memory } })
    expect(plain.find('[data-test="memory-episode"]').exists()).toBe(false)

    const expert = mount(MemoryCard, { props: { memory, expert: true } })
    expect(expert.get('[data-test="memory-episode"]').text()).toContain('ep-42')
  })

  it('maps every scope kind and shows — for a missing time', () => {
    const cases: [string, string][] = [
      ['user:10001', '用户 10001'],
      ['group:456', '群 456'],
      ['character:catoo', '角色'],
      ['global:', '全局'],
    ]
    for (const [scopeKey, label] of cases) {
      const wrapper = mount(MemoryCard, {
        props: { memory: makeMemory({ scope_key: scopeKey, created_at: null }) },
      })
      expect(wrapper.get('[data-test="memory-scope"]').text()).toBe(label)
      expect(wrapper.get('[data-test="memory-time"]').text()).toBe('—')
      wrapper.unmount()
    }
  })
})
