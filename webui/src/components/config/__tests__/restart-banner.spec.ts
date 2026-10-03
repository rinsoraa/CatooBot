/** 重启横幅（§52、§90）：有 pending 才渲染；只有已登录会话才发 GET。 */

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Pinia } from 'pinia'
import { defineComponent } from 'vue'

import { useAuthStore } from '@/stores/auth'
import RestartBanner from '../RestartBanner.vue'
import { fail, flushAll, installFetch, ok, useFreshPinia } from './helpers'

const RouterLinkStub = defineComponent({
  name: 'RouterLinkStub',
  props: { to: { type: [String, Object], default: '' } },
  template: '<a class="router-link-stub"><slot /></a>',
})

function mountBanner(pinia: Pinia): ReturnType<typeof mount> {
  return mount(RestartBanner, {
    global: { plugins: [pinia], stubs: { RouterLink: RouterLinkStub } },
  })
}

describe('RestartBanner', () => {
  it('匿名（未登录）时不发请求，也不渲染', async () => {
    const pinia = useFreshPinia()
    const calls = installFetch(() => ok({ pending: [], since: null }))

    const wrapper = mountBanner(pinia)
    await flushAll()

    expect(wrapper.find('[data-test="restart-banner"]').exists()).toBe(false)
    expect(calls.filter((call) => call.url.includes('/config/restart-pending'))).toHaveLength(0)
  })

  it('已登录但没有 pending 时不渲染，仍然做一次 GET', async () => {
    const pinia = useFreshPinia()
    useAuthStore().$patch({ status: 'authenticated' })
    const calls = installFetch(() => ok({ pending: [], since: null }))

    const wrapper = mountBanner(pinia)
    await flushAll()

    expect(wrapper.find('[data-test="restart-banner"]').exists()).toBe(false)
    expect(calls.filter((call) => call.url.includes('/config/restart-pending'))).toHaveLength(1)
  })

  it('有 pending 时显示数量与查看链接', async () => {
    const pinia = useFreshPinia()
    useAuthStore().$patch({ status: 'authenticated' })
    installFetch((request) =>
      request.url.includes('/config/restart-pending')
        ? ok({ pending: ['onebot.port', 'ai.timeout'], since: 1700000000 })
        : fail(404, 'not_found', 'unexpected'),
    )

    const wrapper = mountBanner(pinia)
    await flushAll()

    const banner = wrapper.get('[data-test="restart-banner"]')
    expect(banner.text()).toContain('2 项设置将在重启后生效')
    expect(banner.text()).toContain('onebot.port')
    expect(wrapper.get('[data-test="restart-banner-link"]').text()).toContain('查看')
  })
})
