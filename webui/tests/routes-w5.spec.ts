/**
 * W5 §125：路由导航级测试。
 *
 * 真实 router + 真实守卫 + 真实懒加载页面：从总览走到每个领域页，
 * 确认每条路由都能解析、组件都能加载、标题正确。
 */

import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import router from '@/router'
import { useAuthStore } from '@/stores/auth'

const W5_ROUTES: { path: string; name: string; title: string }[] = [
  { path: '/', name: 'dashboard', title: '总览' },
  { path: '/character', name: 'character', title: '角色' },
  { path: '/character/world', name: 'character-world', title: '角色 · 世界' },
  { path: '/character/world/timeline', name: 'character-world-timeline', title: '角色 · 世界时间线' },
  { path: '/social', name: 'social-users', title: '社交 · 用户' },
  { path: '/social/users/10001', name: 'social-user', title: '社交 · 人物' },
  { path: '/social/groups', name: 'social-groups', title: '社交 · 群组' },
  { path: '/social/relationships', name: 'social-relationships', title: '社交 · 关系' },
  { path: '/social/commitments', name: 'social-commitments', title: '社交 · 承诺' },
  { path: '/social/commitments/c-1', name: 'social-commitment', title: '社交 · 承诺详情' },
  { path: '/social/sessions', name: 'social-sessions', title: '社交 · 会话' },
  { path: '/memory', name: 'memory', title: '记忆' },
  { path: '/memory/timeline', name: 'memory-timeline', title: '记忆 · 时间线' },
  { path: '/memory/health', name: 'memory-health', title: '记忆 · 健康度' },
  { path: '/memory/12', name: 'memory-detail', title: '记忆 · 详情' },
  { path: '/abilities/tools', name: 'abilities-tools', title: '能力 · 工具' },
  { path: '/abilities/tools/weather', name: 'abilities-tool', title: '能力 · 工具详情' },
  { path: '/abilities/media', name: 'abilities-media', title: '能力 · 媒体' },
  { path: '/abilities/agent', name: 'abilities-agent', title: '能力 · Agent' },
  { path: '/abilities/agent/tasks/t-1', name: 'abilities-agent-task', title: '能力 · Agent 任务' },
  { path: '/system/settings', name: 'system-settings', title: '系统 · 设置' },
  { path: '/system/logs', name: 'system-logs', title: '系统 · 日志' },
  { path: '/system/runtime', name: 'system-runtime', title: '系统 · Runtime' },
  { path: '/ai', name: 'ai-overview', title: 'AI 与模型' },
  { path: '/ai/prompts', name: 'ai-prompts', title: 'AI · 提示词' },
]

function sessionEnvelope() {
  return {
    ok: true,
    data: {
      user: { name: 'admin' },
      csrf_token: 'token',
      permissions: { admin: true },
      lang: 'zh',
      theme: 'dark',
    },
    meta: { request_id: 'nav-test' },
  }
}

describe('W5 路由导航', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify(sessionEnvelope()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ) as unknown as typeof fetch
  })

  // 24 条懒加载路由在 jsdom 下需要更多时间（每个 chunk 首次解析都要编译）
  it(
    '已登录时每条领域路由都能解析、加载组件并带上标题',
    async () => {
    const auth = useAuthStore()
    expect(await auth.bootstrap()).toBe(true)

    for (const route of W5_ROUTES) {
      await router.push(route.path)
      await router.isReady()
      expect(router.currentRoute.value.name, `路由 ${route.path}`).toBe(route.name)
      expect(router.currentRoute.value.meta.title, `标题 ${route.path}`).toBe(route.title)
      const matched = router.currentRoute.value.matched
      expect(matched.length, `匹配层数 ${route.path}`).toBeGreaterThan(0)
      // 懒加载页面必须真的能解析出组件（chunk 存在、无 import 错误）
      for (const record of matched) {
        if (record.components) {
          for (const component of Object.values(record.components)) {
            expect(component, `组件缺失 ${route.path}`).toBeTruthy()
          }
        }
      }
    }
    },
    60_000,
  )

  it('未登录时访问领域路由会被守卫送回登录页', async () => {
    const auth = useAuthStore()
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({
          ok: false,
          error: { code: 'auth.unauthorized', message: '未登录' },
          meta: { request_id: 'nav-test' },
        }),
        { status: 401, headers: { 'Content-Type': 'application/json' } },
      ),
    ) as unknown as typeof fetch
    expect(await auth.bootstrap()).toBe(false)

    await router.push('/memory')
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/memory')
  })
})
