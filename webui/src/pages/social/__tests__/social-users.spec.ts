/**
 * 用户页（W5 §18）：搜索防抖、URL 分页、空态与错误重试。
 * 真实 social store + `globalThis.fetch` 信封 mock。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import type { Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useToast } from '@/composables/toast'
import SocialUsers from '@/pages/social/SocialUsers.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeRouter,
  makeUser,
  ok,
  restoreFetch,
  useFreshPinia,
  wait,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
]

const USERS = [
  makeUser(),
  makeUser({ person_id: 'p-2', display_name: '阿蓝', qq: '10002' }),
]

function handler(total = USERS.length): (request: MockRequest) => MockReply {
  return (request) => {
    if (request.path === '/api/v1/social/users' && request.method === 'GET') {
      const q = request.query.get('q') ?? ''
      const items = q ? USERS.filter((user) => user.display_name.includes(q)) : USERS
      return ok({ items, total: q ? items.length : total, limit: 20, offset: 0, next_cursor: null })
    }
    return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
  }
}

interface MountedUsers {
  wrapper: VueWrapper
  calls: MockRequest[]
  pinia: Pinia
}

async function mountUsers(
  initial = '/social',
  reply: (request: MockRequest) => MockReply = handler(),
): Promise<MountedUsers> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialUsers, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flushAll()
  return { wrapper, calls, pinia }
}

enableAutoUnmount(afterEach)

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  restoreFetch()
})

describe('SocialUsers 页', () => {
  it('首屏按 URL 加载并渲染 PersonCard 网格（显示名，而不是内部 id）', async () => {
    const { wrapper, calls } = await mountUsers()

    const userCalls = calls.filter((call) => call.path === '/api/v1/social/users')
    expect(userCalls.length).toBe(1)
    expect(userCalls[0]?.method).toBe('GET')
    expect(userCalls[0]?.query.get('limit')).toBe('20')
    expect(userCalls[0]?.query.get('offset')).toBe(null)

    const cards = wrapper.findAll('[data-test="person-card"]')
    expect(cards.length).toBe(2)
    expect(wrapper.get('[data-test="person-name"]').text()).toBe('小艾')
    expect(wrapper.get('[data-test="person-name"]').text()).not.toBe('p-1')
  })

  it('搜索输入 300ms 防抖：只发一次新请求，且 q 写入 URL', async () => {
    const { wrapper, calls } = await mountUsers()
    expect(calls.filter((call) => call.path === '/api/v1/social/users').length).toBe(1)

    await wrapper.get('[data-test="users-search"]').setValue('阿蓝')
    expect(calls.filter((call) => call.path === '/api/v1/social/users').length).toBe(1)

    await wait(400)
    await flushAll()

    const userCalls = calls.filter((call) => call.path === '/api/v1/social/users')
    expect(userCalls.length).toBe(2)
    expect(userCalls[1]?.query.get('q')).toBe('阿蓝')
    expect(wrapper.findAll('[data-test="person-card"]').length).toBe(1)
  })

  it('分页 offset 走 URL：从 ?offset=20 点击下一页发出 offset=40', async () => {
    const { wrapper, calls } = await mountUsers('/social?offset=20&limit=20', handler(100))

    const first = calls.find((call) => call.path === '/api/v1/social/users')
    expect(first?.query.get('offset')).toBe('20')
    expect(first?.query.get('limit')).toBe('20')

    await wrapper.get('[data-test="users-next"]').trigger('click')
    await flushAll()

    const userCalls = calls.filter((call) => call.path === '/api/v1/social/users')
    expect(userCalls.at(-1)?.query.get('offset')).toBe('40')
    expect(userCalls.at(-1)?.query.get('limit')).toBe('20')
  })

  it('空列表显示「还没有认识的人」', async () => {
    const { wrapper } = await mountUsers('/social', (request) =>
      request.path === '/api/v1/social/users'
        ? ok({ items: [], total: 0, limit: 20, offset: 0 })
        : fail(404, 'not_found', '未模拟'),
    )

    expect(wrapper.get('.cb-empty__title').text()).toBe('还没有认识的人')
    expect(wrapper.findAll('[data-test="person-card"]').length).toBe(0)
  })

  it('加载失败显示错误态并可重试', async () => {
    let failing = true
    const { wrapper, calls } = await mountUsers('/social', (request) => {
      if (request.path !== '/api/v1/social/users') return fail(404, 'not_found', '未模拟')
      if (failing) {
        failing = false
        return fail(500, 'internal.error', '读取用户失败')
      }
      return ok({ items: USERS, total: USERS.length, limit: 20, offset: 0 })
    })

    expect(wrapper.get('[role="alert"]').text()).toContain('读取用户失败')

    await wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()
    expect(wrapper.findAll('[data-test="person-card"]').length).toBe(2)
    expect(calls.filter((call) => call.path === '/api/v1/social/users').length).toBe(2)
  })
})
