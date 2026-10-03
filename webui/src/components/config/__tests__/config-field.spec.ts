/** ConfigField 动态渲染器（§47/§86/§93）：每个 type 的控件与约束展示。 */

import { mount } from '@vue/test-utils'
import { NInputNumber, NSelect, NSwitch } from 'naive-ui'
import { describe, expect, it } from 'vitest'

import ConfigField from '../ConfigField.vue'
import { makeField } from './helpers'

describe('ConfigField', () => {
  it('bool 渲染 Switch 并回传翻转后的值', async () => {
    const field = makeField({ key: 'bot.debug', label: '调试模式', type: 'bool', default: false })
    const wrapper = mount(ConfigField, { props: { field, value: false } })

    const toggle = wrapper.get('[data-test="field-bool-switch"]')
    expect(toggle.attributes('aria-checked')).toBe('false')
    await toggle.trigger('click')

    expect(wrapper.emitted('update:value')?.[0]).toEqual([true])
  })

  it('int 渲染 InputNumber 并显示 min ~ max 约束文本', () => {
    const field = makeField({
      key: 'web.port',
      label: '端口',
      type: 'int',
      constraints: { min: 1, max: 65535 },
    })
    const wrapper = mount(ConfigField, { props: { field, value: 8500 } })

    expect(wrapper.findComponent(NInputNumber).exists()).toBe(true)
    expect(wrapper.get('[data-test="field-constraints"]').text()).toContain('1 ~ 65535')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('8500')
  })

  it('number 的回车由 NInputNumber 事件回传', () => {
    const field = makeField({ key: 'ai.timeout', label: '超时', type: 'int', constraints: {} })
    const wrapper = mount(ConfigField, { props: { field, value: 30 } })

    wrapper.findComponent(NInputNumber).vm.$emit('update:value', 45)
    expect(wrapper.emitted('update:value')?.[0]).toEqual([45])
  })

  it('str 渲染文本输入并回传输入内容', async () => {
    const field = makeField({ key: 'bot.name', label: '名称', type: 'str' })
    const wrapper = mount(ConfigField, { props: { field, value: 'CatooBot' } })

    const input = wrapper.get('[data-test="field-text"] input')
    expect((input.element as HTMLInputElement).value).toBe('CatooBot')
    await input.setValue('新名字')

    expect(wrapper.emitted('update:value')).toEqual([['新名字']])
    expect(wrapper.text()).toContain(field.description)
  })

  it('带 choices 的 str 渲染 Select', () => {
    const field = makeField({
      key: 'logging.level',
      label: '日志级别',
      type: 'str',
      choices: ['DEBUG', 'INFO'],
    })
    const wrapper = mount(ConfigField, { props: { field, value: 'INFO' } })

    expect(wrapper.findComponent(NSelect).exists()).toBe(true)
    wrapper.findComponent(NSelect).vm.$emit('update:value', 'DEBUG')
    expect(wrapper.emitted('update:value')?.[0]).toEqual(['DEBUG'])
  })

  it('list 支持增删行并回传字符串数组', async () => {
    const field = makeField({
      key: 'permissions.superusers',
      label: '超级管理员',
      type: 'list',
      default: [],
    })
    const wrapper = mount(ConfigField, { props: { field, value: [] } })

    await wrapper.get('[data-test="field-list-add"]').trigger('click')
    expect(wrapper.findAll('[data-test="field-list-row"]')).toHaveLength(1)

    await wrapper.get('[data-test="field-list-row"] input').setValue('10001')
    expect(wrapper.emitted('update:value')?.at(-1)).toEqual([['10001']])

    await wrapper.get('[data-test="field-list-remove"]').trigger('click')
    expect(wrapper.findAll('[data-test="field-list-row"]')).toHaveLength(0)
    expect(wrapper.emitted('update:value')?.at(-1)).toEqual([[]])
  })

  it('dict 渲染 JSON 文本域：合法 JSON 回传对象，非法 JSON 显示校验提示', async () => {
    const field = makeField({
      key: 'sandbox.social_space_map',
      label: '社交空间映射',
      type: 'dict',
      default: {},
    })
    const wrapper = mount(ConfigField, { props: { field, value: {} } })

    const textarea = wrapper.get('[data-test="field-dict-input"] textarea')
    await textarea.setValue('{"客厅": "living"}')
    expect(wrapper.emitted('update:value')?.at(-1)).toEqual([{ 客厅: 'living' }])

    await textarea.setValue('{oops')
    expect(wrapper.get('[data-test="field-dict-error"]').text()).toContain('JSON 格式无效')
  })

  it('sensitive 渲染禁用掩码输入、显示/隐藏按钮与修改提示，且不回传值', async () => {
    const field = makeField({ key: 'web.password', label: '登录密码', type: 'str', sensitive: true })
    const wrapper = mount(ConfigField, { props: { field, value: '********' } })

    const input = wrapper.get('[data-test="field-sensitive-input"] input')
    expect(input.attributes('disabled')).toBeDefined()
    expect(input.attributes('type')).toBe('password')
    expect(wrapper.get('[data-test="field-sensitive-note"]').text()).toContain(
      '敏感项请在凭据页或 Provider 编辑中修改',
    )

    await wrapper.get('[data-test="field-sensitive-toggle"]').trigger('click')
    expect(wrapper.get('[data-test="field-sensitive-input"] input').attributes('type')).toBe('text')
    expect(wrapper.emitted('update:value')).toBeUndefined()
  })

  it('disabled 时渲染 disabledReason', () => {
    const field = makeField({ key: 'ai.providers.<n>.api_key_env', label: 'Key 变量名', type: 'str' })
    const wrapper = mount(ConfigField, {
      props: { field, value: 'OPENAI_API_KEY', disabled: true, disabledReason: '只能在 Provider 编辑页修改' },
    })

    expect(wrapper.get('input').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="field-disabled-reason"]').text()).toContain('只能在 Provider 编辑页修改')
  })

  it('bool 用 NSwitch 组件实例时也有 aria-label', () => {
    const field = makeField({ key: 'bot.debug', label: '调试模式', type: 'bool' })
    const wrapper = mount(ConfigField, { props: { field, value: true } })

    expect(wrapper.findComponent(NSwitch).exists()).toBe(true)
    expect(wrapper.get('[data-test="field-bool-switch"]').attributes('aria-label')).toBe('调试模式')
  })
})
