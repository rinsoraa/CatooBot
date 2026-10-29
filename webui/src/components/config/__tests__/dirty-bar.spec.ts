/** 未保存提示条（§48、§135）：计数、三个动作、保存中状态。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DirtyBar from '../DirtyBar.vue'

describe('DirtyBar', () => {
  it('显示未保存数量与脏键，并分别 emit discard / preview / apply', async () => {
    const wrapper = mount(DirtyBar, {
      props: { count: 2, dirtyKeys: ['bot.name', 'web.port'] },
    })

    expect(wrapper.get('[data-test="dirty-count"]').text()).toContain('有 2 项未保存修改')
    expect(wrapper.text()).toContain('bot.name')

    await wrapper.get('[data-test="dirty-discard"]').trigger('click')
    await wrapper.get('[data-test="dirty-preview"]').trigger('click')
    await wrapper.get('[data-test="dirty-apply"]').trigger('click')

    expect(wrapper.emitted('discard')).toHaveLength(1)
    expect(wrapper.emitted('preview')).toHaveLength(1)
    expect(wrapper.emitted('apply')).toHaveLength(1)
  })

  it('applying 时禁用按钮并显示「保存中…」', async () => {
    const wrapper = mount(DirtyBar, {
      props: { count: 1, dirtyKeys: ['bot.name'], applying: true },
    })

    const apply = wrapper.get('[data-test="dirty-apply"]')
    expect(apply.text()).toBe('保存中…')
    expect(apply.attributes('disabled')).toBeDefined()
    await apply.trigger('click')
    expect(wrapper.emitted('apply')).toBeUndefined()
  })
})
