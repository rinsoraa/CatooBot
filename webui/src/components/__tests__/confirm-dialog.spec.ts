/** ConfirmDialog（§32、§37）：显示、Esc 取消、确认与焦点管理。 */
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import ConfirmDialog from '@/components/ConfirmDialog.vue'

function baseProps() {
  return { modelValue: true, title: '删除确认', message: '删除后不可恢复，确定继续吗？' }
}

describe('ConfirmDialog', () => {
  it('renders the dialog only when shown', () => {
    const hidden = mount(ConfirmDialog, {
      props: { modelValue: false, title: '删除确认', message: '确定吗？' },
    })
    expect(hidden.find('[role="dialog"]').exists()).toBe(false)

    const wrapper = mount(ConfirmDialog, { props: baseProps() })
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(wrapper.text()).toContain('删除确认')
    expect(wrapper.text()).toContain('删除后不可恢复，确定继续吗？')
    expect(wrapper.get('[data-test="confirm"]').text()).toBe('确认')
    expect(wrapper.get('[data-test="cancel"]').text()).toBe('取消')
  })

  it('accepts v-model:show as the visibility source', () => {
    const wrapper = mount(ConfirmDialog, {
      props: { show: true, title: '危险操作', message: '确定执行吗？', danger: true },
    })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="confirm"]').classes()).toContain('cb-dialog__button--danger')
  })

  it('treats Escape as cancel and emits the close update', async () => {
    const wrapper = mount(ConfirmDialog, { props: baseProps(), attachTo: document.body })
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
    expect(wrapper.emitted('update:show')).toEqual([[false]])
    wrapper.unmount()
  })

  it('emits confirm when the confirm button is clicked', async () => {
    const wrapper = mount(ConfirmDialog, {
      props: { ...baseProps(), confirmText: '删除' },
    })
    await wrapper.get('[data-test="confirm"]').trigger('click')
    expect(wrapper.get('[data-test="confirm"]').text()).toBe('删除')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })

  it('moves focus into the dialog on open and restores it on close', async () => {
    const opener = document.createElement('button')
    opener.textContent = '打开确认'
    document.body.appendChild(opener)
    opener.focus()

    const wrapper = mount(ConfirmDialog, { props: baseProps(), attachTo: document.body })
    await nextTick()
    const panel = wrapper.get('[role="dialog"]').element
    expect(panel.contains(document.activeElement)).toBe(true)

    await wrapper.setProps({ modelValue: false })
    await nextTick()
    expect(document.activeElement).toBe(opener)

    wrapper.unmount()
    opener.remove()
  })
})
