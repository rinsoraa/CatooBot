/** 设置搜索（§85-§86）：防抖、中文 label / 内部 key 命中、键盘选择、空态。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConfigSearch from '../ConfigSearch.vue'
import { makeField, wait } from './helpers'

const FIELDS = [
  makeField({
    key: 'bot.name',
    label: '机器人名称',
    description: '机器人名称（bot.name）：保存后立即生效。',
    area: '系统',
    level: 'basic',
    usage_status: 'ACTIVE',
  }),
  makeField({
    key: 'ai.max_tokens',
    label: '最大输出 tokens',
    description: '最大输出 tokens（ai.max_tokens）：保存后立即生效。',
    area: 'AI与模型',
    level: 'advanced',
    usage_status: 'ACTIVE_WITH_RESTART',
  }),
  makeField({
    key: 'memory.semantic.batch_size',
    label: '批量大小',
    description: '批量大小（memory.semantic.batch_size）：仅启动时读取。',
    area: '记忆',
    level: 'expert',
    usage_status: 'DEFINED_BUT_UNUSED',
  }),
]

describe('ConfigSearch', () => {
  it('输入中文 label 时 200ms 防抖后命中，并展示分类/级别/状态', async () => {
    const wrapper = mount(ConfigSearch, { props: { fields: FIELDS } })
    const input = wrapper.get('[data-test="config-search-input"]')
    expect(input.attributes('aria-label')).toBe('搜索设置')

    await input.setValue('名称')
    expect(wrapper.find('[data-test="config-search-result"]').exists()).toBe(false)

    await wait(250)
    const results = wrapper.findAll('[data-test="config-search-result"]')
    expect(results).toHaveLength(1)
    const text = results[0].text()
    expect(text).toContain('机器人名称')
    expect(text).toContain('系统')
    expect(text).toContain('基础')
    expect(text).toContain('运行期使用')
  })

  it('按内部 key 搜索也能命中', async () => {
    const wrapper = mount(ConfigSearch, { props: { fields: FIELDS } })
    await wrapper.get('[data-test="config-search-input"]').setValue('max_tokens')
    await wait(250)

    const results = wrapper.findAll('[data-test="config-search-result"]')
    expect(results).toHaveLength(1)
    expect(results[0].text()).toContain('ai.max_tokens')
    expect(results[0].text()).toContain('高级')
  })

  it('键盘 ↑/↓ + Enter 选择并 emit key', async () => {
    const wrapper = mount(ConfigSearch, { props: { fields: FIELDS } })
    const input = wrapper.get('[data-test="config-search-input"]')
    await input.setValue('大小')
    await wait(250)

    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('select')).toEqual([['memory.semantic.batch_size']])
    expect(wrapper.find('[data-test="config-search-results"]').exists()).toBe(false)
  })

  it('没有匹配时显示空态', async () => {
    const wrapper = mount(ConfigSearch, { props: { fields: FIELDS } })
    await wrapper.get('[data-test="config-search-input"]').setValue('不存在的配置项')
    await wait(250)

    expect(wrapper.get('[data-test="config-search-empty"]').text()).toContain('没有匹配的设置')
  })

  it('点击结果行也会 emit select', async () => {
    const wrapper = mount(ConfigSearch, { props: { fields: FIELDS } })
    await wrapper.get('[data-test="config-search-input"]').setValue('机器人')
    await wait(250)

    await wrapper.get('[data-test="config-search-result"]').trigger('mousedown')
    expect(wrapper.emitted('select')).toEqual([['bot.name']])
  })
})
