/**
 * 关系页（W5 §22）：只读表格与指标展示；页面上没有任何写入控件。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import SocialRelationships from '@/pages/social/SocialRelationships.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeRelationship,
  makeRouter,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social/relationships', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
]

async function mountRelationships(
  reply: (request: MockRequest) => MockReply,
  initial = '/social/relationships',
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialRelationships, {
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

describe('SocialRelationships 页', () => {
  it('渲染人物 / 类型 / 四指标 / 互动次数 / 最近互动，并标注只读且没有写控件', async () => {
    const { wrapper, calls } = await mountRelationships((request) =>
      request.path === '/api/v1/social/relationships'
        ? ok({ items: [makeRelationship()], count: 1 })
        : fail(404, 'not_found', '未模拟'),
    )

    const call = calls.find((item) => item.path === '/api/v1/social/relationships')
    expect(call?.method).toBe('GET')
    expect(call?.query.get('limit')).toBe('50')

    expect(wrapper.get('[data-test="relationship-readonly-note"]').text()).toContain('只读')
    expect(wrapper.get('[data-test="relationship-row-type"]').text()).toBe('朋友')
    expect(
      wrapper.findAll('[data-test="relationship-row-value"]').map((node) => node.text()),
    ).toEqual(['0.72', '0.50', '0.40', '0.60'])
    expect(wrapper.get('[data-test="relationship-row-interactions"]').text()).toBe('18')
    expect(wrapper.get('[data-test="relationship-row-last"]').text()).not.toBe('—')

    // §22：只读页面上不允许出现任何输入或提交控件。
    expect(wrapper.findAll('button').length).toBe(0)
    expect(wrapper.findAll('input').length).toBe(0)
    expect(wrapper.findAll('textarea').length).toBe(0)
  })

  it('没有关系记录时显示空态', async () => {
    const { wrapper } = await mountRelationships((request) =>
      request.path === '/api/v1/social/relationships'
        ? ok({ items: [], count: 0 })
        : fail(404, 'not_found', '未模拟'),
    )

    expect(wrapper.get('.cb-empty__title').text()).toBe('还没有关系记录')
  })
})
