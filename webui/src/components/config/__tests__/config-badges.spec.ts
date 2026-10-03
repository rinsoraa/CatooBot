/** 来源徽标与生效状态徽标（§42-§46、§91-§92）：文案、提示与未保存标记。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ConfigSourceBadge from '../ConfigSourceBadge.vue'
import ConfigStatusBadge from '../ConfigStatusBadge.vue'
import { makeField } from './helpers'

describe('ConfigSourceBadge', () => {
  it('显示来源中文名，点击展开来源文件与内部键', async () => {
    const wrapper = mount(ConfigSourceBadge, {
      props: { source: 'overrides', fieldKey: 'bot.name' },
    })

    expect(wrapper.get('[data-test="config-source-badge"]').text()).toContain('WebUI 覆盖')
    expect(wrapper.find('[data-test="config-source-detail"]').exists()).toBe(false)

    await wrapper.get('[data-test="config-source-badge"]').trigger('click')
    const detail = wrapper.get('[data-test="config-source-detail"]').text()
    expect(detail).toContain('overrides.yaml')
    expect(detail).toContain('bot.name')
  })

  it('source 缺省时显示「默认值」', () => {
    const wrapper = mount(ConfigSourceBadge, { props: { fieldKey: 'x.y' } })
    expect(wrapper.text()).toContain('默认值')
  })

  it('env 来源指向 .env', async () => {
    const wrapper = mount(ConfigSourceBadge, { props: { source: 'env', fieldKey: 'ai.timeout' } })
    await wrapper.get('[data-test="config-source-badge"]').trigger('click')
    expect(wrapper.get('[data-test="config-source-detail"]').text()).toContain('.env')
  })
})

describe('ConfigStatusBadge', () => {
  it('热重载且未修改：✓ 已生效', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ hot_reload: true, restart_required: false }) },
    })
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('已生效')
    expect(wrapper.attributes('data-status')).toBe('effective')
  })

  it('restart_required：⚠ 重启后生效', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: {
        field: makeField({
          hot_reload: false,
          restart_required: true,
          usage_status: 'ACTIVE_WITH_RESTART',
        }),
      },
    })
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('重启后生效')
  })

  it('DEFINED_BUT_UNUSED：⚠ 当前未使用 + Runtime 提示', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ usage_status: 'DEFINED_BUT_UNUSED' }) },
    })
    const badge = wrapper.get('[data-test="config-status-badge"]')
    expect(badge.text()).toContain('当前未使用')
    expect(badge.attributes('title')).toContain('该配置项当前没有被 Runtime 使用，修改它不会改变 Bot 行为')
  })

  it('LEGACY 同样显示⚠ 当前未使用', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ usage_status: 'LEGACY' }) },
    })
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('当前未使用')
  })

  it('CONDITIONALLY_USED：ⓘ 条件生效', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ usage_status: 'CONDITIONALLY_USED' }) },
    })
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('条件生效')
  })

  it('dirty 时附加 ● 未保存 标记', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: {
        field: makeField({ hot_reload: false, restart_required: true }),
        dirty: true,
      },
    })
    expect(wrapper.get('[data-test="config-status-dirty"]').text()).toContain('未保存')
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('重启后生效')
  })

  it('热重载字段被修改时显示 ● 未保存（尚未生效）', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ hot_reload: true }), dirty: true },
    })
    expect(wrapper.attributes('data-status')).toBe('unsaved')
    expect(wrapper.text()).toContain('未保存')
  })

  it('pendingRestart 时即便 hot_reload 也显示重启后生效', () => {
    const wrapper = mount(ConfigStatusBadge, {
      props: { field: makeField({ hot_reload: true }), pendingRestart: true },
    })
    expect(wrapper.get('[data-test="config-status-badge"]').text()).toContain('重启后生效')
  })
})
