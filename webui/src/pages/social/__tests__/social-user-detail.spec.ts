/**
 * 人物详情（W5 §19/§21）：五区块、记忆未启用、404、以及「只提交改动字段」的保存。
 */
import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useToast } from '@/composables/toast'
import SocialUserDetail from '@/pages/social/SocialUserDetail.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeRouter,
  makeSpace,
  makeUserDetail,
  ok,
  restoreFetch,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const ROUTES = [
  { path: '/social', component: { template: '<div />' } },
  { path: '/social/users/:personId', component: { template: '<div />' } },
  { path: '/social/commitments/:commitmentId', component: { template: '<div />' } },
]

interface Mounted {
  wrapper: VueWrapper
  calls: MockRequest[]
}

function detailHandler(patchReply?: (request: MockRequest) => MockReply) {
  return (request: MockRequest): MockReply => {
    if (request.path === '/api/v1/social/spaces') {
      return ok({ items: [makeSpace()], map: {} })
    }
    if (request.path === '/api/v1/social/users' && request.method === 'GET') {
      return ok({ items: [], total: 0, limit: 20, offset: 0 })
    }
    if (request.path === '/api/v1/social/users/p-1' && request.method === 'GET') {
      return ok(makeUserDetail())
    }
    if (request.path === '/api/v1/social/users/p-1' && request.method === 'PATCH') {
      return patchReply ? patchReply(request) : ok({ person: makeUserDetail().person })
    }
    if (request.path === '/api/v1/social/users/p-404') {
      return fail(404, 'social.user_not_found', '用户不存在：p-404')
    }
    return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
  }
}

async function mountDetail(
  initial = '/social/users/p-1',
  reply: (request: MockRequest) => MockReply = detailHandler(),
): Promise<Mounted> {
  const pinia = useFreshPinia()
  const calls = installFetch(reply)
  const router = await makeRouter(initial, ROUTES)
  const wrapper = mount(SocialUserDetail, {
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

describe('SocialUserDetail 页', () => {
  it('渲染身份 / 关系 / 开放承诺 / 最近共享经历 / 相关记忆五个区块与社交空间维度说明', async () => {
    const { wrapper } = await mountDetail()
    const text = wrapper.text()

    expect(wrapper.get('[data-test="detail-display-name"]').text()).toBe('小艾')
    expect(text).toContain('关系')
    expect(text).toContain('开放承诺')
    expect(text).toContain('最近共享经历')
    expect(text).toContain('相关记忆')
    expect(text).toContain('社交空间')

    expect(wrapper.find('[data-test="block-relationship"] [data-test="relationship-card"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="block-commitments"] [data-test="commitment-card"]').length).toBe(1)
    expect(wrapper.get('[data-test="experience-item"]').text()).toContain('一起看了电影')
    expect(wrapper.get('[data-test="memory-item"]').text()).toContain('喜欢科幻片')
    expect(wrapper.get('[data-test="detail-space"]').text()).toContain('私聊空间')

    // §21：person 与 social_space 是两个独立维度
    expect(wrapper.get('[data-test="block-spaces"]').text()).toContain('两个独立维度')
  })

  it('memories 为 null 时显示「记忆未启用」', async () => {
    const { wrapper } = await mountDetail('/social/users/p-1', (request) => {
      if (request.path === '/api/v1/social/spaces') return ok({ items: [], map: {} })
      if (request.path === '/api/v1/social/users/p-1') return ok(makeUserDetail({ memories: null }))
      return fail(404, 'not_found', '未模拟')
    })

    expect(wrapper.get('[data-test="block-memories"]').text()).toContain('记忆未启用')
    expect(wrapper.find('[data-test="memory-item"]').exists()).toBe(false)
  })

  it('未知人物 404 → 「找不到这个人物」+ 返回链接', async () => {
    const { wrapper } = await mountDetail('/social/users/p-404', detailHandler())

    expect(wrapper.text()).toContain('找不到这个人物')
    expect(wrapper.get('[data-test="user-back"]').attributes('href')).toBe('/social')
    expect(wrapper.find('[data-test="user-edit-form"]').exists()).toBe(false)
  })

  it('保存只提交改动字段：只改备注 → PATCH body 仅 {notes}', async () => {
    const { wrapper, calls } = await mountDetail()

    await wrapper.get('[data-test="edit-notes"]').setValue('新的备注')
    await wrapper.get('[data-test="user-save"]').trigger('click')
    await flushAll()

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.path).toBe('/api/v1/social/users/p-1')
    expect(patch?.body).toEqual({ notes: '新的备注' })

    const { items } = useToast()
    expect(items.value.some((item) => item.message.includes('已保存'))).toBe(true)
  })

  it('保存失败时展示后端 message，且不弹成功 toast', async () => {
    const { wrapper } = await mountDetail('/social/users/p-1', detailHandler(() =>
      fail(422, 'validation.failed', '备注不能超过 200 字'),
    ))

    await wrapper.get('[data-test="edit-notes"]').setValue('超长备注')
    await wrapper.get('[data-test="user-save"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="user-save-error"]').text()).toBe('备注不能超过 200 字')
    const { items } = useToast()
    expect(items.value.some((item) => item.message.includes('已保存'))).toBe(false)
  })
})
