/**
 * 承诺页（W5 §23）：状态过滤映射真实枚举值、人物过滤与空态。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import SocialCommitments from '@/pages/social/SocialCommitments.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeCommitment,
  makeRouter,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social/commitments', component: { template: '<div />' } },
  { path: '/social/commitments/:commitmentId', component: { template: '<div />' } },
]

const ROW = makeCommitment({ status: 'broken', summary: '答应帮对方搬东西' })

async function mountCommitments(
  reply: (request: MockRequest) => MockReply,
  initial = '/social/commitments',
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialCommitments, {
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

describe('SocialCommitments 页', () => {
  it('?status=broken&person=p-9 原样出参，并渲染中文状态卡', async () => {
    const { wrapper, calls } = await mountCommitments(
      (request) =>
        request.path === '/api/v1/social/commitments'
          ? ok({ items: [ROW], count: 1 })
          : fail(404, 'not_found', '未模拟'),
      '/social/commitments?status=broken&person=p-9',
    )

    const call = calls.find((item) => item.path === '/api/v1/social/commitments')
    expect(call?.query.get('status')).toBe('broken')
    expect(call?.query.get('person')).toBe('p-9')
    expect(wrapper.get('[data-test="commitment-status"]').text()).toBe('未履行')
  })

  it('点击状态过滤把真实枚举值写入请求（已履行 → completed），全部则移除该参数', async () => {
    const { wrapper, calls } = await mountCommitments((request) =>
      request.path === '/api/v1/social/commitments'
        ? ok({ items: [ROW], count: 1 })
        : fail(404, 'not_found', '未模拟'),
    )

    await wrapper.get('[data-test="commitment-filter-completed"]').trigger('click')
    await flushAll()
    let last = calls.filter((item) => item.path === '/api/v1/social/commitments').at(-1)
    expect(last?.query.get('status')).toBe('completed')

    await wrapper.get('[data-test="commitment-filter-all"]').trigger('click')
    await flushAll()
    last = calls.filter((item) => item.path === '/api/v1/social/commitments').at(-1)
    expect(last?.query.get('status')).toBe(null)
  })

  it('没有承诺记录时显示空态「当前没有承诺记录」', async () => {
    const { wrapper } = await mountCommitments((request) =>
      request.path === '/api/v1/social/commitments'
        ? ok({ items: [], count: 0 })
        : fail(404, 'not_found', '未模拟'),
      '/social/commitments?status=cancelled',
    )

    expect(wrapper.get('.cb-empty__title').text()).toBe('当前没有承诺记录')
    expect(wrapper.text()).toContain('清除筛选')
  })
})
