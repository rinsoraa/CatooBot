/**
 * 群组页（W5 §20）：群 ≠ 人物提示、参与开关的 PATCH 与「服务端确认后刷新」。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useToast } from '@/composables/toast'
import SocialGroups from '@/pages/social/SocialGroups.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeGroup,
  makeRouter,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [{ path: '/social/groups', component: { template: '<div />' } }]

interface Mounted {
  wrapper: VueWrapper
  calls: MockRequest[]
}

async function mountGroups(reply: (request: MockRequest) => MockReply): Promise<Mounted> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter('/social/groups', ROUTES)
  const wrapper = mount(SocialGroups, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushAll()
  return { wrapper, calls }
}

enableAutoUnmount(afterEach)

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  restoreFetch()
})

describe('SocialGroups 页', () => {
  it('渲染群号 / 名称 / 最近活动 / 互动次数，并提示「群 ≠ 人物」', async () => {
    const { wrapper } = await mountGroups((request) => {
      if (request.path === '/api/v1/social/groups' && request.method === 'GET') {
        return ok({ items: [makeGroup(), makeGroup({ group_id: 'g-2', name: '工作群', participation_enabled: false })], count: 2 })
      }
      return fail(404, 'not_found', '未模拟')
    })

    expect(wrapper.get('[data-test="group-warning"]').text()).toContain('群 ≠ 人物')
    expect(wrapper.get('[data-test="group-warning"]').text()).toContain('群号不是关系对象')

    const rows = wrapper.findAll('[data-test="group-row"]')
    expect(rows.length).toBe(2)
    expect(rows[0]?.text()).toContain('g-1')
    expect(rows[0]?.text()).toContain('老友群')
    expect(wrapper.findAll('[data-test="group-interactions"]').map((node) => node.text())).toEqual(['42', '42'])
    expect(wrapper.findAll('[data-test="group-last-seen"]')[0]?.text()).not.toBe('—')
    expect(wrapper.findAll('[data-test="group-toggle"]').map((node) => node.text())).toEqual(['已参与', '未参与'])
  })

  it('切换参与开关 PATCH 只带 {participation_enabled}，之后刷新群列表', async () => {
    let enabled = true
    const { wrapper, calls } = await mountGroups((request) => {
      if (request.path === '/api/v1/social/groups' && request.method === 'GET') {
        return ok({ items: [makeGroup({ participation_enabled: enabled })], count: 1 })
      }
      if (request.path === '/api/v1/social/groups/g-1' && request.method === 'PATCH') {
        const body = request.body as { participation_enabled: boolean }
        enabled = body.participation_enabled
        return ok({ group_id: 'g-1', participation_enabled: enabled })
      }
      return fail(404, 'not_found', '未模拟')
    })

    await wrapper.get('[data-test="group-toggle"]').trigger('click')
    await flushAll()

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.path).toBe('/api/v1/social/groups/g-1')
    expect(patch?.body).toEqual({ participation_enabled: false })
    expect(calls.filter((call) => call.path === '/api/v1/social/groups').length).toBe(2)
    expect(wrapper.get('[data-test="group-toggle"]').text()).toBe('未参与')

    const { items } = useToast()
    expect(items.value.some((item) => item.message.includes('已关闭'))).toBe(true)
  })

  it('没有群时显示空态', async () => {
    const { wrapper } = await mountGroups((request) =>
      request.path === '/api/v1/social/groups'
        ? ok({ items: [], count: 0 })
        : fail(404, 'not_found', '未模拟'),
    )

    expect(wrapper.get('.cb-empty__title').text()).toBe('还没有配置的群')
  })
})
