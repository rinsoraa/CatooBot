/**
 * 会话页（W5 §25）：活动会话卡字段、无正文声明与空态。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import SocialSessions from '@/pages/social/SocialSessions.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeRouter,
  makeSession,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social/sessions', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
]

async function mountSessions(
  reply: (request: MockRequest) => MockReply,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter('/social/sessions', ROUTES)
  const wrapper = mount(SocialSessions, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

afterEach(() => {
  restoreFetch()
})

describe('SocialSessions 页', () => {
  it('有活动会话时渲染 person / space / 起止时间 / 回合数 / 打断状态，并声明不含聊天正文', async () => {
    const { wrapper, calls } = await mountSessions((request) =>
      request.path === '/api/v1/social/sessions'
        ? ok({ active: true, items: [makeSession({ interrupted: true })] })
        : fail(404, 'not_found', '未模拟'),
    )

    expect(calls.filter((call) => call.path === '/api/v1/social/sessions').length).toBe(1)
    expect(wrapper.get('[data-test="sessions-statement"]').text()).toContain('不包含聊天正文')

    const card = wrapper.get('[data-test="session-card"]')
    expect(card.get('[data-test="session-person"]').text()).toBe('小艾')
    expect(card.get('[data-test="session-space"]').text()).toBe('space-1')
    expect(card.get('[data-test="session-started"]').text()).not.toBe('—')
    expect(card.get('[data-test="session-last-activity"]').text()).not.toBe('—')
    expect(card.get('[data-test="session-turns"]').text()).toBe('6')
    expect(card.get('[data-test="session-interrupted"]').text()).toBe('已打断')
  })

  it('没有活动会话时显示空态，且声明仍然可见', async () => {
    const { wrapper } = await mountSessions((request) =>
      request.path === '/api/v1/social/sessions'
        ? ok({ active: false, items: [] })
        : fail(404, 'not_found', '未模拟'),
    )

    expect(wrapper.get('.cb-empty__title').text()).toBe('当前没有活动会话')
    expect(wrapper.find('[data-test="session-card"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="sessions-statement"]').text()).toContain('不包含聊天正文')
  })
})
