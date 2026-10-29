/**
 * W6 §32/§33/§34：API 错误边界。
 *
 * 代表页（MemoryBrowse / SocialUsers / Tools / Runtime / AiProviders）在
 * 500/503/404/403/409/422 下都必须渲染可重试的错误态、原样显示后端 message，
 * 绝不空白；401 会清空会话（真实外壳随后把用户送回 login），且不得出现重试风暴。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick, type Component } from 'vue'
import { createMemoryHistory, createRouter, type RouteRecordRaw, type Router } from 'vue-router'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import App from '@/App.vue'
import { api } from '@/api/client'
import AiProviders from '@/pages/ai/AiProviders.vue'
import MemoryBrowse from '@/pages/memory/MemoryBrowse.vue'
import SocialUsers from '@/pages/social/SocialUsers.vue'
import Tools from '@/pages/abilities/Tools.vue'
import Runtime from '@/pages/system/Runtime.vue'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'
import type { SessionData } from '@/types/auth'

const originalFetch = globalThis.fetch
const originalWebSocket = globalThis.WebSocket

const STATUSES = [500, 503, 404, 403, 409, 422] as const

const MEMORY_ROUTES: RouteRecordRaw[] = [
  { path: '/memory', name: 'memory', component: { template: '<div />' } },
  { path: '/memory/:memoryId(\\d+)', name: 'memory-detail', component: { template: '<div />' } },
]
const SOCIAL_ROUTES: RouteRecordRaw[] = [
  { path: '/social', name: 'social-users', component: { template: '<div />' } },
]
const AI_ROUTES: RouteRecordRaw[] = [
  { path: '/ai', name: 'ai-overview', component: { template: '<div />' } },
  { path: '/ai/providers', name: 'ai-providers', component: { template: '<div />' } },
  { path: '/ai/models', name: 'ai-models', component: { template: '<div />' } },
]

interface PageCase {
  label: string
  component: Component
  path?: string
  routes?: RouteRecordRaw[]
}

const PAGES: PageCase[] = [
  { label: 'MemoryBrowse', component: MemoryBrowse, path: '/memory', routes: MEMORY_ROUTES },
  { label: 'SocialUsers', component: SocialUsers, path: '/social', routes: SOCIAL_ROUTES },
  { label: 'Tools', component: Tools },
  { label: 'Runtime', component: Runtime },
  { label: 'AiProviders', component: AiProviders, path: '/ai/providers', routes: AI_ROUTES },
]

/** 挂载代表页；传入 pinia 时可先在其中准备 auth store 状态。 */
async function mountPage(page: PageCase, pinia?: Pinia): Promise<VueWrapper> {
  const activePinia = pinia ?? useFreshPinia()
  let localRouter: Router | null = null
  if (page.path && page.routes) {
    localRouter = createRouter({ history: createMemoryHistory(), routes: page.routes })
    await localRouter.push(page.path)
    await localRouter.isReady()
  }
  const wrapper = mount(page.component, {
    global: { plugins: localRouter ? [activePinia, localRouter] : [activePinia] },
  })
  await flushAll()
  return wrapper
}

/** 每个状态码都用一条可辨认的后端 message。 */
function statusMessage(status: number): string {
  return `后端返回 HTTP ${status}：服务暂时不可用，请稍后重试`
}

function unhandled(request: MockRequest): MockReply {
  return fail(500, 'internal.error', `未 mock 的请求：${request.method} ${request.url}`)
}

/** 所有请求都以给定状态失败。 */
function alwaysFails(status: number): (request: MockRequest) => MockReply {
  return () => fail(status, 'internal.error', statusMessage(status))
}

/** 前 N 次请求失败，之后交给成功 handler（用于验证重试不是装饰）。 */
function failThenSucceed(
  failures: number,
  success: (request: MockRequest) => MockReply,
): (request: MockRequest) => MockReply {
  let remaining = failures
  return (request) => {
    if (remaining > 0) {
      remaining -= 1
      return fail(503, 'internal.error', statusMessage(503))
    }
    return success(request)
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch
  globalThis.WebSocket = originalWebSocket
})

beforeAll(async () => {
  // jsdom 下懒加载分包的动态 import 可能晚于 settle 循环：先预热 login 分块，
  // 否则 401 后的跳转会拖到测试环境销毁之后（history 已不存在）。
  await import('@/pages/Login.vue')
})

describe('W6 错误边界（§32/§33/§34）', () => {
  for (const page of PAGES) {
    for (const status of STATUSES) {
      it(`${page.label} 在 HTTP ${status} 时显示可重试的错误态，绝不空白`, async () => {
        installFetch(alwaysFails(status))

        const wrapper = await mountPage(page)
        const alert = wrapper.find('[role="alert"]')

        expect(alert.exists(), `${page.label} 没有错误态`).toBe(true)
        expect(wrapper.text()).toContain(statusMessage(status))
        expect(wrapper.find('[data-test="retry"]').exists(), `${page.label} 缺少重试按钮`).toBe(true)
        expect(wrapper.text().trim().length, `${page.label} 渲染为空白`).toBeGreaterThan(0)
        expect(wrapper.html().length, `${page.label} 没有渲染任何 DOM`).toBeGreaterThan(0)

        wrapper.unmount()
      })
    }
  }

  it('503 之后点击重试会重新请求并恢复正常渲染', async () => {
    installFetch(
      failThenSucceed(1, (request) => {
        const url = request.url.split('?')[0] ?? request.url
        if (url === '/api/v1/memories') {
          return ok({ items: [], total: 0, limit: 20, offset: 0, next_cursor: null })
        }
        return unhandled(request)
      }),
    )

    const memoryPage = PAGES[0] as PageCase
    const wrapper = await mountPage(memoryPage)
    expect(wrapper.text()).toContain(statusMessage(503))

    await wrapper.get('[data-test="retry"]').trigger('click')
    await flushAll()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('还没有长期记忆')
    wrapper.unmount()
  })

  for (const page of PAGES) {
    it(`${page.label} 的 401 会清空会话，且不会重试`, async () => {
      // 先用同一个 pinia 建立「已登录」状态，再挂载页面。
      const pinia = createPinia()
      setActivePinia(pinia)
      const auth = useAuthStore(pinia)
      auth.status = 'authenticated'
      auth.user = { name: 'admin' }
      api.setCsrf('w6-csrf')

      const calls = installFetch(() => fail(401, 'auth.unauthorized', '会话已失效，请重新登录'))
      const wrapper = await mountPage(page, pinia)
      await flushAll()

      expect(auth.status, `${page.label} 未清空会话`).toBe('anonymous')
      expect(auth.user).toBeNull()
      expect(api.csrfReady).toBe(false)

      // 每个端点最多请求一次；401 之后不再产生任何新请求（无重试风暴）。
      const perPath = new Map<string, number>()
      for (const call of calls) {
        const path = call.url.split('?')[0] ?? call.url
        perPath.set(path, (perPath.get(path) ?? 0) + 1)
      }
      for (const [path, count] of perPath) {
        expect(count, `${page.label} 对 ${path} 重试了 ${count} 次`).toBe(1)
      }

      const before = calls.length
      await flushAll()
      await flushAll()
      expect(calls.length - before, `${page.label} 在 401 后仍在发请求`).toBe(0)
      expect(wrapper.text().trim().length).toBeGreaterThan(0)

      wrapper.unmount()
    })
  }

  it('页面请求 401 后，真实外壳的 watcher 把用户带回 login 并记录 redirect', async () => {
    class FakeWebSocket {
      static instances: FakeWebSocket[] = []
      readonly url: string
      onopen: ((event: unknown) => void) | null = null
      onmessage: ((event: { data: unknown }) => void) | null = null
      onclose: ((event: unknown) => void) | null = null
      onerror: ((event: unknown) => void) | null = null
      constructor(url: string) {
        this.url = url
        FakeWebSocket.instances.push(this)
      }
      close(): void {}
      send(): void {}
    }
    globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket

    const pinia = createPinia()
    setActivePinia(pinia)

    const requests = installFetch((request) => {
      if (request.url.includes('/api/v1/session')) {
        return ok<SessionData>({
          user: { name: 'admin' },
          csrf_token: 'w6-csrf',
          permissions: { admin: true },
        })
      }
      if (request.url.includes('/api/v1/memories')) {
        return fail(401, 'auth.unauthorized', '会话已失效')
      }
      if (request.url.includes('/api/v1/config/restart-pending')) {
        return ok({ pending: [], since: null })
      }
      return ok({})
    })

    await router.replace('/memory')
    await router.isReady()

    const wrapper = mount(App, { attachTo: document.body, global: { plugins: [pinia, router] } })
    const settle = async (): Promise<void> => {
      for (let index = 0; index < 8; index += 1) {
        await flushPromises()
        await new Promise((resolve) => setTimeout(resolve, 0))
      }
      await nextTick()
    }
    await settle()

    const memories = requests.filter((request) => request.url.includes('/api/v1/memories'))
    expect(memories.length, '记忆页应只请求一次').toBe(1)

    const auth = useAuthStore(pinia)
    expect(auth.status).toBe('anonymous')
    expect(auth.user).toBeNull()
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/memory')
    expect(wrapper.find('[data-testid="login-view"]').exists()).toBe(true)

    const total = requests.length
    await settle()
    expect(requests.length, '401 之后不应再有任何请求').toBe(total)
    expect(memories.length).toBeLessThanOrEqual(2)

    wrapper.unmount()
  })
})
