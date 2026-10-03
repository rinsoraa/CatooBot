/** 重启横幅（§52、§90）：有 pending 才渲染，挂载时只发一次 GET。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'

import RestartBanner from '../RestartBanner.vue'
import { fail, flushAll, installFetch, ok, useFreshPinia } from './helpers'

const RouterLinkStub = defineComponent({
  name: 'RouterLinkStub',
  props: { to: { type: [String, Object], default: '' } },
  template: '<a class="router-link-stub"><slot /></a>',
})

describe('RestartBanner', () => {
  it('没有 pending 时不渲染任何内容，但仍然做一次 GET', async () => {
    const pinia = useFreshPinia()
    const calls = installFetch(() => ok({ pending: [], since: null }))

    const wrapper = mount(RestartBanner, {
      global: { plugins: [pinia], stubs: { RouterLink: RouterLinkStub } },
    })
    await flushAll()

    expect(wrapper.find('[data-test="restart-banner"]').exists()).toBe(false)
    expect(calls.filter((call) => call.url.includes('/config/restart-pending'))).toHaveLength(1)
  })

  it('有 pending 时显示数量与查看链接', async () => {
    const pinia = useFreshPinia()
    installFetch((request) =>
      request.url.includes('/config/restart-pending')
        ? ok({ pending: ['onebot.port', 'ai.timeout'], since: 1700000000 })
        : fail(404, 'not_found', 'unexpected'),
    )

    const wrapper = mount(RestartBanner, {
      global: { plugins: [pinia], stubs: { RouterLink: RouterLinkStub } },
    })
    await flushAll()

    const banner = wrapper.get('[data-test="restart-banner"]')
    expect(banner.text()).toContain('2 项设置将在重启后生效')
    expect(banner.text()).toContain('onebot.port')
    expect(wrapper.get('[data-test="restart-banner-link"]').text()).toContain('查看')
  })
})
