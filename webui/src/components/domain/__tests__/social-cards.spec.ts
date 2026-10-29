/**
 * 社交域三张卡片（W5 §18/§22/§23）：字段、只读语义、Expert 标识与跳转。
 * 真实 vue-router + 真实数据形状，不 stub 子组件。
 */
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import CommitmentCard from '@/components/domain/CommitmentCard.vue'
import PersonCard from '@/components/domain/PersonCard.vue'
import RelationshipCard from '@/components/domain/RelationshipCard.vue'
import { flushAll, makeCommitment, makeRelationship, makeUser, makeRouter } from '@/pages/social/__tests__/helpers'

const ROUTES = [
  { path: '/social', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
  { path: '/social/commitments/:commitmentId', component: { template: '<div />' } },
]

enableAutoUnmount(afterEach)

describe('PersonCard', () => {
  it('渲染显示名 / QQ / 关系 / 互动次数 / 开放承诺 / 最近共享经历 / 最近互动时间，并链接到详情', async () => {
    const router = await makeRouter('/social', ROUTES)
    const wrapper = mount(PersonCard, {
      props: { user: makeUser() },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-test="person-name"]').text()).toBe('小艾')
    expect(wrapper.get('[data-test="person-qq"]').text()).toBe('10001')
    expect(wrapper.get('[data-test="person-relation"]').text()).toBe('朋友')
    expect(wrapper.get('[data-test="person-interactions"]').text()).toBe('18')
    expect(wrapper.get('[data-test="person-open-commitments"]').text()).toBe('2')
    expect(wrapper.get('[data-test="person-experience"]').text()).toBe('一起看了电影')
    expect(wrapper.get('[data-test="person-last-seen"]').text()).not.toBe('—')

    const link = wrapper.get('[data-test="person-name"]')
    expect(link.attributes('href')).toBe('/social/users/p-1')

    // 主展示是显示名，不是内部 id（id 只在 Expert 折叠区）。
    expect(wrapper.get('[data-test="person-experience"]').text()).not.toContain('p-1')
  })

  it('Expert 折叠区展示 person_id，点击卡片跳详情', async () => {
    const router = await makeRouter('/social', ROUTES)
    const wrapper = mount(PersonCard, {
      props: { user: makeUser({ person_id: 'person-abc' }) },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-test="person-expert-id"]').text()).toContain('person-abc')

    await wrapper.get('[data-test="person-card"]').trigger('click')
    await flushAll()
    expect(router.currentRoute.value.path).toBe('/social/users/person-abc')
  })
})

describe('RelationshipCard', () => {
  it('relationship 为 null 时显示「尚无关系记录」，不渲染指标', async () => {
    const wrapper = mount(RelationshipCard, { props: { relationship: null } })

    expect(wrapper.get('.cb-empty__title').text()).toBe('尚无关系记录')
    expect(wrapper.find('[data-test="relationship-metric-value"]').exists()).toBe(false)
  })

  it('渲染关系类型、四条 0-1 指标（带数值）、互动次数与最近互动，并标注只读', async () => {
    const wrapper = mount(RelationshipCard, { props: { relationship: makeRelationship() } })

    expect(wrapper.get('[data-test="relationship-type"]').text()).toBe('朋友')
    expect(wrapper.get('[data-test="relationship-interactions"]').text()).toBe('18')

    const values = wrapper.findAll('[data-test="relationship-metric-value"]').map((node) => node.text())
    expect(values).toEqual(['0.72', '0.50', '0.40', '0.60'])

    const bars = wrapper.findAll('[data-test="relationship-metric-bar"]')
    expect(bars.length).toBe(4)
    expect(bars[0]?.attributes('style')).toContain('width: 72%')

    expect(wrapper.get('[data-test="relationship-readonly"]').text()).toContain('只读')
  })
})

describe('CommitmentCard', () => {
  it('渲染人物、摘要、状态中文、强度/优先级、时间窗、创建时间与 Goal 状态', async () => {
    const router = await makeRouter('/social', ROUTES)
    const wrapper = mount(CommitmentCard, {
      props: { commitment: makeCommitment({ status: 'broken', goal_status: 'blocked' }) },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-test="commitment-person"]').text()).toContain('小艾')
    expect(wrapper.get('[data-test="commitment-summary"]').text()).toBe('周日晚一起看电影')
    expect(wrapper.get('[data-test="commitment-status"]').text()).toBe('未履行')
    expect(wrapper.get('[data-test="commitment-strength"]').text()).toBe('明确')
    expect(wrapper.get('[data-test="commitment-priority"]').text()).toBe('0.78')
    expect(wrapper.get('[data-test="commitment-time-hint"]').text()).toBe('周日晚上')
    expect(wrapper.get('[data-test="commitment-earliest"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="commitment-due"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="commitment-created"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="commitment-goal-status"]').text()).toBe('受阻')
  })

  it('真实 Core 的 completed 状态显示「已完成」，点击跳承诺详情', async () => {
    const router = await makeRouter('/social', ROUTES)
    const wrapper = mount(CommitmentCard, {
      props: { commitment: makeCommitment({ status: 'completed' }) },
      global: { plugins: [router] },
    })

    expect(wrapper.get('[data-test="commitment-status"]').text()).toBe('已完成')
    expect(wrapper.get('[data-test="commitment-summary"]').attributes('href')).toBe(
      '/social/commitments/c-1',
    )

    await wrapper.get('[data-test="commitment-card"]').trigger('click')
    await flushAll()
    expect(router.currentRoute.value.path).toBe('/social/commitments/c-1')
  })
})
