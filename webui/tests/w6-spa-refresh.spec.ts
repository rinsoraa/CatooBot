/**
 * W6 §22/§23/§24/§25：SPA 刷新 / 深链 / 404。
 *
 * 用真实 router 验证：带查询串的深链在「刷新」（全新 pinia + 只 mock 网络）后
 * 仍解析到同名的真实页面、查询串原样保留；未知路径落到 NotFound；每条路由
 * 都有非空且互不重复的标题。
 */

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RouteRecordNormalized, RouteLocationRaw } from 'vue-router'

import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import type { ApiFailure, ApiSuccess } from '@/types/api'
import type { SessionData } from '@/types/auth'

const originalFetch = globalThis.fetch

const SESSION_DATA: SessionData = {
  user: { name: 'admin' },
  csrf_token: 'w6-csrf',
  permissions: { admin: true },
}

const DEEP_LINKS: { url: string; name: string; query?: Record<string, string> }[] = [
  { url: '/ai', name: 'ai-overview' },
  { url: '/ai/providers?create=1', name: 'ai-providers', query: { create: '1' } },
  { url: '/ai/models?create=1', name: 'ai-models', query: { create: '1' } },
  {
    url: '/system/settings?focus=ai.temperature&level=expert',
    name: 'system-settings',
    query: { focus: 'ai.temperature', level: 'expert' },
  },
  { url: '/character', name: 'character' },
  { url: '/character/world', name: 'character-world' },
  { url: '/character/world/timeline', name: 'character-world-timeline' },
  { url: '/social', name: 'social-users' },
  { url: '/social/users/10001', name: 'social-user' },
  { url: '/social/commitments', name: 'social-commitments' },
  { url: '/memory', name: 'memory' },
  { url: '/memory/12', name: 'memory-detail' },
  { url: '/memory/health', name: 'memory-health' },
  { url: '/abilities/tools', name: 'abilities-tools' },
  { url: '/abilities/tools/weather', name: 'abilities-tool' },
  { url: '/abilities/media', name: 'abilities-media' },
  { url: '/abilities/agent', name: 'abilities-agent' },
  { url: '/system/logs', name: 'system-logs' },
  { url: '/system/runtime', name: 'system-runtime' },
  { url: '/system/credentials', name: 'system-credentials' },
  { url: '/system/settings/advanced', name: 'system-advanced' },
  { url: '/system/settings/restart-pending', name: 'system-restart-pending' },
]

/** 只放行会话引导；任何页面级请求都会以 404 失败并被记录。 */
function installSessionFetch(): { sessionCalls: () => number; dataCalls: () => number } {
  let session = 0
  let data = 0
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (url.includes('/api/v1/session')) {
      session += 1
      const body: ApiSuccess<SessionData> = { ok: true, data: SESSION_DATA, meta: { request_id: 'w6-spa' } }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    data += 1
    const body: ApiFailure = {
      ok: false,
      error: { code: 'resource.not_found', message: `深链导航不应发起页面请求：${url}` },
      meta: { request_id: 'w6-spa' },
    }
    return new Response(JSON.stringify(body), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
  return { sessionCalls: () => session, dataCalls: () => data }
}

function concretePath(record: RouteRecordNormalized): string {
  const values: Record<string, string> = {
    memoryId: '12',
    personId: '10001',
    commitmentId: 'c-1',
    taskId: 't-1',
    name: 'weather',
  }
  return record.path
    .replace(/:([A-Za-z0-9_]+)\([^)]*\)/g, (_match, name: string) => values[name] ?? '10001')
    .replace(/:([A-Za-z0-9_]+)/g, (_match, name: string) => values[name] ?? '10001')
}

/** 跟随记录级 redirect，得到守卫实际落点的最终地址。 */
function expectedFullPath(path: string): string {
  const resolved = router.resolve(path)
  const redirect = resolved.matched.find((record) => record.redirect !== undefined)?.redirect
  if (redirect !== undefined && typeof redirect === 'object') {
    return expectedFullPath(router.resolve(redirect as RouteLocationRaw).fullPath)
  }
  return resolved.fullPath
}

async function resetToLogin(): Promise<void> {
  await router.replace('/login')
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('W6 SPA 刷新 / 深链 / 404（§22/§23/§24/§25）', () => {
  it('每一条深链都在已登录会话下解析到真实页面，并原样保留查询串', async () => {
    const fetchState = installSessionFetch()
    await resetToLogin()

    await router.push(DEEP_LINKS[0]!.url)
    expect(useAuthStore().isAuthenticated).toBe(true)

    for (const entry of DEEP_LINKS) {
      await router.push(entry.url)
      const current = router.currentRoute.value
      expect(current.name, `深链 ${entry.url} 未解析到 ${entry.name}`).toBe(entry.name)
      expect(current.fullPath, `深链 ${entry.url} 的查询串被改写`).toBe(entry.url)
      for (const [key, value] of Object.entries(entry.query ?? {})) {
        expect(current.query[key], `深链 ${entry.url} 丢失 ?${key}`).toBe(value)
      }
      // 深链只改地址，不触发任何页面级请求（页面挂载才取数）。
      expect(fetchState.dataCalls(), `深链 ${entry.url} 发起了页面请求`).toBe(0)
    }
    expect(fetchState.sessionCalls()).toBe(1)
  }, 60_000)

  it('全新会话（模拟刷新）直接打开带查询串的深链仍然是登录后的真实页面', async () => {
    const fetchState = installSessionFetch()
    await resetToLogin()

    await router.push('/system/settings?focus=ai.temperature&level=expert')

    const auth = useAuthStore()
    expect(auth.status).toBe('authenticated')
    expect(router.currentRoute.value.name).toBe('system-settings')
    expect(router.currentRoute.value.query.focus).toBe('ai.temperature')
    expect(router.currentRoute.value.query.level).toBe('expert')
    // 冷启动只探测一次会话。
    expect(fetchState.sessionCalls()).toBe(1)
    expect(fetchState.dataCalls()).toBe(0)
  }, 60_000)

  it('未知路径落到 NotFound（not-found），再次访问也不会空白或打转', async () => {
    installSessionFetch()
    await resetToLogin()
    await router.push('/ai')
    expect(useAuthStore().isAuthenticated).toBe(true)

    await router.push('/definitely-not-a-page')
    const current = router.currentRoute.value
    expect(current.name).toBe('not-found')
    expect(current.matched.length).toBeGreaterThan(0)
    const component = current.matched[current.matched.length - 1]?.components?.default
    expect(component, 'not-found 必须解析出 NotFound 组件').toBeTruthy()

    // 再访问一次仍然稳定落在 NotFound，而不是被重定向回自身（无循环）。
    await router.push('/definitely-not-a-page')
    expect(router.currentRoute.value.name).toBe('not-found')
  }, 60_000)

  it('每条命名路由的 meta.title 都非空，且至少有 15 个不同的标题', async () => {
    installSessionFetch()
    await resetToLogin()
    await router.push('/ai')
    expect(useAuthStore().isAuthenticated).toBe(true)

    const titles = new Set<string>()
    const records = router.getRoutes().filter((record) => record.name !== undefined)

    for (const record of records) {
      const finalPath = expectedFullPath(concretePath(record))
      const title = router.resolve(finalPath).meta.title
      expect(typeof title, `路由 ${record.path} 缺少标题`).toBe('string')
      expect((title as string).length, `路由 ${record.path} 的标题为空`).toBeGreaterThan(0)
      titles.add(title as string)
    }

    expect(titles.size).toBeGreaterThanOrEqual(15)
  }, 60_000)
})
