/** 系统分区外壳（§30）：PageHeader、四个 Tab、RouterView 与重启横幅。 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SystemLayout from '../SystemLayout.vue'
import { fail, flushAll, installFetch, makeRouter, ok, useFreshPinia, type MockRequest } from './helpers'

function child(test: string) {
  return { template: `<div data-test="${test}" />` }
}

function routes() {
  return [
    { path: '/system/settings', name: 'system-settings', component: child('child-settings') },
    { path: '/system/credentials', name: 'system-credentials', component: child('child-credentials') },
    { path: '/system/settings/advanced', name: 'system-advanced', component: child('child-advanced') },
    { path: '/system/settings/restart-pending', name: 'system-restart-pending', component: child('child-restart') },
  ]
}

async function mountLayout(pending: string[]): Promise<VueWrapper> {
  const pinia = useFreshPinia()
  installFetch((request: MockRequest) =>
    request.url.includes('/config/restart-pending')
      ? ok({ pending, since: pending.length ? 1700000000 : null })
      : fail(404, 'not_found', 'unexpected'),
  )
  const router = await makeRouter('/system/settings', routes())
  const wrapper = mount(SystemLayout, { global: { plugins: [pinia, router] } })
  await flushAll()
  return wrapper
}

describe('SystemLayout', () => {
  it('渲染系统标题、四个 Tab 与当前子路由', async () => {
    const wrapper = await mountLayout([])

    expect(wrapper.get('h1').text()).toBe('系统')
    const tabs = wrapper.findAll('.cb-system__tab')
    expect(tabs.map((tab) => tab.text())).toEqual(['设置', '凭据', '高级 YAML', '等待重启'])
    expect(wrapper.find('[data-test="child-settings"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="system-tab-system-settings"]').attributes('aria-current')).toBe('page')
    expect(wrapper.find('[data-test="restart-banner"]').exists()).toBe(false)
  })

  it('有等待重启项时在内容顶部渲染横幅，点击 Tab 切换子路由', async () => {
    const wrapper = await mountLayout(['onebot.port'])

    expect(wrapper.get('[data-test="restart-banner"]').text()).toContain('1 项设置将在重启后生效')

    await wrapper.get('[data-test="system-tab-system-credentials"]').trigger('click')
    await flushAll()

    expect(wrapper.find('[data-test="child-credentials"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="system-tab-system-credentials"]').attributes('aria-current')).toBe('page')
  })
})
