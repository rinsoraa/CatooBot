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
  makeSession,
  makeSpace,
  makeUser,
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
    if (request.path === '/api/v1/social/sessions' && request.method === 'GET') {
      return ok({ active: false, items: [] })
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

describe('SocialUserDetail 页 · 清空会话上下文', () => {
  function sessionHandler(
    options: { active?: boolean; qq?: string; clearReply?: (request: MockRequest) => MockReply } = {},
  ) {
    return (request: MockRequest): MockReply => {
      if (request.path === '/api/v1/social/spaces') return ok({ items: [makeSpace()], map: {} })
      if (request.path === '/api/v1/social/sessions' && request.method === 'GET') {
        return ok({ active: options.active ?? false, items: options.active ? [makeSession()] : [] })
      }
      if (request.path === '/api/v1/social/users/p-1' && request.method === 'GET') {
        return ok(makeUserDetail({ person: makeUser({ qq: options.qq ?? '10001' }) }))
      }
      if (request.path.startsWith('/api/v1/sessions/') && request.method === 'POST') {
        return (
          options.clearReply?.(request) ?? ok({ cleared: true, session_id: 'private:10001' })
        )
      }
      return fail(404, 'not_found', `未模拟 ${request.method} ${request.path}`)
    }
  }

  function sessionInput(wrapper: VueWrapper): string {
    return (wrapper.get('[data-test="session-id"] input').element as HTMLInputElement).value
  }

  it('有活动会话时预填 private:QQ 并显示会话状态', async () => {
    const { wrapper } = await mountDetail('/social/users/p-1', sessionHandler({ active: true }))

    expect(sessionInput(wrapper)).toBe('private:10001')
    expect(wrapper.get('[data-test="session-status"]').text()).toContain('当前有活动会话')
  })

  it('清空必须先确认：取消不发请求，确认后 POST 带 confirm=clear', async () => {
    const { wrapper, calls } = await mountDetail('/social/users/p-1', sessionHandler({ active: true }))

    await wrapper.get('[data-test="session-clear"]').trigger('click')
    expect(wrapper.find('[data-test="confirm"]').exists()).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('不可撤销')
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)

    await wrapper.get('[data-test="cancel"]').trigger('click')
    await flushAll()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)

    await wrapper.get('[data-test="session-clear"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.path).toBe('/api/v1/sessions/private%3A10001/clear')
    expect(posts[0]?.body).toEqual({ confirm: 'clear' })

    const { items } = useToast()
    expect(items.value.some((item) => item.message.includes('已清空'))).toBe(true)
    // 清空后重新读取会话状态。
    expect(calls.filter((call) => call.path === '/api/v1/social/sessions').length).toBe(2)
  })

  it('清空失败时展示后端 message', async () => {
    const { wrapper } = await mountDetail(
      '/social/users/p-1',
      sessionHandler({
        clearReply: () =>
          fail(
            409,
            'session.confirm_required',
            '清空会话上下文会让她忘掉这段对话，请带 confirm=clear 再试',
          ),
      }),
    )

    await wrapper.get('[data-test="session-clear"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="session-clear-error"]').text()).toContain('confirm=clear')
  })

  it('没有 QQ 也没有输入时按钮禁用且不发请求', async () => {
    const { wrapper, calls } = await mountDetail('/social/users/p-1', sessionHandler({ qq: '', active: false }))

    expect(sessionInput(wrapper)).toBe('')
    expect(wrapper.get('[data-test="session-clear"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-test="session-clear"]').trigger('click')
    await flushAll()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(0)
  })
})
