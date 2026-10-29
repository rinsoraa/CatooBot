/**
 * W6 §18/§19：路由守卫的数据驱动覆盖。
 *
 * 直接读取真实 router 的路由表：每一条 `meta.requiresAuth === true` 的路由，
 * 在匿名会话下都必须落到 login，并把原始地址写进 `?redirect=`（根路径 `/` 除外）。
 * 所有断言只依赖真实 router + 守卫 + 401 网络 mock，不用假的守卫替身。
 */

import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it } from 'vitest'
import type { RouteRecordNormalized, RouteLocationRaw } from 'vue-router'

import router from '@/router'
import type { ApiFailure } from '@/types/api'

const originalFetch = globalThis.fetch

const UNAUTHORIZED: ApiFailure = {
  ok: false,
  error: { code: 'auth.unauthorized', message: '未登录或会话已失效' },
  meta: { request_id: 'w6-guard' },
}

function unauthorizedFetch(): () => number {
  let calls = 0
  globalThis.fetch = (async () => {
    calls += 1
    return new Response(JSON.stringify(UNAUTHORIZED), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
  return () => calls
}

/** 把路由记录里的 `:param` / `:param(\\d+)` 换成能匹配的示例值。 */
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

function guardedRecords(): RouteRecordNormalized[] {
  return router.getRoutes().filter(
    (record) => record.meta.requiresAuth === true && !record.path.includes('pathMatch'),
  )
}

/**
 * 导航到某条记录时守卫看到的最终地址。
 * `router.resolve()` 不会跟随记录级 `redirect`，因此这里显式跟随一次
 * （/system → /system/settings、/abilities → /abilities/tools）。
 */
function expectedFullPath(path: string): string {
  const resolved = router.resolve(path)
  const redirect = resolved.matched.find((record) => record.redirect !== undefined)?.redirect
  if (redirect !== undefined && typeof redirect === 'object') {
    return expectedFullPath(router.resolve(redirect as RouteLocationRaw).fullPath)
  }
  return resolved.fullPath
}

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('W6 路由守卫（§18/§19）', () => {
  it('路由表至少有 40 条需要登录的路由，且 /login 是唯一的 guest 入口', () => {
    const records = router.getRoutes()
    const guarded = guardedRecords()

    expect(guarded.length).toBeGreaterThanOrEqual(40)

    const login = records.filter((record) => record.path === '/login')
    expect(login).toHaveLength(1)
    expect(login[0]?.meta.guest).toBe(true)
    expect(login[0]?.meta.requiresAuth).toBeUndefined()
  })

  it('每一条需要登录的路由在匿名态都跳到 login，并原样保留 redirect', async () => {
    const guarded = guardedRecords()
    expect(guarded.length).toBeGreaterThanOrEqual(40)

    for (const record of guarded) {
      const path = concretePath(record)
      // 解析后的最终地址：父路由可能带 redirect（/system → /system/settings）。
      const finalPath = expectedFullPath(path)
      const label = `${record.path}（实际 ${finalPath}）`

      // 每条路由都用全新的 pinia，确保 auth store 是「未探测」的匿名态。
      setActivePinia(createPinia())
      const fetchCalls = unauthorizedFetch()

      await router.push(path)

      const current = router.currentRoute.value
      expect(current.name, `守卫未把 ${label} 送回登录页`).toBe('login')
      expect(current.path, `登录页路径异常：${label}`).toBe('/login')

      if (finalPath === '/') {
        // 根路径是默认落点，不应带 redirect 查询串（避免登录后重复跳转）。
        expect(current.query.redirect, `根路径不应携带 redirect：${label}`).toBeUndefined()
      } else {
        expect(current.query.redirect, `redirect 应等于原始地址：${label}`).toBe(finalPath)
        // 不允许把 login 自己当作 redirect，否则会自我循环。
        expect(current.query.redirect).not.toBe('/login')
      }

      // 没有路由会重定向到自身（落点永远是 login，而不是原地址）。
      expect(finalPath, `路由 ${label} 重定向到了自身`).not.toBe('/login')
      expect(current.fullPath.startsWith('/login'), `落点不是登录页：${label}`).toBe(true)

      // 守卫只探测一次会话；401 之后不得重试（无重定向风暴）。
      expect(fetchCalls(), `守卫对 ${label} 发起了多余请求`).toBeLessThanOrEqual(2)
    }
  }, 60_000)
})
