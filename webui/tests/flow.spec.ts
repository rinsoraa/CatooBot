/**
 * W3 流程测试（§51）：真实 router / pinia / stores / App.vue，只 mock 网络。
 *
 * 覆盖：未登录跳转、登录并渲染真实 overview 值、CSRF 头、WebSocket status 帧、
 * 主题持久化、写请求 401 回登录页、单块数据缺失时的独立降级。
 */

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App.vue'
import { api } from '@/api/client'
import router from '@/router'
import { useAppStore } from '@/stores/app'
import { useRealtimeStore } from '@/stores/realtime'
import { useRuntimeStore } from '@/stores/runtime'
import type { ApiFailure, ApiSuccess } from '@/types/api'
import type { SessionData } from '@/types/auth'
import type { OverviewData, RuntimeData } from '@/types/runtime'

interface RecordedRequest {
  url: string
  method: string
  headers: Headers
  body: unknown
}

const SESSION_DATA: SessionData = {
  user: { name: 'admin' },
  csrf_token: 'csrf-w3-token',
  permissions: { admin: true },
}

const OVERVIEW: OverviewData = {
  qq: { online: true, self_id: 10001, messages_received: 7, users: 3, groups: 1, sessions: 1 },
  ai: {
    enabled: true,
    current_model: 'deepseek',
    models_ok: 2,
    models_total: 3,
    requests: 42,
    errors: 0,
    rate_limited: 0,
  },
  world: {
    phase: '下午',
    location: '书房',
    action: { name: '写作', progress: 0.5 },
    modes: ['focus'],
    needs: { critical: [], pressing: ['休息'] },
    world_revision: 11,
    cognitive_revision: 3,
  },
  runtime: {
    scheduler: { running: true, interval_seconds: 30, ticks: 120, catchups: 0 },
    uptime_seconds: 3661,
    database: { connected: true },
    hub: { subscribers: 1, published: 9, dropped: 0 },
  },
  counts: { memories: 10, experiences: 2, goals_open: 1, commitments_open: 0 },
}

const RUNTIME: RuntimeData = {
  scheduler: { running: true, interval_seconds: 30, ticks: 120, catchups: 0 },
  uptime_seconds: 3661,
  database: { connected: true },
}

/** 只 mock 网络的最简 W2 后端。 */
class MockServer {
  authenticated = false
  unauthorizedWrites = false
  readonly requests: RecordedRequest[] = []
  overview: OverviewData = OVERVIEW
  runtimeData: RuntimeData = RUNTIME

  private readonly csrf = SESSION_DATA.csrf_token

  private success<T>(data: T): Response {
    const body: ApiSuccess<T> = { ok: true, data, meta: { request_id: 'test-req' } }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  private failure(status: number, code: string, message: string): Response {
    const body: ApiFailure = {
      ok: false,
      error: { code, message },
      meta: { request_id: 'test-req' },
    }
    return new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  private sessionPayload(): SessionData {
    return { ...SESSION_DATA, csrf_token: this.csrf }
  }

  private static parseBody(init?: RequestInit): unknown {
    if (typeof init?.body !== 'string' || init.body.length === 0) return null
    try {
      return JSON.parse(init.body) as unknown
    } catch {
      return null
    }
  }

  async handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    const headers = new Headers(init?.headers ?? {})
    const path = url.split('?')[0] ?? url
    const body = MockServer.parseBody(init)
    this.requests.push({ url, method, headers, body })

    if (path === '/api/v1/session' && method === 'GET') {
      if (!this.authenticated) return this.failure(401, 'auth.unauthorized', '未登录或会话已失效')
      return this.success(this.sessionPayload())
    }
    if (path === '/api/v1/session' && method === 'POST') {
      const credentials = body as { username?: string; password?: string } | null
      if (credentials?.username === 'admin' && credentials.password === 'secret') {
        this.authenticated = true
        return this.success(this.sessionPayload())
      }
      return this.failure(401, 'auth.bad_credentials', '用户名或密码不正确')
    }
    if (path === '/api/v1/session' && method === 'PATCH') {
      if (this.unauthorizedWrites) return this.failure(401, 'auth.unauthorized', '未登录或会话已失效')
      if (headers.get('X-CSRF-Token') !== this.csrf) return this.failure(403, 'auth.csrf', 'CSRF 校验失败')
      return this.success(body ?? {})
    }
    if (path === '/api/v1/overview' && method === 'GET') {
      if (!this.authenticated) return this.failure(401, 'auth.unauthorized', '未登录或会话已失效')
      return this.success(this.overview)
    }
    if (path === '/api/v1/runtime' && method === 'GET') {
      if (!this.authenticated) return this.failure(401, 'auth.unauthorized', '未登录或会话已失效')
      return this.success(this.runtimeData)
    }
    return this.failure(404, 'resource.not_found', '未知端点')
  }

  writes(): RecordedRequest[] {
    return this.requests.filter((request) => request.method !== 'GET')
  }
}

/** 可注入的假 WebSocket：立即 onopen，测试手动 emit 帧。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []

  static latest(): FakeWebSocket {
    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    if (!socket) throw new Error('测试中没有已建立的 WebSocket 连接')
    return socket
  }

  readonly url: string
  readonly sent: string[] = []
  onopen: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
    queueMicrotask(() => this.onopen?.({}))
  }

  close(): void {
    this.onclose?.({})
  }

  send(data: string): void {
    this.sent.push(data)
  }

  emit(topic: string, data: unknown, ts = Date.now()): void {
    this.onmessage?.({ data: JSON.stringify({ topic, ts, data }) })
  }
}

const originalFetch = globalThis.fetch
const originalWebSocket = globalThis.WebSocket

let server: MockServer
let wrapper: VueWrapper | null = null
let activePinia: Pinia | null = null

beforeAll(async () => {
  // 预加载路由分包：jsdom 下 vue-router 对懒加载组件的动态 import 可能与
  // 并发中的请求互锁，先 import 一次让模块缓存就绪（不改变应用行为）。
  await Promise.all([
    import('@/pages/Dashboard.vue'),
    import('@/pages/Login.vue'),
    import('@/pages/NotFound.vue'),
  ])
})

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) {
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  await nextTick()
}

async function mountApp(path: string): Promise<void> {
  activePinia = createPinia()
  setActivePinia(activePinia)
  await router.replace(path)
  await router.isReady()
  wrapper = mount(App, {
    attachTo: document.body,
    global: { plugins: [activePinia, router] },
  })
  await settle()
}

async function login(username = 'admin', password = 'secret'): Promise<void> {
  if (!wrapper) throw new Error('App 尚未挂载')
  await wrapper.find('input[name="username"]').setValue(username)
  await wrapper.find('input[name="password"]').setValue(password)
  await wrapper.find('form').trigger('submit')
  await settle()
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  server = new MockServer()
  FakeWebSocket.instances = []
  globalThis.WebSocket = FakeWebSocket as unknown as typeof WebSocket
  globalThis.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    server.handle(input, init),
  ) as unknown as typeof fetch
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  activePinia = null
  globalThis.fetch = originalFetch
  globalThis.WebSocket = originalWebSocket
})

describe('W3 前端流程', () => {
  it('(a) 未登录访问 / 会落到登录页，不出现总览内容', async () => {
    await mountApp('/')

    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper?.find('[data-testid="login-view"]').exists()).toBe(true)
    expect(wrapper?.find('[data-testid="dashboard"]').exists()).toBe(false)
  })

  it('(b) 登录成功后进入总览，并渲染 overview 返回的真实值', async () => {
    await mountApp('/login')
    await login()

    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper?.find('[data-testid="dashboard"]').exists()).toBe(true)
    const text = wrapper?.text() ?? ''
    expect(text).toContain('10001')
    expect(text).toContain('42')
    expect(text).toContain('已启用')
  })

  it('(c) 写请求自动附带 /session 下发的 CSRF token', async () => {
    await mountApp('/login')
    await login()

    await api.patch('/session', { theme: 'light' })

    const write = server.writes().find((request) => request.method === 'PATCH')
    expect(write).toBeDefined()
    expect(write?.headers.get('X-CSRF-Token')).toBe(SESSION_DATA.csrf_token)
  })

  it('(d) status 帧更新 runtime store，世界卡片显示新地点', async () => {
    await mountApp('/login')
    await login()

    FakeWebSocket.latest().emit('status', {
      qq: { online: true, self_id: 10001 },
      world: { phase: '傍晚', location: '公园', action: { name: '散步' }, world_revision: 12 },
      runtime: { uptime_seconds: 321, scheduler: { ticks: 121, interval_seconds: 30 } },
    })
    await settle()

    const runtime = useRuntimeStore()
    expect(runtime.world?.location).toBe('公园')
    expect(runtime.world?.action?.name).toBe('散步')
    expect(runtime.runtime?.uptime_seconds).toBe(321)
    const text = wrapper?.text() ?? ''
    expect(text).toContain('公园')
    expect(text).toContain('散步')
  })

  it('(e) 主题写入 localStorage 并在重新挂载后重新应用', async () => {
    await mountApp('/login')

    const app = useAppStore()
    app.setTheme('light')
    expect(localStorage.getItem('catoobot-theme')).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')

    wrapper?.unmount()
    wrapper = null
    await mountApp('/login')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('(f) 写请求 401 会把用户送回登录页', async () => {
    await mountApp('/login')
    await login()
    expect(router.currentRoute.value.path).toBe('/')

    server.unauthorizedWrites = true
    await expect(api.patch('/session', { theme: 'dark' })).rejects.toThrow()
    await settle()

    expect(router.currentRoute.value.name).toBe('login')
    expect(wrapper?.find('[data-testid="login-view"]').exists()).toBe(true)
    expect(wrapper?.find('[data-testid="dashboard"]').exists()).toBe(false)
  })

  it('(g) 数据块缺失时各卡片独立降级为空态而不是崩溃', async () => {
    server.overview = {
      qq: {},
      ai: {},
      world: {},
      runtime: {},
      counts: {},
    }
    server.runtimeData = {}
    await mountApp('/login')
    await login()

    const dashboard = wrapper?.find('[data-testid="dashboard"]')
    expect(dashboard?.exists()).toBe(true)
    const text = dashboard?.text() ?? ''
    expect(text).toContain('暂无世界状态')
    expect(text).toContain('暂无实时事件')
    // 数据缺失时卡片显示「—」，绝不显示编造的状态
    expect(text).toContain('—')
    expect(text).not.toContain('离线')
    expect(text).not.toContain('已停用')

    const realtime = useRealtimeStore()
    expect(realtime.feed).toHaveLength(0)
  })
})
