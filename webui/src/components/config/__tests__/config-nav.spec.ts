/** 配置类导航（§38-§49 的可用性补丁）：按区域分列、点击跳转、抽奖展示项数。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import ConfigNav from '../ConfigNav.vue'

const SECTIONS = [
  { area: '角色', section: 'behavior.initiative', label: '主动聊天', count: 11 },
  {
    area: '角色',
    section: 'behavior.initiative.core_friend',
    label: '主动聊天（核心好友）',
    count: 10,
  },
  { area: '系统', section: 'bot', label: '机器人', count: 3 },
]

function anchor(section: string): HTMLElement {
  const element = document.createElement('div')
  element.id = `section-${section.replace(/[.[\]<>]/g, '-')}`
  element.scrollIntoView = vi.fn()
  document.body.appendChild(element)
  return element
}

describe('ConfigNav', () => {
  it('按区域分列并在 chip 上显示项数', () => {
    const wrapper = mount(ConfigNav, { props: { sections: SECTIONS } })
    const areas = wrapper.findAll('.cb-config-nav__area').map((node) => node.text())
    expect(areas).toEqual(['角色', '系统'])
    expect(wrapper.findAll('[data-test^="config-nav-"]')).toHaveLength(3)
    expect(wrapper.get('[data-test="config-nav-behavior.initiative"]').text()).toContain('主动聊天')
    expect(wrapper.get('[data-test="config-nav-behavior.initiative"]').text()).toContain('11')
  })

  it('点击 chip 滚动到对应配置类的锚点并发出 jump', async () => {
    const target = anchor('behavior.initiative')
    const wrapper = mount(ConfigNav, { props: { sections: SECTIONS } })

    await wrapper.get('[data-test="config-nav-behavior.initiative"]').trigger('click')

    expect(target.scrollIntoView).toHaveBeenCalled()
    expect(wrapper.emitted('jump')?.[0]).toEqual(['behavior.initiative'])
    expect(
      wrapper
        .get('[data-test="config-nav-behavior.initiative"]')
        .classes()
        .some((name) => name.includes('chip--active')),
    ).toBe(true)
    target.remove()
  })

  it('没有可见配置类时不渲染导航', () => {
    const wrapper = mount(ConfigNav, { props: { sections: [] } })
    expect(wrapper.find('[data-test="config-nav"]').exists()).toBe(false)
  })
})
