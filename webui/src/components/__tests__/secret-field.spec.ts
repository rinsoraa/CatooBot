/**
 * SecretField（§85 密钥 UX）：掩码只出现在 placeholder、显示/隐藏切换、
 * 已保存的明文永不进入 value / 事件。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SecretField from '@/components/ai/SecretField.vue'

const MASKED = 'sk-********abcd'

function mountField(props: Record<string, unknown> = {}) {
  return mount(SecretField, {
    props: { modelValue: '', label: 'API Key', ...props },
  })
}

describe('SecretField', () => {
  it('renders a password input bound to its label and never the stored key', () => {
    const wrapper = mountField({ masked: MASKED, configured: true, modelValue: '' })
    const input = wrapper.get('[data-test="secret-input"]')
    expect(input.attributes('type')).toBe('password')
    expect(input.attributes('placeholder')).toBe(MASKED)
    // 已保存的 Key 只做 placeholder，绝不出现在 value 里
    expect((input.element as HTMLInputElement).value).toBe('')
    expect(wrapper.html()).not.toContain('value="sk-')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.get('label').attributes('for')).toBe(input.attributes('id'))
  })

  it('emits exactly what the user typed and nothing else', async () => {
    const wrapper = mountField({ masked: MASKED })
    const input = wrapper.get('[data-test="secret-input"]')
    await input.setValue('sk-new-secret')
    expect(wrapper.emitted('update:modelValue')).toEqual([['sk-new-secret']])
  })

  it('toggles visibility with an accessible 显示/隐藏 button without emitting', async () => {
    const wrapper = mountField()
    const toggle = wrapper.get('[data-test="secret-toggle"]')
    expect(toggle.attributes('aria-label')).toBe('显示')
    expect(toggle.attributes('aria-pressed')).toBe('false')

    await toggle.trigger('click')
    expect(wrapper.get('[data-test="secret-input"]').attributes('type')).toBe('text')
    expect(wrapper.get('[data-test="secret-toggle"]').attributes('aria-label')).toBe('隐藏')
    expect(wrapper.get('[data-test="secret-toggle"]').attributes('aria-pressed')).toBe('true')

    await wrapper.get('[data-test="secret-toggle"]').trigger('click')
    expect(wrapper.get('[data-test="secret-input"]').attributes('type')).toBe('password')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('honours disabled and the parent placeholder override', () => {
    const wrapper = mountField({ disabled: true, placeholder: '请输入新的 Key', masked: MASKED })
    const input = wrapper.get('[data-test="secret-input"]')
    expect(input.attributes('disabled')).toBeDefined()
    expect(input.attributes('placeholder')).toBe('请输入新的 Key')
    expect(wrapper.get('[data-test="secret-toggle"]').attributes('disabled')).toBeDefined()
  })

  it('renders hint and the error slot next to the field with aria wiring', () => {
    const wrapper = mount(SecretField, {
      props: { modelValue: '', label: 'API Key', hint: '只写入服务端 .env' },
      slots: { error: 'Key 不能为空' },
    })
    const input = wrapper.get('[data-test="secret-input"]')
    expect(wrapper.text()).toContain('只写入服务端 .env')
    expect(wrapper.get('[role="alert"]').text()).toBe('Key 不能为空')
    expect(input.attributes('aria-invalid')).toBe('true')
    const describedBy = input.attributes('aria-describedby') ?? ''
    expect(describedBy.split(' ')).toHaveLength(2)
  })
})
