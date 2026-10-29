/** StatusBadge（§31、§37）：文字标签永远渲染，颜色不是唯一信号。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusBadge from '@/components/StatusBadge.vue'
import type { StatusState } from '@/components/StatusBadge.vue'

const STATES: StatusState[] = ['ok', 'warn', 'error', 'off', 'idle']

describe('StatusBadge', () => {
  it('renders the exact text label for every state', () => {
    for (const state of STATES) {
      const label = `状态-${state}`
      const wrapper = mount(StatusBadge, { props: { state, label } })
      expect(wrapper.text()).toContain(label)
      expect(wrapper.get('.cb-status').attributes('data-state')).toBe(state)
    }
  })

  it('is role=status with a decorative dot, so text stays the primary signal', () => {
    const wrapper = mount(StatusBadge, { props: { state: 'error', label: '连接失败' } })
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.text()).toContain('连接失败')
    expect(wrapper.get('.cb-status__dot').attributes('aria-hidden')).toBe('true')
  })

  it('adds the pulse animation class only when pulse is set', () => {
    const plain = mount(StatusBadge, { props: { state: 'ok', label: '在线' } })
    expect(plain.find('.cb-status--pulse').exists()).toBe(false)

    const pulsing = mount(StatusBadge, { props: { state: 'ok', label: '在线', pulse: true } })
    expect(pulsing.find('.cb-status--pulse').exists()).toBe(true)
  })
})
