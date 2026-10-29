/**
 * 社交外壳（W5 §17）：唯一 h1 + 五个页签 + RouterView。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import SocialLayout from '@/pages/social/SocialLayout.vue'
import { flushAll, makeRouter, useFreshPinia } from './helpers'

const ROUTES = [
  { path: '/social', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/users/:personId', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/groups', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/relationships', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/commitments', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/commitments/:commitmentId', component: { template: '<div data-test="child-page" />' } },
  { path: '/social/sessions', component: { template: '<div data-test="child-page" />' } },
]

async function mountLayout(initial: string): Promise<{ wrapper: VueWrapper; router: Awaited<ReturnType<typeof makeRouter>> }> {
  const pinia = useFreshPinia()
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialLayout, { global: { plugins: [pinia, router] } })
  await flushAll()
  return { wrapper, router }
}

enableAutoUnmount(afterEach)

describe('SocialLayout', () => {
  it('只有一个 h1「社交」，五个页签 href 正确，子路由下「用户」页签保持激活', async () => {
    const { wrapper } = await mountLayout('/social/users/p-1')

    const headings = wrapper.findAll('h1')
    expect(headings.length).toBe(1)
    expect(headings[0]?.text()).toBe('社交')

    expect(wrapper.get('[data-test="social-tab-users"]').attributes('href')).toBe('/social')
    expect(wrapper.get('[data-test="social-tab-groups"]').attributes('href')).toBe('/social/groups')
    expect(wrapper.get('[data-test="social-tab-relationships"]').attributes('href')).toBe(
      '/social/relationships',
    )
    expect(wrapper.get('[data-test="social-tab-commitments"]').attributes('href')).toBe(
      '/social/commitments',
    )
    expect(wrapper.get('[data-test="social-tab-sessions"]').attributes('href')).toBe(
      '/social/sessions',
    )

    expect(wrapper.get('[data-test="social-tab-users"]').attributes('aria-current')).toBe('page')
    expect(wrapper.get('[data-test="social-tab-groups"]').attributes('aria-current')).toBeUndefined()
    expect(wrapper.find('[data-test="child-page"]').exists()).toBe(true)
  })

  it('承诺详情下只有「承诺」页签激活', async () => {
    const { wrapper } = await mountLayout('/social/commitments/c-1')

    expect(wrapper.get('[data-test="social-tab-commitments"]').attributes('aria-current')).toBe('page')
    expect(wrapper.get('[data-test="social-tab-users"]').attributes('aria-current')).toBeUndefined()
  })
})
