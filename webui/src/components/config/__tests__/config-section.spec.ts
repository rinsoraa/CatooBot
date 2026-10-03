/** 配置分组（§41）：标题、计数、badge 插槽与默认插槽。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConfigSection from '../ConfigSection.vue'
import { makeField } from './helpers'

describe('ConfigSection', () => {
  it('渲染标题、说明、计数与两个插槽', () => {
    const fields = [makeField(), makeField({ key: 'web.port' })]
    const wrapper = mount(ConfigSection, {
      props: { title: '系统', description: '基础运行设置', fields },
      slots: {
        default: '<p data-test="inner-field">字段</p>',
        badge: '<span data-test="section-badge">3 项未保存</span>',
      },
    })

    expect(wrapper.text()).toContain('系统')
    expect(wrapper.text()).toContain('基础运行设置')
    expect(wrapper.text()).toContain('共 2 项')
    expect(wrapper.get('[data-test="section-badge"]').text()).toBe('3 项未保存')
    expect(wrapper.get('[data-test="inner-field"]').text()).toBe('字段')
  })
})
