/**
 * 承诺详情（W5 §24）：五段链条、缺失降级、可推导的排查结论与 404。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import SocialCommitmentDetail from '@/pages/social/SocialCommitmentDetail.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeCommitmentDetail,
  makeRouter,
  makeSpace,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social/commitments', component: { template: '<div />' } },
  { path: '/social/commitments/:commitmentId', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
]

async function mountDetail(
  reply: (request: MockRequest) => MockReply,
  initial = '/social/commitments/c-1',
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialCommitmentDetail, {
    global: { plugins: [pinia, router] },
    attachTo: document.body,
  })
  await flushAll()
  return { wrapper, calls }
}

function withSpaces(reply: (request: MockRequest) => MockReply) {
  return (request: MockRequest): MockReply => {
    if (request.path === '/api/v1/social/spaces') return ok({ items: [makeSpace()], map: {} })
    return reply(request)
  }
}

enableAutoUnmount(afterEach)

afterEach(() => {
  restoreFetch()
})

describe('SocialCommitmentDetail 页', () => {
  it('渲染 Person → Social Space → Goal → Action → Outcome 链条与本体', async () => {
    const { wrapper, calls } = await mountDetail(
      withSpaces((request) =>
        request.path === '/api/v1/social/commitments/c-1'
          ? ok(makeCommitmentDetail())
          : fail(404, 'not_found', '未模拟'),
      ),
    )

    expect(calls.some((call) => call.path === '/api/v1/social/commitments/c-1')).toBe(true)
    expect(wrapper.get('[data-test="detail-summary"]').text()).toBe('周日晚一起看电影')
    expect(wrapper.get('[data-test="detail-status"]').text()).toBe('进行中')

    expect(wrapper.get('[data-test="chain-person"]').text()).toContain('小艾')
    expect(wrapper.get('[data-test="chain-space-name"]').text()).toContain('私聊空间')
    expect(wrapper.get('[data-test="chain-goal-status"]').text()).toContain('进行中')
    expect(wrapper.get('[data-test="chain-action-name"]').text()).toContain('看电影')
    expect(wrapper.find('[data-test="chain-outcome-missing"]').exists()).toBe(true)

    // 结论只复述可推导字段。
    expect(wrapper.get('[data-test="commitment-diagnosis"]').text()).toContain('进行中')
  })

  it('broken + 已过截止：给出「已超过截止时间」的排查结论', async () => {
    const { wrapper } = await mountDetail(
      withSpaces((request) =>
        request.path === '/api/v1/social/commitments/c-1'
          ? ok(
              makeCommitmentDetail({
                status: 'broken',
                due_at: 1_700_000_000,
                outcome: {
                  status: 'broken',
                  resolved_at: 1_700_100_000,
                  updated_at: 1_700_100_000,
                  revision: 2,
                  result: '未完成',
                },
              }),
            )
          : fail(404, 'not_found', '未模拟'),
      ),
    )

    const diagnosis = wrapper.get('[data-test="commitment-diagnosis"]').text()
    expect(diagnosis).toContain('未履行')
    expect(diagnosis).toContain('已超过截止时间')
    expect(wrapper.get('[data-test="chain-outcome-status"]').text()).toContain('未履行')
  })

  it('goal / action / outcome 缺失时三块都降级为「未关联」', async () => {
    const { wrapper } = await mountDetail(
      withSpaces((request) =>
        request.path === '/api/v1/social/commitments/c-1'
          ? ok(makeCommitmentDetail({ goal: null, action: null, outcome: null, goal_id: '', goal_status: '' }))
          : fail(404, 'not_found', '未模拟'),
      ),
    )

    expect(wrapper.get('[data-test="chain-space-missing"]').text()).toBe('未关联')
    expect(wrapper.get('[data-test="chain-goal-missing"]').text()).toBe('未关联')
    expect(wrapper.get('[data-test="chain-action-missing"]').text()).toBe('未关联')
    expect(wrapper.get('[data-test="chain-outcome-missing"]').text()).toBe('未关联')
    expect(wrapper.get('[data-test="commitment-diagnosis"]').text()).toContain('还没有关联 Goal')
  })

  it('未知承诺 404 → 「找不到这条承诺」+ 返回链接', async () => {
    const { wrapper } = await mountDetail(
      (request) =>
        request.path === '/api/v1/social/spaces'
          ? ok({ items: [], map: {} })
          : fail(404, 'social.commitment_not_found', '承诺不存在：c-404'),
      '/social/commitments/c-404',
    )

    expect(wrapper.text()).toContain('找不到这条承诺')
    expect(wrapper.get('[data-test="commitment-back"]').attributes('href')).toBe('/social/commitments')
  })
})
