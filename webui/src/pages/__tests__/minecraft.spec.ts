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
    username: 'Catodayo',
    auth_mode: 'offline',
    dimension: 'overworld',
    position: { x: 4.5, y: 21.0, z: 25.3 },
    health: 20,
    last_error: null,
    kicked_reason: null,
    connected_at: 1700000000,
  },
  action: {
    action: null,
    action_id: null,
    status: 'IDLE',
    started_at: null,
    finished_at: null,
    elapsed_ms: null,
  },
  pathfinder: { goal: null, target: null, distance: null, moving: false },
  last_event: null,
}

const WORLD_VIEW = {
  available: true,
  online: true,
  captured_at: 1700000000,
  age_seconds: 0.5,
  layers: { near: { age_seconds: 0.5 }, local: { age_seconds: 2 }, extended: { age_seconds: 10 } },
  semantic: {
    captured_at: 1700000000,
    self: { location: 'plains', dimension: 'overworld', position: { x: 10, y: 64, z: -5 }, health: 20, food: 20 },
    environment: { biome: 'plains', time_phase: 'day', weather: 'clear', light: 15 },
    terrain: [{ type: 'grassland', direction: 'north', distance: 4 }],
    players: [{ name: 'RinsoraNeko', direction: 'front_right', distance: 6, compass: 'west' }],
    entities: [{ type: 'cow', count: 3, direction: 'west', distance: 8 }],
    points_of_interest: [{ type: 'crafting_table', direction: 'front', distance: 4 }],
  },
  raw: { self: {}, blocks: {} },
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
    if (url.pathname === '/api/v1/minecraft/world' && request.method === 'GET') {
      return ok(WORLD_VIEW)
    }
    if (url.pathname === '/api/v1/minecraft/join' && request.method === 'POST') {
      return overrides.join ?? ok({ session_id: 'mc_new', status: 'CONNECTING' })
    }
    if (url.pathname === '/api/v1/minecraft/leave' && request.method === 'POST') {
      return overrides.leave ?? ok({ ok: true, status: 'DISCONNECTING' })
    }
    if (url.pathname === '/api/v1/minecraft/look_at' && request.method === 'POST') {
      return ok({ action_id: 'act_ui_1', action: 'look_at', status: 'SUCCEEDED' })
    }
    if (url.pathname === '/api/v1/minecraft/move_to' && request.method === 'POST') {
      return ok({
        action_id: 'act_move_ui',
        action: 'move_to',
        status: 'SUCCEEDED',
        result: {
          target: { x: 10, y: 21, z: 30 },
          final_position: { x: 10, y: 21, z: 30 },
          distance_to_target: 0.4,
        },
      })
    }
    if (url.pathname === '/api/v1/minecraft/follow_player' && request.method === 'POST') {
      return ok({ action_id: 'act_follow_ui', action: 'follow_player', status: 'RUNNING' })
    }
    if (url.pathname === '/api/v1/minecraft/stop' && request.method === 'POST') {
      return ok({ status: 'IDLE', cancelled: [] })
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
    expect(wrapper.find('[data-test="minecraft-username"]').text()).toContain('Catodayo')
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

  it('在线时展示 World Debug：环境/玩家/生物/POI/地形', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.find('[data-test="mc-world"]').exists()).toBe(true)
    const env = wrapper.get('[data-test="mc-world-env"]').text()
    expect(env).toContain('plains')
    expect(env).toContain('白天')
    const players = wrapper.get('[data-test="mc-world-players"]').text()
    expect(players).toContain('RinsoraNeko')
    expect(players).toContain('front_right')
    expect(wrapper.get('[data-test="mc-world-entities"]').text()).toContain('cow')
    expect(wrapper.get('[data-test="mc-world-poi"]').text()).toContain('crafting_table')
    expect(wrapper.get('[data-test="mc-world-terrain"]').text()).toContain('grassland')
  })

  it('未在线时不出 World Debug 区', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      if (url.pathname === '/api/v1/minecraft/world') {
        return ok({ available: false, online: false, semantic: null, raw: null })
      }
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect(wrapper.find('[data-test="mc-world"]').exists()).toBe(false)
  })
})

describe('Minecraft 页 · Current Action（Phase 3B）', () => {
  it('空闲时显示 IDLE，Look At Test 被禁用（不在世界）', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect(wrapper.get('[data-test="mc-action-status"]').text()).toContain('IDLE')
    expect(wrapper.get('[data-test="mc-action-name"]').text()).toContain('—')
    expect((wrapper.get('[data-test="mc-action-look"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
    // STOP 永远可用（幂等安全停止）
    expect((wrapper.get('[data-test="mc-action-stop"]').element as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('在线时 Look At Test 提交附近测试坐标到 /minecraft/look_at', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-action-look"]').trigger('click')
    await flushAll()
    const look = calls.find((c) => c.url.endsWith('/minecraft/look_at'))
    expect(look).toBeTruthy()
    // position 是 (4.5, 21.0, 25.3) → Math.round 后 (+5, 0, +5) = (10, 21, 30)
    expect(look?.body).toEqual({ x: 10, y: 21, z: 30 })
  })

  it('STOP 调 /minecraft/stop 并在 toast 里报告取消数量', async () => {
    const { wrapper, calls } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft' && request.method === 'GET') return ok(ONLINE_OVERVIEW)
      if (url.pathname === '/api/v1/minecraft/stop' && request.method === 'POST') {
        return ok({ status: 'IDLE', cancelled: ['act_1'] })
      }
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    await wrapper.get('[data-test="mc-action-stop"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/stop'))).toBe(true)
    const toast = useToast()
    expect(toast.items.value.some((item) => item.detail.includes('act_1'))).toBe(true)
  })

  it('运行中的动作显示名称/ID/耗时', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      action: {
        action: 'look_at',
        action_id: 'act_running',
        status: 'RUNNING',
        started_at: 1700000000,
        finished_at: null,
        elapsed_ms: 1200,
      },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect(wrapper.get('[data-test="mc-action-name"]').text()).toContain('look_at')
    expect(wrapper.get('[data-test="mc-action-status"]').text()).toContain('RUNNING')
    expect(wrapper.get('[data-test="mc-action-id"]').text()).toContain('act_running')
    expect(wrapper.get('[data-test="mc-action-elapsed"]').text()).toContain('1.2 s')
  })
})

describe('Minecraft 页 · move_to（Phase 3C）', () => {
  it('在线时预填附近测试坐标并提交到 /minecraft/move_to', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    // position (4.5, 21.0, 25.3) → 预填 (+4, 0, 0) = (9, 21, 25)
    expect((wrapper.get('[data-test="mc-move-x"]').element as HTMLInputElement).value).toBe('9')
    expect((wrapper.get('[data-test="mc-move-y"]').element as HTMLInputElement).value).toBe('21')
    expect((wrapper.get('[data-test="mc-move-z"]').element as HTMLInputElement).value).toBe('25')

    await wrapper.get('[data-test="mc-move-x"]').setValue('12')
    await wrapper.get('[data-test="mc-move-z"]').setValue('-7')
    await wrapper.get('[data-test="mc-move-to"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/move_to'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ x: 12, y: 21, z: -7 })
  })

  it('移动中显示 Moving to 与目标坐标', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      action: {
        action: 'move_to',
        action_id: 'act_moving',
        status: 'RUNNING',
        started_at: 1700000000,
        finished_at: null,
        elapsed_ms: 900,
      },
      pathfinder: { goal: 'GoalNear', target: { x: 10.4, y: 64.0, z: -5.6 }, moving: true },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    const moving = wrapper.get('[data-test="mc-moving-to"]').text()
    expect(moving).toContain('Moving to:')
    expect(moving).toContain('10')
    expect(moving).toContain('-6')
    expect(moving).toContain('正在移动')
    expect(wrapper.get('[data-test="mc-action-name"]').text()).toContain('move_to')
    expect(wrapper.get('[data-test="mc-action-status"]').text()).toContain('RUNNING')
  })

  it('离线时 MOVE TO 与坐标输入禁用', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect((wrapper.get('[data-test="mc-move-to"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('[data-test="mc-move-x"]').element as HTMLInputElement).disabled).toBe(true)
  })

  it('坐标非法时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-move-x"]').setValue('')
    await wrapper.get('[data-test="mc-move-to"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/move_to'))).toBe(false)
  })
})

describe('Minecraft 页 · follow_player（Phase 3D）', () => {
  it('FOLLOW 提交玩家名与距离到 /minecraft/follow_player', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-follow-username"]').setValue('空凛')
    await wrapper.get('[data-test="mc-follow-distance"]').setValue('3')
    await wrapper.get('[data-test="mc-follow"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/follow_player'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ username: '空凛', distance: 3 })
  })

  it('跟随中显示 Following 与距离（GoalFollow 诊断）', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      action: {
        action: 'follow_player',
        action_id: 'act_follow_running',
        status: 'RUNNING',
        started_at: 1700000000,
        finished_at: null,
        elapsed_ms: 4200,
      },
      pathfinder: {
        goal: 'GoalFollow',
        target: { username: '空凛', x: 10.4, y: 64.0, z: -5.6 },
        distance: 2.5,
        moving: true,
      },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    const following = wrapper.get('[data-test="mc-following"]').text()
    expect(following).toContain('Following:')
    expect(following).toContain('空凛')
    expect(following).toContain('2.5')
    expect(following).toContain('正在移动')
    expect(wrapper.get('[data-test="mc-action-name"]').text()).toContain('follow_player')
  })

  it('离线时 FOLLOW 与输入禁用；空玩家名本地拦截', async () => {
    const offline = {
      ...ONLINE_OVERVIEW,
      connection: { ...ONLINE_OVERVIEW.connection, status: 'DISCONNECTED' },
    }
    const first = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(offline)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect((first.wrapper.get('[data-test="mc-follow"]').element as HTMLButtonElement).disabled).toBe(true)

    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-follow"]').trigger('click') // 玩家名空
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/follow_player'))).toBe(false)
  })
})
