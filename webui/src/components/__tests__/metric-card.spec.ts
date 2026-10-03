/** MetricCard（§31、§32）：空值语义与加载骨架。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MetricCard from '@/components/MetricCard.vue'

describe('MetricCard', () => {
  it('renders label, formatted value, hint and state attribute', () => {
    const wrapper = mount(MetricCard, {
      props: { label: '记忆条数', value: 128, hint: '累计写入', state: 'ok' },
    })
    expect(wrapper.get('.cb-metric__label').text()).toBe('记忆条数')
    expect(wrapper.get('.cb-metric__value').text()).toBe('128')
    expect(wrapper.get('.cb-metric__hint').text()).toBe('累计写入')
    expect(wrapper.get('.cb-metric').attributes('data-state')).toBe('ok')
  })

  it('keeps string values intact', () => {
    const wrapper = mount(MetricCard, { props: { label: '成功率', value: '99.5%' } })
    expect(wrapper.get('.cb-metric__value').text()).toBe('99.5%')
  })

  it('shows an em dash for null or missing values instead of a fake number', () => {
    const withNull = mount(MetricCard, { props: { label: '目标', value: null } })
    expect(withNull.get('.cb-metric__value').text()).toBe('—')
    expect(withNull.get('.cb-metric__value').attributes('data-empty')).toBe('true')

    const withoutValue = mount(MetricCard, { props: { label: '目标' } })
    expect(withoutValue.get('.cb-metric__value').text()).toBe('—')
  })

  it('renders a skeleton and aria-busy while loading', () => {
    const wrapper = mount(MetricCard, { props: { label: '记忆条数', value: 128, loading: true } })
    expect(wrapper.get('.cb-metric').attributes('aria-busy')).toBe('true')
    expect(wrapper.find('.cb-metric__bar').exists()).toBe(true)
    expect(wrapper.find('.cb-metric__value').exists()).toBe(false)
  })

  it('renders the actions and footer slots', () => {
    const wrapper = mount(MetricCard, {
      props: { label: '记忆条数', value: 1 },
      slots: { actions: '<button type="button">刷新</button>', footer: '<span>更新于 12:00</span>' },
    })
    expect(wrapper.get('.cb-metric__actions').text()).toContain('刷新')
    expect(wrapper.get('.cb-metric__footer').text()).toContain('更新于 12:00')
  })
})
