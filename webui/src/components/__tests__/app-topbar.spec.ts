/** AppTopbar（§26）：实时徽标、机器人状态、主题循环、用户菜单与登出。 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { Pinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AppTopbar from '@/components/AppTopbar.vue'
import { useAuthStore } from '@/stores/auth'
import { useRealtimeStore } from '@/stores/realtime'
import { useRuntimeStore } from '@/stores/runtime'

let pinia: Pinia

beforeEach(() => {
  localStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** 后端信封的最小替身：`{ok:true,data,meta}`（§7）。 */
function stubEnvelope(data: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true, data, meta: { request_id: 'test' } }),
    })),
  )
}

async function mountTopbar() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' }, meta: { title: '总览' } },
      { path: '/login', name: 'login', component: { template: '<div />' }, meta: { title: '登录' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(AppTopbar, { global: { plugins: [pinia, router] } })
  return { wrapper, router }
}

describe('AppTopbar', () => {
  it('shows the connected realtime text from the realtime store', async () => {
    const realtime = useRealtimeStore()
    realtime.state = 'connected'
    const { wrapper } = await mountTopbar()
    const badge = wrapper.get('[data-test="realtime"]')
    expect(badge.text()).toContain('Live / 已连接')
    expect(badge.attributes('data-state')).toBe('ok')
  })

  it('shows the reconnecting realtime text when not connected', async () => {
    const realtime = useRealtimeStore()
    realtime.state = 'reconnecting'
    const { wrapper } = await mountTopbar()
    const badge = wrapper.get('[data-test="realtime"]')
    expect(badge.text()).toContain('Reconnecting… / 重连中…')
    expect(badge.text()).not.toContain('已连接')
  })

  it('cycles the theme through system / light / dark with an accessible label', async () => {
    const { wrapper } = await mountTopbar()
    const button = wrapper.get('[data-test="theme"]')
    expect(button.attributes('aria-label')).toContain('跟随系统')

    await button.trigger('click')
    expect(button.attributes('aria-label')).toContain('浅色')

    await button.trigger('click')
    expect(button.attributes('aria-label')).toContain('深色')

    await button.trigger('click')
    expect(button.attributes('aria-label')).toContain('跟随系统')
  })

  it('renders page title, legacy link and emits toggle-sidebar', async () => {
    const { wrapper } = await mountTopbar()
    expect(wrapper.get('.cb-topbar__title').text()).toBe('总览')

    const legacy = wrapper.get('a[href="/legacy"]')
    expect(legacy.text()).toBe('旧版后台')
    expect(legacy.attributes('target')).toBe('_self')

    await wrapper.get('[data-test="toggle-sidebar"]').trigger('click')
    expect(wrapper.emitted('toggle-sidebar')).toHaveLength(1)
  })

  it('renders the bot status from runtime store data', async () => {
    const runtime = useRuntimeStore()
    const offline = await mountTopbar()
    expect(offline.wrapper.get('[data-test="bot-status"]').text()).toContain('离线')

    runtime.overview = { qq: { online: true }, ai: {}, world: {}, runtime: {}, counts: {} }
    await nextTick()
    const online = await mountTopbar()
    expect(online.wrapper.get('[data-test="bot-status"]').text()).toContain('在线')
  })

  it('shows the authenticated user name from the session envelope', async () => {
    stubEnvelope({ user: { name: 'Catoo' }, csrf_token: 'csrf-token' })
    const auth = useAuthStore()
    await auth.bootstrap()
    expect(auth.user?.name).toBe('Catoo')

    const { wrapper } = await mountTopbar()
    expect(wrapper.get('[data-test="user-menu"]').text()).toContain('Catoo')
  })

  it('calls the auth logout action and returns to /login', async () => {
    const auth = useAuthStore()
    const logout = vi.spyOn(auth, 'logout').mockResolvedValue(undefined)
    const { wrapper, router } = await mountTopbar()

    await wrapper.get('[data-test="logout"]').trigger('click')
    await flushPromises()

    expect(logout).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.path).toBe('/login')
  })
})
