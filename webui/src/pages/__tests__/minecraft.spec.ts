/** Minecraft 页（Phase 1）：禁用态、加入表单、离开确认、错误 toast。 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import Minecraft from '@/pages/Minecraft.vue'
import { useToast } from '@/composables/toast'
import {
  fail,
  flushAll,
  installFetch,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'

const ONLINE_OVERVIEW = {
  enabled: true,
  auth_configured: true,
  runtime: { running: true, pid: 123, managed: true, restarts: 0, down: false, log_tail: [] },
  connection: {
    status: 'ONLINE',
    session_id: 'mc_s1',
    host: '127.0.0.1',
    port: 25565,
    username: 'GuanTou',
    auth_mode: 'offline',
    dimension: 'overworld',
    position: { x: 4.5, y: 21.0, z: 25.3 },
    health: 20,
    last_error: null,
    kicked_reason: null,
    connected_at: 1700000000,
  },
  last_event: null,
}

const DISABLED_OVERVIEW = {
  enabled: false,
  auth_configured: false,
  runtime: { running: false, pid: null, managed: true, restarts: 0, down: false, log_tail: [] },
  connection: {
    status: 'DISCONNECTED',
    session_id: null,
    host: null,
    port: null,
    username: null,
    auth_mode: null,
    dimension: null,
    position: null,
    health: null,
    last_error: null,
    kicked_reason: null,
    connected_at: null,
  },
  last_event: null,
}

function makeHandler(overrides: { join?: MockReply; leave?: MockReply } = {}) {
  return (request: MockRequest): MockReply => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/minecraft' && request.method === 'GET') {
      return ok(ONLINE_OVERVIEW)
    }
    if (url.pathname === '/api/v1/minecraft/join' && request.method === 'POST') {
      return overrides.join ?? ok({ session_id: 'mc_new', status: 'CONNECTING' })
    }
    if (url.pathname === '/api/v1/minecraft/leave' && request.method === 'POST') {
      return overrides.leave ?? ok({ ok: true, status: 'DISCONNECTING' })
    }
    return fail(404, 'resource.not_found', `未模拟 ${request.method} ${url.pathname}`)
  }
}

async function mountPage(
  handler: (request: MockRequest) => MockReply,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(handler)
  const wrapper = mount(Minecraft, { global: { plugins: [pinia] } })
  await flushAll()
  return { wrapper, calls }
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
})

describe('Minecraft 页', () => {
  it('连接层未启用时显示引导而不是表单', async () => {
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(DISABLED_OVERVIEW)
      return fail(503, 'minecraft.disabled', 'Minecraft 连接层未启用')
    })
    expect(wrapper.find('[data-test="minecraft-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="minecraft-host"]').exists()).toBe(false)
  })

  it('在线时展示状态事实与离开按钮，不展示加入表单动作', async () => {
    const { wrapper } = await mountPage(makeHandler())
    expect(wrapper.find('[data-test="minecraft-status-value"]').text()).toContain('在线')
    expect(wrapper.find('[data-test="minecraft-server"]').text()).toContain('127.0.0.1:25565')
    expect(wrapper.find('[data-test="minecraft-username"]').text()).toContain('GuanTou')
    expect(wrapper.find('[data-test="minecraft-dimension"]').text()).toContain('overworld')
    expect(wrapper.find('[data-test="minecraft-position"]').text()).not.toContain('—')
    expect(wrapper.find('[data-test="minecraft-health"]').text()).toContain('20')
    expect(wrapper.find('[data-test="minecraft-leave"]').exists()).toBe(true)
    expect((wrapper.find('[data-test="minecraft-join"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('未连接时填表加入：POST /minecraft/join 带上 host/port', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const { wrapper, calls } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      if (url.pathname === '/api/v1/minecraft/join') {
        return ok({ session_id: 'mc_new', status: 'CONNECTING' })
      }
      return fail(404, 'resource.not_found', 'no')
    })
    await wrapper.find('[data-test="minecraft-host"]').setValue('play.example.org')
    await wrapper.find('[data-test="minecraft-port"]').setValue('25566')
    await wrapper.find('[data-test="minecraft-join"]').trigger('submit')
    await flushAll()
    const join = calls.find((c) => c.url.endsWith('/minecraft/join'))
    expect(join).toBeTruthy()
    expect(join?.body).toEqual({ host: 'play.example.org', port: 25566 })
  })

  it('加入被后端拒绝时弹错误 toast', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      if (url.pathname === '/api/v1/minecraft/join') {
        return fail(409, 'minecraft.session_active', '已经有 bot 会话在运行')
      }
      return fail(404, 'resource.not_found', 'no')
    })
    await wrapper.find('[data-test="minecraft-host"]').setValue('127.0.0.1')
    await wrapper.find('[data-test="minecraft-join"]').trigger('submit')
    await flushAll()
    const toast = useToast()
    expect(
      toast.items.value.some((item) => item.kind === 'error' && item.message === '加入失败'),
    ).toBe(true)
  })

  it('离开需要确认，确认后才 POST leave', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await wrapper.find('[data-test="minecraft-leave"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/leave'))).toBe(false)
    expect(wrapper.get('[role="dialog"]').text()).toContain('离开服务器')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/leave'))).toBe(true)
  })
})
