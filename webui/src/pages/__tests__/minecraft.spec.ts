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
  agent: {
    enabled: true,
    context: {
      online: true,
      username: 'Catodayo',
      dimension: 'minecraft:overworld',
      position: { x: 120.5, y: 64, z: -230.5 },
      biome: 'plains',
      players: [{ name: '空凛', distance: 6.4, direction: 'front_right' }],
      current_action: { action: 'follow_player', action_id: 'act_follow_1', status: 'RUNNING' },
      last_action: {
        action: 'move_to',
        action_id: 'act_move_0',
        status: 'FAILED',
        code: 'minecraft.path_not_found',
        error: '无法找到到达目标的非破坏性路径',
        result: null,
        at: 1700000000,
      },
      activity: '',
      updated_at: 1700000000,
    },
    policy: { enabled: true, risk_flags: { SAFE: true, LOW: true }, registered: {} },
    trusted_players: ['RinsoraNeko'],
    confirmations: {
      ttl_seconds: 60,
      max_pending: 32,
      total: 1,
      pending: [
        {
          confirmation_id: 'cfm_test_1',
          session_id: 'private:10001',
          user_id: '10001',
          tool: 'minecraft_test_medium',
          risk: 'MEDIUM',
          arguments_hash: 'abc123',
          arguments: { x: 120, y: 64, z: -230 },
          summary: 'minecraft_test_medium（MEDIUM）：x=120',
          created_at: 1700000000,
          expires_at: 1700000060,
          status: 'PENDING',
        },
      ],
    },
    tools: [
      { name: 'minecraft_chat', risk: 'SAFE', enabled: true, allowed: true, reason: '' },
      { name: 'minecraft_follow_player', risk: 'LOW', enabled: true, allowed: false, reason: 'minecraft.action_busy' },
      { name: 'minecraft_look_at', risk: 'SAFE', enabled: true, allowed: true, reason: '' },
      { name: 'minecraft_move_to', risk: 'LOW', enabled: true, allowed: false, reason: 'minecraft.action_busy' },
      { name: 'minecraft_stop', risk: 'SAFE', enabled: true, allowed: true, reason: '' },
      // 注册表里被关掉的工具（enabled=false）也如实带原因
      { name: 'minecraft_world', risk: 'SAFE', enabled: false, allowed: false, reason: 'tool.disabled' },
    ],
  },
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

const TASK_VIEW = {
  task_id: 'task_abc',
  session_id: 'minecraft:127.0.0.1:25565:空凛',
  origin: 'user',
  source: 'qq',
  objective: '去附近找一棵橡木，挖一块原木并捡回来',
  state: 'RUNNING',
  progress: { completed: 1, total: 3 },
  current_step: {
    step_id: 'step_2',
    tool: 'minecraft_dig',
    risk: 'MEDIUM',
    state: 'WAITING_ACTION',
    arguments: { x: 12, y: 64, z: 9, expected_block: 'oak_log' },
  },
  current_action: 'act_dig_1',
  plan: {
    plan_hash: 'abc123',
    steps: [
      {
        step_id: 'step_1',
        tool: 'minecraft_move_to',
        risk: 'LOW',
        state: 'SUCCEEDED',
        label: '走到 (12,64,9) 附近',
      },
      {
        step_id: 'step_2',
        tool: 'minecraft_dig',
        risk: 'MEDIUM',
        state: 'WAITING_ACTION',
        label: '挖掉 (12,64,9) 的 oak_log',
      },
      {
        step_id: 'step_3',
        tool: 'minecraft_inventory',
        risk: 'SAFE',
        state: 'PENDING',
        label: '重新读一次背包',
      },
    ],
    expected_final_state: { inventory_delta: { oak_log: 1 } },
  },
  confirmation_required: false,
  confirmation_id: null,
  last_result: { tool: 'minecraft_move_to', status: 'SUCCEEDED', summary: '到了', result: {} },
  failure: null,
  verification: {},
  result: {},
  summary: '任务进行中 1/3',
  replans: 0,
  expires_at: 1700000000,
  rollback_supported: false,
  updated_at: 1700000000,
  plan_version: 2,
  plan_status: 'PENDING_CONFIRMATION',
  plan_history: [
    {
      version: 1,
      plan_hash: 'oldhash1',
      created_at: 1700000000,
      summary: '任务：去附近找一棵橡木',
      steps: [],
      confirmed_at: 1700000001,
      superseded_at: 1700000002,
      reason: 'WORLD_CHANGED',
      status: 'SUPERSEDED',
    },
    {
      version: 2,
      plan_hash: 'abc123',
      created_at: 1700000003,
      summary: '任务：去附近找一棵橡木',
      steps: [],
      confirmed_at: 0,
      superseded_at: 0,
      reason: 'WORLD_CHANGED',
      status: 'PENDING_CONFIRMATION',
    },
  ],
  replan_required: false,
  replan_reason: 'WORLD_CHANGED',
  recovery: { reason: 'WORLD_CHANGED', outcome: 'TARGET_ALREADY_DONE', at: 1700000002, detail: {} },
  authorization: null,
  authorization_expired_at: 1700000002,
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

function makeHandler(
  overrides: {
    join?: MockReply
    leave?: MockReply
    dig?: MockReply
    place?: MockReply
    equip?: MockReply
    inventoryMove?: MockReply
    containerInspect?: MockReply
    containerTransfer?: MockReply
    recipeLookup?: MockReply
    craft?: MockReply
    droppedItems?: MockReply
    pickupItem?: MockReply
    digCapability?: MockReply
    findBlocks?: MockReply
    task?: MockReply
    taskAction?: MockReply
  } = {},
) {
  return (request: MockRequest): MockReply => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/minecraft' && request.method === 'GET') {
      return ok(ONLINE_OVERVIEW)
    }
    if (url.pathname === '/api/v1/minecraft/world' && request.method === 'GET') {
      return ok(WORLD_VIEW)
    }
    if (url.pathname === '/api/v1/minecraft/task' && request.method === 'GET') {
      return overrides.task ?? ok({ task: null, session_id: null })
    }
    if (/^\/api\/v1\/minecraft\/task\/[^/]+\/(pause|resume|cancel)$/.test(url.pathname)) {
      return overrides.taskAction ?? ok({ ...TASK_VIEW, state: 'PAUSED' })
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
    if (url.pathname === '/api/v1/minecraft/agent/confirm' && request.method === 'POST') {
      return ok({ created: true, confirmation_id: 'cfm_test_1', status: 'CANCELLED' })
    }
    if (url.pathname === '/api/v1/minecraft/find_blocks' && request.method === 'POST') {
      return (
        overrides.findBlocks ??
        ok({
          ok: true,
          action: 'find_blocks',
          status: 'SUCCEEDED',
          action_id: 'act_find_ui',
          result: {
            ok: true,
            query: { block_names: ['oak_log'], max_distance: 16, max_results: 8 },
            matches: [
              {
                block: { name: 'oak_log' },
                position: { x: 103, y: 64, z: 141 },
                distance: { goal_near: 5, raw: 5.42 },
              },
              {
                block: { name: 'oak_log' },
                position: { x: 100, y: 66, z: 139 },
                distance: { goal_near: 7, raw: 7.9 },
              },
            ],
            truncated: false,
          },
        })
      )
    }
    if (url.pathname === '/api/v1/minecraft/dig_capability' && request.method === 'POST') {
      return (
        overrides.digCapability ??
        ok({
          ok: true,
          action: 'dig_capability',
          status: 'SUCCEEDED',
          action_id: 'act_cap_ui',
          result: {
            ok: true,
            position: { x: 100, y: 64, z: 100 },
            block: { name: 'iron_ore' },
            held_item: { name: 'stone_pickaxe', count: 1 },
            distance: { goal_near: 1, raw: 4.28 },
            can_dig: true,
            dig_time_ms: 1250,
            reason: null,
          },
        })
      )
    }
    if (url.pathname === '/api/v1/minecraft/dig' && request.method === 'POST') {
      return overrides.dig ?? ok({ ok: true, action: 'dig', action_id: 'act_dig_ui', status: 'RUNNING' })
    }
    if (url.pathname === '/api/v1/minecraft/inventory' && request.method === 'GET') {
      return ok({
        ok: true,
        online: true,
        selected_hotbar_slot: 0,
        held_item: { name: 'dirt', count: 12 },
        items: [
          { name: 'dirt', count: 12 },
          { name: 'sand', count: 24 },
        ],
      })
    }
    if (url.pathname === '/api/v1/minecraft/place' && request.method === 'POST') {
      return (
        overrides.place ??
        ok({ ok: true, action: 'place', action_id: 'act_place_ui', status: 'RUNNING' })
      )
    }
    if (url.pathname === '/api/v1/minecraft/inventory/slots' && request.method === 'GET') {
      return ok({
        ok: true,
        online: true,
        hotbar_start: 36,
        inventory_start: 9,
        slots: [
          { slot: 9, name: 'dirt', count: 12, hotbar: false },
          { slot: 10, name: 'sand', count: 24, hotbar: false },
          { slot: 36, name: 'dirt', count: 12, hotbar: true },
        ],
      })
    }
    if (url.pathname === '/api/v1/minecraft/equip' && request.method === 'POST') {
      return (
        overrides.equip ??
        ok({ ok: true, action: 'equip', action_id: 'act_equip_ui', status: 'RUNNING' })
      )
    }
    if (url.pathname === '/api/v1/minecraft/inventory_move' && request.method === 'POST') {
      return (
        overrides.inventoryMove ??
        ok({ ok: true, action: 'inventory_move', action_id: 'act_move_ui', status: 'RUNNING' })
      )
    }
    if (url.pathname === '/api/v1/minecraft/container_inspect' && request.method === 'POST') {
      return (
        overrides.containerInspect ??
        ok({
          ok: true,
          action: 'container_inspect',
          action_id: 'act_cinspect_ui',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            container: {
              type: 'minecraft:chest',
              label: 'Chest',
              position: { x: 100, y: 64, z: 100 },
              size: 27,
            },
            slots: [
              { slot: 0, name: 'dirt', count: 12 },
              { slot: 7, name: 'sand', count: 32 },
            ],
          },
        })
      )
    }
    if (url.pathname === '/api/v1/minecraft/dropped_items' && request.method === 'POST') {
      return (
        overrides.droppedItems ??
        ok({
          ok: true,
          action: 'dropped_items',
          action_id: 'act_dropped_ui',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            online: true,
            total: 2,
            truncated: false,
            items: [
              {
                entity_id: 123,
                item: { name: 'dirt', count: 3 },
                position: { x: 100.35, y: 64.12, z: 101.84 },
                distance: 3.7,
              },
              {
                entity_id: 130,
                item: { name: 'oak_log', count: 1 },
                position: { x: 106.02, y: 64, z: 99.5 },
                distance: 6.1,
              },
            ],
          },
        })
      )
    }
    if (url.pathname === '/api/v1/minecraft/pickup_item' && request.method === 'POST') {
      return (
        overrides.pickupItem ??
        ok({ ok: true, action: 'pickup_item', action_id: 'act_pickup_ui', status: 'RUNNING' })
      )
    }
    if (url.pathname === '/api/v1/minecraft/recipe_lookup' && request.method === 'POST') {
      return (
        overrides.recipeLookup ??
        ok({
          ok: true,
          action: 'recipe_lookup',
          action_id: 'act_recipe_ui',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            item: 'stick',
            status: 'available',
            total: 2,
            recipes: [
              {
                recipe_id: 'stick*4=oak_planks*2',
                result: { name: 'stick', count_per_craft: 4 },
                requires_table: false,
                available: true,
                ingredients: [{ name: 'oak_planks', count: 2 }],
              },
              {
                recipe_id: 'stick*4=birch_planks*2',
                result: { name: 'stick', count_per_craft: 4 },
                requires_table: false,
                available: false,
                ingredients: [{ name: 'birch_planks', count: 2 }],
              },
            ],
          },
        })
      )
    }
    if (url.pathname === '/api/v1/minecraft/craft' && request.method === 'POST') {
      return (
        overrides.craft ??
        ok({ ok: true, action: 'craft', action_id: 'act_craft_ui', status: 'RUNNING' })
      )
    }
    if (url.pathname === '/api/v1/minecraft/container_transfer' && request.method === 'POST') {
      return (
        overrides.containerTransfer ??
        ok({ ok: true, action: 'container_transfer', action_id: 'act_ctransfer_ui', status: 'RUNNING' })
      )
    }
    return fail(404, 'resource.not_found', `未模拟 ${request.method} ${url.pathname}`)
  }
}

//: 挂载过的页面，测试结束统一卸载——页面有 3s 轮询定时器，
//: 不卸载会在环境拆除后继续回调（document is not defined）。
const mounted: VueWrapper[] = []

async function mountPage(
  handler: (request: MockRequest) => MockReply,
): Promise<{ wrapper: VueWrapper; calls: MockRequest[] }> {
  const pinia = useFreshPinia()
  const calls = installFetch(handler)
  const wrapper = mount(Minecraft, { global: { plugins: [pinia] } })
  mounted.push(wrapper)
  await flushAll()
  return { wrapper, calls }
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
  for (const wrapper of mounted.splice(0)) {
    wrapper.unmount()
  }
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

describe('Minecraft 页 · LLM Tool Debug（Phase 3E）', () => {
  it('列出所有 Minecraft 工具的 风险 / 启用 / 是否允许 与 Agent 上下文', async () => {
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(ONLINE_OVERVIEW)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()

    const panel = wrapper.get('[data-test="mc-agent-tools"]')
    expect(panel.text()).toContain('minecraft_world')
    expect(panel.text()).toContain('minecraft_move_to')
    expect(panel.text()).toContain('SAFE')
    expect(panel.text()).toContain('LOW')
    // 被拒的工具要如实显示稳定错误码
    expect(panel.text()).toContain('minecraft.action_busy')
    // 被关掉的工具显示原因而不是「允许」
    expect(wrapper.get('[data-test="mc-agent-tool-minecraft_world"]').text()).toContain(
      'tool.disabled',
    )
    expect(wrapper.get('[data-test="mc-agent-tool-minecraft_stop"]').text()).toContain('允许')

    expect(wrapper.get('[data-test="mc-agent-online"]').text()).toContain('在线')
    expect(wrapper.get('[data-test="mc-agent-dimension"]').text()).toContain('minecraft:overworld')
    expect(wrapper.get('[data-test="mc-agent-position"]').text()).toContain('121')
    expect(wrapper.get('[data-test="mc-agent-players"]').text()).toContain('空凛 6.4 格')
    expect(wrapper.get('[data-test="mc-agent-current"]').text()).toContain('follow_player · RUNNING')
    expect(wrapper.get('[data-test="mc-agent-last"]').text()).toContain('minecraft.path_not_found')
  })

  it('Minecraft 未启用时面板说明所有工具都不可用', async () => {
    const overview = {
      ...ONLINE_OVERVIEW,
      agent: {
        enabled: false,
        context: {},
        policy: {},
        tools: [
          {
          name: 'minecraft_world',
          risk: 'SAFE',
          enabled: false,
          allowed: false,
          reason: 'minecraft.disabled',
        },
        ],
      },
    }
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(overview)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    expect(wrapper.get('[data-test="mc-agent"]').text()).toContain('minecraft.disabled')
  })
})

describe('Minecraft 页 · Dig Test（Phase 4B）', () => {
  it('DIG 提交坐标与方块名到 /minecraft/dig（并带上 expected_block）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-x"]').setValue('120')
    await wrapper.get('[data-test="mc-dig-y"]').setValue('64')
    await wrapper.get('[data-test="mc-dig-z"]').setValue('-230')
    await wrapper.get('[data-test="mc-dig-block"]').setValue('minecraft:stone')
    await wrapper.get('[data-test="mc-dig-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/dig'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      x: 120,
      y: 64,
      z: -230,
      expected_block: 'minecraft:stone',
    })
  })

  it('缺少方块名时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-block"]').setValue('')
    await wrapper.get('[data-test="mc-dig-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/dig'))).toBe(false)
  })

  it('被确认门拒绝时如实报错（不假装成功）', async () => {
    const denied = {
      status: 409,
      body: { code: 'minecraft.confirmation_required', message: '这个 Minecraft 动作需要用户确认' },
    }
    const { wrapper } = await mountPage(
      makeHandler({ dig: fail(denied.status, denied.body.code, denied.body.message) }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-dig-run"]').trigger('click')
    await flushAll()
    expect(wrapper.text()).toContain('需要用户确认')
    expect(wrapper.text()).not.toContain('已开始挖掘')
  })

  it('离线时 DIG 禁用', async () => {
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
    expect((wrapper.get('[data-test="mc-dig-run"]').element as HTMLButtonElement).disabled).toBe(
      true,
    )
  })
})

describe('Minecraft 页 · Dig Test 工具感知（Phase 4I）', () => {
  it('显示当前主手与"未指定工具"状态', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-dig-held"]').text()).toContain('dirt × 12')
    expect(wrapper.get('[data-test="mc-dig-tool-state"]').text()).toContain('未指定工具')
  })

  it('Expected Tool 与主手不一致时明确提示 Held item mismatch（不自动装备）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-tool"]').setValue('minecraft:stone_pickaxe')
    await flushAll()
    expect(wrapper.get('[data-test="mc-dig-tool-state"]').text()).toContain('Held item mismatch')
    // 提示归提示：页面不会自己去调用 equip / 切槽
    expect(calls.some((c) => c.url.endsWith('/minecraft/equip'))).toBe(false)
  })

  it('Expected Tool 与主手一致时显示匹配（minecraft: 前缀不影响判断）', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-tool"]').setValue('dirt')
    await flushAll()
    expect(wrapper.get('[data-test="mc-dig-tool-state"]').text()).toContain('主手匹配')
    await wrapper.get('[data-test="mc-dig-tool"]').setValue('minecraft:dirt')
    await flushAll()
    expect(wrapper.get('[data-test="mc-dig-tool-state"]').text()).toContain('主手匹配')
  })

  it('DIG 填了 Expected Tool 就把它带进请求体', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-x"]').setValue('120')
    await wrapper.get('[data-test="mc-dig-y"]').setValue('64')
    await wrapper.get('[data-test="mc-dig-z"]').setValue('-230')
    await wrapper.get('[data-test="mc-dig-block"]').setValue('minecraft:stone')
    await wrapper.get('[data-test="mc-dig-tool"]').setValue('minecraft:stone_pickaxe')
    await wrapper.get('[data-test="mc-dig-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/dig'))
    expect(call?.body).toEqual({
      x: 120,
      y: 64,
      z: -230,
      expected_block: 'minecraft:stone',
      expected_tool: 'minecraft:stone_pickaxe',
    })
  })

  it('Expected Tool 留空时不带这个字段（4B 的请求形状不变）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-dig-x"]').setValue('120')
    await wrapper.get('[data-test="mc-dig-y"]').setValue('64')
    await wrapper.get('[data-test="mc-dig-z"]').setValue('-230')
    await wrapper.get('[data-test="mc-dig-block"]').setValue('minecraft:stone')
    await wrapper.get('[data-test="mc-dig-tool"]').setValue('   ')
    await wrapper.get('[data-test="mc-dig-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/dig'))
    expect(call?.body).toEqual({ x: 120, y: 64, z: -230, expected_block: 'minecraft:stone' })
  })
})

describe('Minecraft 页 · Find Blocks（Phase 4K · SAFE 只读）', () => {
  it('初始不显示结论', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-find-table"]').text()).toContain('还没有查询过')
    expect(wrapper.get('[data-test="mc-find-summary"]').text()).toContain('只读定位')
  })

  it('FIND 把方块名与上限发给 /minecraft/find_blocks 并列出候选', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue('minecraft:oak_log')
    await wrapper.get('[data-test="mc-find-distance"]').setValue('16')
    await wrapper.get('[data-test="mc-find-results"]').setValue('8')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/find_blocks'))
    expect(call?.body).toEqual({
      block_names: ['minecraft:oak_log'],
      max_distance: 16,
      max_results: 8,
    })
    const table = wrapper.get('[data-test="mc-find-table"]').text()
    expect(table).toContain('oak_log')
    expect(table).toContain('103, 64, 141')
    expect(table).toContain('5 格')
    expect(table).toContain('5.42 格')
  })

  it('多个方块名用逗号/空格分隔', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue(' oak_log , stone  iron_ore ')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/find_blocks'))
    expect(call?.body).toEqual({
      block_names: ['oak_log', 'stone', 'iron_ore'],
      max_distance: 16,
      max_results: 8,
    })
  })

  it('USE AS TARGET 只把坐标填进后续表单（不发任何动作请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue('oak_log')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    const before = calls.length
    await wrapper.get('[data-test="mc-find-use-103-64-141"]').trigger('click')
    await flushAll()
    expect((wrapper.get('[data-test="mc-dig-x"]').element as HTMLInputElement).value).toBe('103')
    expect((wrapper.get('[data-test="mc-dig-y"]').element as HTMLInputElement).value).toBe('64')
    expect((wrapper.get('[data-test="mc-dig-z"]').element as HTMLInputElement).value).toBe('141')
    expect((wrapper.get('[data-test="mc-dig-block"]').element as HTMLInputElement).value).toBe(
      'oak_log',
    )
    expect((wrapper.get('[data-test="mc-capability-x"]').element as HTMLInputElement).value).toBe(
      '103',
    )
    // 只填表单：没有再发任何 move / equip / dig / pickup 请求
    const after = calls.slice(before)
    for (const forbidden of ['/minecraft/move_to', '/minecraft/equip', '/minecraft/dig', '/minecraft/pickup_item']) {
      expect(after.some((c) => c.url.endsWith(forbidden))).toBe(false)
    }
  })

  it('没有结果时如实显示（不是错误）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        findBlocks: ok({
          ok: true,
          action: 'find_blocks',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            query: { block_names: ['oak_log'], max_distance: 16, max_results: 8 },
            matches: [],
            truncated: false,
          },
        }),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue('oak_log')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    expect(wrapper.get('[data-test="mc-find-table"]').text()).toContain('没有找到')
    expect(wrapper.find('[data-test="mc-find-error"]').exists()).toBe(false)
  })

  it('未知方块名如实报错（不假装附近没有）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        findBlocks: fail(422, 'minecraft.block_name_unknown', '不认识的方块名：banana_ore'),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue('banana_ore')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    expect(wrapper.get('[data-test="mc-find-error"]').text()).toContain('不认识的方块名')
  })

  it('缺少方块名本地拦截', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-find-names"]').setValue('   ')
    await wrapper.get('[data-test="mc-find-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/find_blocks'))).toBe(false)
    // 也没有 AUTO GATHER 这类按钮（§四十五）
    expect(wrapper.find('[data-test="mc-find-auto-gather"]').exists()).toBe(false)
  })
})

describe('Minecraft 页 · Dig Capability（Phase 4J · SAFE 只读）', () => {
  it('初始不显示任何结论（还没查过）', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    const facts = wrapper.get('[data-test="mc-capability-facts"]').text()
    expect(facts).toContain('还没有查询过')
    expect(wrapper.get('[data-test="mc-capability-block"]').text()).toBe('-')
  })

  it('CHECK 把整数坐标发给 /minecraft/dig_capability 并显示七项事实', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-capability-x"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-y"]').setValue('64')
    await wrapper.get('[data-test="mc-capability-z"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-check"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/dig_capability'))
    expect(call?.body).toEqual({ x: 100, y: 64, z: 100 })
    expect(wrapper.get('[data-test="mc-capability-block"]').text()).toBe('iron_ore')
    expect(wrapper.get('[data-test="mc-capability-held"]').text()).toContain('stone_pickaxe × 1')
    expect(wrapper.get('[data-test="mc-capability-goal-near"]').text()).toBe('1 格')
    expect(wrapper.get('[data-test="mc-capability-raw"]').text()).toBe('4.28 格')
    expect(wrapper.get('[data-test="mc-capability-can-dig"]').text()).toBe('true')
    expect(wrapper.get('[data-test="mc-capability-dig-time"]').text()).toBe('1250 ms')
    expect(wrapper.get('[data-test="mc-capability-reason"]').text()).toBe('能挖')
  })

  it('挖不动时如实显示原因与 "-" 耗时（不推测工具等级）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        digCapability: ok({
          ok: true,
          action: 'dig_capability',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            position: { x: 100, y: 64, z: 100 },
            block: { name: 'iron_ore' },
            held_item: { name: 'dirt', count: 3 },
            distance: { goal_near: 1, raw: 4.28 },
            can_dig: false,
            dig_time_ms: null,
            reason: 'not_diggable',
          },
        }),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-capability-x"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-y"]').setValue('64')
    await wrapper.get('[data-test="mc-capability-z"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-check"]').trigger('click')
    await flushAll()
    expect(wrapper.get('[data-test="mc-capability-can-dig"]').text()).toBe('false')
    expect(wrapper.get('[data-test="mc-capability-dig-time"]').text()).toBe('-')
    expect(wrapper.get('[data-test="mc-capability-reason"]').text()).toContain('挖不动')
    expect(wrapper.get('[data-test="mc-capability-held"]').text()).toContain('dirt × 3')
  })

  it('空手也是事实（held item 显示"空手"）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        digCapability: ok({
          ok: true,
          action: 'dig_capability',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            position: { x: 100, y: 64, z: 100 },
            block: { name: 'stone' },
            held_item: null,
            distance: { goal_near: 1, raw: 2.1 },
            can_dig: false,
            dig_time_ms: null,
            reason: 'not_diggable',
          },
        }),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-capability-x"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-y"]').setValue('64')
    await wrapper.get('[data-test="mc-capability-z"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-check"]').trigger('click')
    await flushAll()
    expect(wrapper.get('[data-test="mc-capability-held"]').text()).toBe('空手')
  })

  it('非整数坐标本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-capability-x"]').setValue('100.5')
    await wrapper.get('[data-test="mc-capability-y"]').setValue('64')
    await wrapper.get('[data-test="mc-capability-z"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-check"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/dig_capability'))).toBe(false)
  })

  it('没有方块时如实报错，且页面不会自己去装备或挖掘', async () => {
    const { wrapper, calls } = await mountPage(
      makeHandler({
        digCapability: fail(404, 'minecraft.block_unavailable', '那个位置没有方块'),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-capability-x"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-y"]').setValue('64')
    await wrapper.get('[data-test="mc-capability-z"]').setValue('100')
    await wrapper.get('[data-test="mc-capability-check"]').trigger('click')
    await flushAll()
    expect(wrapper.get('[data-test="mc-capability-error"]').text()).toContain('没有方块')
    // 只读面板绝不触发 equip / dig
    expect(calls.some((c) => c.url.endsWith('/minecraft/equip'))).toBe(false)
    expect(calls.some((c) => c.url.endsWith('/minecraft/dig'))).toBe(false)
    // 也没有 AUTO EQUIP / AUTO DIG 这类按钮
    expect(wrapper.find('[data-test="mc-capability-auto-equip"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="mc-capability-auto-dig"]').exists()).toBe(false)
  })
})

describe('Minecraft 页 · Place Test（Phase 4C）', () => {
  it('显示主手物品 / 槽位 / 背包摘要', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-place-held"]').text()).toContain('dirt × 12')
    expect(wrapper.get('[data-test="mc-place-slot"]').text()).toContain('1')
    expect(wrapper.get('[data-test="mc-place-inventory"]').text()).toContain('sand×24')
  })

  it('PLACE 提交坐标 + face + expected_item 到 /minecraft/place', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-place-x"]').setValue('100')
    await wrapper.get('[data-test="mc-place-y"]').setValue('64')
    await wrapper.get('[data-test="mc-place-z"]').setValue('-230')
    await wrapper.get('[data-test="mc-place-face"]').setValue('north')
    await wrapper.get('[data-test="mc-place-item"]').setValue('minecraft:dirt')
    await wrapper.get('[data-test="mc-place-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/place'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      x: 100,
      y: 64,
      z: -230,
      face: 'north',
      expected_item: 'minecraft:dirt',
    })
  })

  it('小数坐标与空物品名本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-place-x"]').setValue('100.5')
    await wrapper.get('[data-test="mc-place-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/place'))).toBe(false)

    await wrapper.get('[data-test="mc-place-x"]').setValue('100')
    await wrapper.get('[data-test="mc-place-item"]').setValue('')
    await wrapper.get('[data-test="mc-place-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/place'))).toBe(false)
  })

  it('用手持物品填入按钮把主手物品写进 expected_item', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-place-item"]').setValue('')
    await wrapper.get('[data-test="mc-place-use-held"]').trigger('click')
    await flushAll()
    expect((wrapper.get('[data-test="mc-place-item"]').element as HTMLInputElement).value).toBe(
      'dirt',
    )
  })

  it('被确认门拒绝时如实报错（不假装成功）', async () => {
    const denied = fail(409, 'minecraft.confirmation_required', '这个 Minecraft 动作需要用户确认')
    const { wrapper } = await mountPage(makeHandler({ place: denied }))
    await flushAll()
    await wrapper.get('[data-test="mc-place-run"]').trigger('click')
    await flushAll()
    expect(wrapper.text()).toContain('需要用户确认')
    expect(wrapper.text()).not.toContain('已开始放置')
  })
})

describe('Minecraft 页 · Inventory Control（Phase 4D）', () => {
  it('EQUIP 把物品名发到 /minecraft/equip', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-equip-item"]').setValue('sand')
    await wrapper.get('[data-test="mc-equip-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/equip'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ item: 'sand' })
  })

  it('EQUIP 缺少物品名时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-equip-item"]').setValue('   ')
    await wrapper.get('[data-test="mc-equip-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/equip'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('缺少物品名')
  })

  it('EQUIP 被确认门拒绝时如实报错（不假装成功）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        equip: fail(409, 'minecraft.confirmation_required', '这个 Minecraft 动作需要用户确认'),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-equip-run"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('EQUIP 被拒绝')
    expect(useToast().items.value.at(-1)?.detail).toContain('需要用户确认')
  })

  it('MOVE 把槽位 / 物品 / 数量发到 /minecraft/inventory_move', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-invmove-source"]').setValue('37')
    await wrapper.get('[data-test="mc-invmove-destination"]').setValue('9')
    await wrapper.get('[data-test="mc-invmove-item"]').setValue('dirt')
    await wrapper.get('[data-test="mc-invmove-count"]').setValue('2')
    await wrapper.get('[data-test="mc-invmove-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/inventory_move'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      source_slot: 37,
      destination_slot: 9,
      item: 'dirt',
      count: 2,
    })
  })

  it('MOVE 在本地就拦住非法槽位 / 数量（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    const run = () => wrapper.get('[data-test="mc-invmove-run"]').trigger('click')
    const set = async (key: string, value: string) => {
      await wrapper.get(`[data-test="${key}"]`).setValue(value)
    }

    await set('mc-invmove-source', '8') // 主背包从 9 开始
    await run()
    await set('mc-invmove-source', '37')
    await set('mc-invmove-destination', '37') // 同一个槽位
    await run()
    await set('mc-invmove-destination', '9')
    await set('mc-invmove-count', '0') // 数量必须 >= 1
    await run()
    await set('mc-invmove-count', '1')
    await set('mc-invmove-item', '') // 物品名必填
    await run()
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/inventory_move'))).toBe(false)
  })

  it('槽位表照 /minecraft/inventory/slots 渲染，点一行填入 source / item', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/inventory/slots'))).toBe(true)
    expect(wrapper.get('[data-test="mc-slot-10"]').text()).toContain('sand')
    await wrapper.get('[data-test="mc-slot-use-10"]').trigger('click')
    await flushAll()
    expect(
      (wrapper.get('[data-test="mc-invmove-source"]').element as HTMLInputElement).value,
    ).toBe('10')
    expect((wrapper.get('[data-test="mc-invmove-item"]').element as HTMLInputElement).value).toBe(
      'sand',
    )
  })

  it('Move Test 的默认值是真实槽位（第一个有物品的槽 + 第一个空槽）', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    // 9 / 10 / 36 被占 → 第一个空槽是 11
    expect(
      (wrapper.get('[data-test="mc-invmove-source"]').element as HTMLInputElement).value,
    ).toBe('9')
    expect(
      (wrapper.get('[data-test="mc-invmove-destination"]').element as HTMLInputElement).value,
    ).toBe('11')
    expect((wrapper.get('[data-test="mc-invmove-count"]').element as HTMLInputElement).value).toBe(
      '1',
    )
  })

  it('Equip 的默认物品不是当前主手（真的能换个东西）', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    // 主手是 dirt → 默认填 sand
    expect((wrapper.get('[data-test="mc-equip-item"]').element as HTMLInputElement).value).toBe(
      'sand',
    )
  })

  it('背包面板如实显示主手与槽位范围', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-inventory-held"]').text()).toContain('dirt')
    expect(wrapper.get('[data-test="mc-inventory-range"]').text()).toContain('9–44')
    expect(wrapper.text()).toContain('一次只动一个物品、一个来源槽、一个目标槽、一个数量')
  })
})

describe('Minecraft 页 · Container（Phase 4E）', () => {
  async function inspectSomeContainer(wrapper: VueWrapper): Promise<void> {
    await wrapper.get('[data-test="mc-container-x"]').setValue('100')
    await wrapper.get('[data-test="mc-container-y"]').setValue('64')
    await wrapper.get('[data-test="mc-container-z"]').setValue('100')
    await wrapper.get('[data-test="mc-container-inspect"]').trigger('click')
    await flushAll()
  }

  it('INSPECT 把坐标发到 /minecraft/container_inspect 并渲染内容', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await inspectSomeContainer(wrapper)
    const call = calls.find((c) => c.url.endsWith('/minecraft/container_inspect'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ x: 100, y: 64, z: 100 })
    expect(wrapper.get('[data-test="mc-container-type"]').text()).toContain('Chest')
    expect(wrapper.get('[data-test="mc-container-size"]').text()).toBe('27')
    expect(wrapper.get('[data-test="mc-container-position"]').text()).toBe('100, 64, 100')
    expect(wrapper.get('[data-test="mc-container-slot-0"]').text()).toContain('dirt')
    expect(wrapper.get('[data-test="mc-container-slot-7"]').text()).toContain('sand')
  })

  it('INSPECT 坐标不是整数时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-container-x"]').setValue('100.5')
    await wrapper.get('[data-test="mc-container-y"]').setValue('64')
    await wrapper.get('[data-test="mc-container-z"]').setValue('100')
    await wrapper.get('[data-test="mc-container-inspect"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/container_inspect'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('容器坐标不合法')
  })

  it('INSPECT 被拒绝时如实报错（不假装读到了）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        containerInspect: fail(422, 'minecraft.container_unsupported', '这个位置不是箱子或桶'),
      }),
    )
    await flushAll()
    await inspectSomeContainer(wrapper)
    expect(useToast().items.value.at(-1)?.message).toBe('INSPECT 被拒绝')
    expect(useToast().items.value.at(-1)?.detail).toContain('不是箱子或桶')
    expect(wrapper.get('[data-test="mc-container-type"]').text()).toBe('—')
  })

  it('INSPECT 后自动填好 Transfer 的 container_slot / item / 空背包格', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await inspectSomeContainer(wrapper)
    // 第一格有东西的容器槽 = 0（dirt）；第一个空背包格 = 11（9/10/36 被占）
    expect((wrapper.get('[data-test="mc-container-slot"]').element as HTMLInputElement).value).toBe(
      '0',
    )
    expect((wrapper.get('[data-test="mc-container-item"]').element as HTMLInputElement).value).toBe(
      'dirt',
    )
    expect(
      (wrapper.get('[data-test="mc-container-inventory-slot"]').element as HTMLInputElement).value,
    ).toBe('11')
  })

  it('容器槽位表点一行 → 填入 container_slot / item', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await inspectSomeContainer(wrapper)
    await wrapper.get('[data-test="mc-container-use-7"]').trigger('click')
    await flushAll()
    expect((wrapper.get('[data-test="mc-container-slot"]').element as HTMLInputElement).value).toBe(
      '7',
    )
    expect((wrapper.get('[data-test="mc-container-item"]').element as HTMLInputElement).value).toBe(
      'sand',
    )
  })

  it('TRANSFER 把 8 个字段发到 /minecraft/container_transfer', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-container-x"]').setValue('100')
    await wrapper.get('[data-test="mc-container-y"]').setValue('64')
    await wrapper.get('[data-test="mc-container-z"]').setValue('100')
    await wrapper.get('[data-test="mc-container-direction"]').setValue('deposit')
    await wrapper.get('[data-test="mc-container-slot"]').setValue('3')
    await wrapper.get('[data-test="mc-container-inventory-slot"]').setValue('9')
    await wrapper.get('[data-test="mc-container-item"]').setValue('sand')
    await wrapper.get('[data-test="mc-container-count"]').setValue('2')
    await wrapper.get('[data-test="mc-container-transfer"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/container_transfer'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      x: 100,
      y: 64,
      z: 100,
      direction: 'deposit',
      container_slot: 3,
      inventory_slot: 9,
      item: 'sand',
      count: 2,
    })
  })

  it('TRANSFER 在本地就拦住非法槽位 / 数量 / 缺物品（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-container-x"]').setValue('100')
    await wrapper.get('[data-test="mc-container-y"]').setValue('64')
    await wrapper.get('[data-test="mc-container-z"]').setValue('100')
    const run = () => wrapper.get('[data-test="mc-container-transfer"]').trigger('click')
    const set = async (key: string, value: string) => {
      await wrapper.get(`[data-test="${key}"]`).setValue(value)
    }

    await set('mc-container-slot', '-1')
    await run()
    await set('mc-container-slot', '0')
    await set('mc-container-inventory-slot', '8') // 主背包从 9 开始
    await run()
    await set('mc-container-inventory-slot', '45') // 副手
    await run()
    await set('mc-container-inventory-slot', '9')
    await set('mc-container-count', '0')
    await run()
    await set('mc-container-count', '1')
    await set('mc-container-item', '')
    await run()
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/container_transfer'))).toBe(false)
  })

  it('TRANSFER 被确认门拒绝时如实报错（WebUI 拿不到执行权）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        containerTransfer: fail(
          409,
          'minecraft.confirmation_required',
          '这个 Minecraft 动作需要用户确认',
        ),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-container-x"]').setValue('100')
    await wrapper.get('[data-test="mc-container-y"]').setValue('64')
    await wrapper.get('[data-test="mc-container-z"]').setValue('100')
    await wrapper.get('[data-test="mc-container-slot"]').setValue('0')
    await wrapper.get('[data-test="mc-container-inventory-slot"]').setValue('9')
    await wrapper.get('[data-test="mc-container-item"]').setValue('dirt')
    await wrapper.get('[data-test="mc-container-transfer"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('TRANSFER 被拒绝')
    expect(useToast().items.value.at(-1)?.detail).toContain('需要用户确认')
  })

  it('未 INSPECT 时如实说「还没有 INSPECT 过容器」', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-container"]').text()).toContain('还没有 INSPECT 过容器')
    expect(wrapper.get('[data-test="mc-container-type"]').text()).toBe('—')
  })
})

describe('Minecraft 页 · Crafting（Phase 4F）', () => {
  async function lookupSomeRecipe(wrapper: VueWrapper, item = 'stick'): Promise<void> {
    await wrapper.get('[data-test="mc-recipe-item"]').setValue(item)
    await wrapper.get('[data-test="mc-recipe-lookup"]').trigger('click')
    await flushAll()
  }

  it('LOOKUP 发送物品名并渲染配方表（recipe_id / 产物 / 材料 / available / table）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await lookupSomeRecipe(wrapper)
    const call = calls.find((c) => c.url.endsWith('/minecraft/recipe_lookup'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ item: 'stick' })
    expect(wrapper.get('[data-test="mc-recipe-status"]').text()).toBe('available')
    expect(wrapper.get('[data-test="mc-recipe-name"]').text()).toBe('stick')
    expect(wrapper.get('[data-test="mc-recipe-total"]').text()).toBe('2/2')
    const row = wrapper.get('[data-test="mc-recipe-stick*4=oak_planks*2"]')
    expect(row.text()).toContain('oak_planks×2')
    expect(row.text()).toContain('stick × 4')
    // 材料不够的那行也列出来，但按钮禁用（不允许点）
    const notReady = wrapper.get('[data-test="mc-recipe-stick*4=birch_planks*2"]')
    expect(notReady.text()).toContain('材料不够')
    expect(
      (wrapper.get('[data-test="mc-recipe-use-stick*4=birch_planks*2"]').element as HTMLButtonElement)
        .disabled,
    ).toBe(true)
  })

  it('LOOKUP 缺物品名时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-recipe-item"]').setValue('   ')
    await wrapper.get('[data-test="mc-recipe-lookup"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/recipe_lookup'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('缺少物品名')
  })

  it('LOOKUP 被拒绝时如实报错（不假装查到了）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        recipeLookup: fail(404, 'minecraft.recipe_not_found', '找不到这个配方'),
      }),
    )
    await flushAll()
    await lookupSomeRecipe(wrapper, 'unobtainium')
    expect(useToast().items.value.at(-1)?.message).toBe('查配方失败')
    expect(useToast().items.value.at(-1)?.detail).toContain('找不到这个配方')
    expect(wrapper.get('[data-test="mc-recipe-status"]').text()).toBe('—')
  })

  it('只有工作台配方时如实提示、表格为空', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        recipeLookup: ok({
          ok: true,
          action: 'recipe_lookup',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            item: 'chest',
            status: 'crafting_table_required',
            total: 0,
            recipes: [],
          },
        }),
      }),
    )
    await flushAll()
    await lookupSomeRecipe(wrapper, 'chest')
    expect(wrapper.get('[data-test="mc-recipe-status"]').text()).toBe('crafting_table_required')
    expect(wrapper.get('[data-test="mc-recipe-table"]').text()).toContain('没有可执行的 2×2 配方')
    expect(useToast().items.value.at(-1)?.message).toBe('需要工作台')
  })

  it('点一行"用作 recipe_id" → 填入 recipe_id 并显示可读摘要', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await lookupSomeRecipe(wrapper)
    await wrapper.get('[data-test="mc-recipe-use-stick*4=oak_planks*2"]').trigger('click')
    await flushAll()
    expect(
      (wrapper.get('[data-test="mc-craft-recipe-id"]').element as HTMLInputElement).value,
    ).toBe('stick*4=oak_planks*2')
    expect(wrapper.get('[data-test="mc-craft-summary"]').text()).toContain(
      '用 2 个 oak_planks 制作 4 个 stick',
    )
  })

  it('CRAFT 把 recipe_id 发到 /minecraft/craft', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-recipe-id"]').setValue('stick*4=oak_planks*2')
    await wrapper.get('[data-test="mc-craft-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/craft'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ recipe_id: 'stick*4=oak_planks*2' })
  })

  it('CRAFT 缺 recipe_id 时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-recipe-id"]').setValue('  ')
    await wrapper.get('[data-test="mc-craft-run"]').trigger('click')
    await flushAll()
    expect(calls.some((c) => c.url.endsWith('/minecraft/craft'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('缺少 recipe_id')
  })

  it('CRAFT 被确认门拒绝时如实报错（WebUI 拿不到执行权）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        craft: fail(409, 'minecraft.confirmation_required', '这个 Minecraft 动作需要用户确认'),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-craft-recipe-id"]').setValue('stick*4=oak_planks*2')
    await wrapper.get('[data-test="mc-craft-run"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('CRAFT 被拒绝')
    expect(useToast().items.value.at(-1)?.detail).toContain('需要用户确认')
  })

  it('切到 Crafting Table 3×3 后：LOOKUP 带上工作台坐标', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-context-mode"]').setValue('table')
    await wrapper.get('[data-test="mc-craft-table-x"]').setValue('100')
    await wrapper.get('[data-test="mc-craft-table-y"]').setValue('64')
    await wrapper.get('[data-test="mc-craft-table-z"]').setValue('100')
    await lookupSomeRecipe(wrapper, 'chest')
    const call = calls.find((c) => c.url.endsWith('/minecraft/recipe_lookup'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      item: 'chest',
      crafting_table: { x: 100, y: 64, z: 100 },
    })
    // 结果里如实显示"在哪张工作台上"（fixture 默认没有 crafting_table → 显示 2×2）
    expect(wrapper.get('[data-test="mc-recipe-table-at"]').text()).toContain('玩家 2×2')
  })

  it('3×3 缺少坐标时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-context-mode"]').setValue('table')
    await wrapper.get('[data-test="mc-craft-table-x"]').setValue('100')
    await wrapper.get('[data-test="mc-craft-table-y"]').setValue('64')
    // z 留空
    await lookupSomeRecipe(wrapper, 'chest')
    expect(calls.some((c) => c.url.endsWith('/minecraft/recipe_lookup'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('缺少工作台坐标')
  })

  it('3×3 坐标不是整数时本地拦截（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-context-mode"]').setValue('table')
    await wrapper.get('[data-test="mc-craft-table-x"]').setValue('100.5')
    await wrapper.get('[data-test="mc-craft-table-y"]').setValue('64')
    await wrapper.get('[data-test="mc-craft-table-z"]').setValue('100')
    await lookupSomeRecipe(wrapper, 'chest')
    expect(calls.some((c) => c.url.endsWith('/minecraft/recipe_lookup'))).toBe(false)
    expect(useToast().items.value.at(-1)?.message).toBe('工作台坐标不合法')
  })

  it('3×3 时 CRAFT 也带上工作台坐标', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-craft-context-mode"]').setValue('table')
    await wrapper.get('[data-test="mc-craft-table-x"]').setValue('100')
    await wrapper.get('[data-test="mc-craft-table-y"]').setValue('64')
    await wrapper.get('[data-test="mc-craft-table-z"]').setValue('100')
    await wrapper.get('[data-test="mc-craft-recipe-id"]').setValue('!chest*1=oak_planks*8')
    await wrapper.get('[data-test="mc-craft-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/craft'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({
      recipe_id: '!chest*1=oak_planks*8',
      crafting_table: { x: 100, y: 64, z: 100 },
    })
  })

  it('2×2 上下文不带 crafting_table 字段（4F 兼容）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await lookupSomeRecipe(wrapper, 'stick')
    const call = calls.find((c) => c.url.endsWith('/minecraft/recipe_lookup'))
    expect(call?.body).toEqual({ item: 'stick' })
  })

  it('3×3 结果里显示工作台坐标', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        recipeLookup: ok({
          ok: true,
          action: 'recipe_lookup',
          status: 'SUCCEEDED',
          result: {
            ok: true,
            item: 'chest',
            crafting_table: { x: 100, y: 64, z: 100 },
            status: 'available',
            total: 1,
            recipes: [
              {
                recipe_id: '!chest*1=oak_planks*8',
                result: { name: 'chest', count_per_craft: 1 },
                requires_table: true,
                available: true,
                ingredients: [{ name: 'oak_planks', count: 8 }],
              },
            ],
          },
        }),
      }),
    )
    await flushAll()
    await lookupSomeRecipe(wrapper, 'chest')
    expect(wrapper.get('[data-test="mc-recipe-table-at"]').text()).toBe('100, 64, 100')
    expect(wrapper.get('[data-test="mc-recipe-!chest*1=oak_planks*8"]').text()).toContain('需要')
  })

  it('未查过配方时如实说"还没有查过配方"', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-craft"]').text()).toContain('还没有查过配方')
    expect(wrapper.get('[data-test="mc-recipe-status"]').text()).toBe('—')
  })
})

describe('Minecraft 页 · Dropped Items / Pickup（Phase 4H）', () => {
  async function refreshDropped(wrapper: VueWrapper): Promise<void> {
    await wrapper.get('[data-test="mc-dropped-refresh"]').trigger('click')
    await flushAll()
  }

  it('REFRESH 拉取掉落物并渲染 entity_id / 物品 / 数量 / 坐标 / 距离', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await refreshDropped(wrapper)
    expect(calls.some((c) => c.url.endsWith('/minecraft/dropped_items'))).toBe(true)
    const row = wrapper.get('[data-test="mc-dropped-123"]')
    expect(row.text()).toContain('123')
    expect(row.text()).toContain('dirt')
    expect(row.text()).toContain('3')
    expect(row.text()).toContain('3.7 格')
    expect(wrapper.get('[data-test="mc-dropped-summary"]').text()).toContain('2 个')
  })

  it('空列表 / 离线时如实显示', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        droppedItems: ok({
          ok: true,
          action: 'dropped_items',
          status: 'SUCCEEDED',
          result: { ok: true, online: true, total: 0, truncated: false, items: [] },
        }),
      }),
    )
    await flushAll()
    await refreshDropped(wrapper)
    expect(wrapper.get('[data-test="mc-dropped-table"]').text()).toContain('附近没有掉落物')
    expect(wrapper.get('[data-test="mc-dropped-summary"]').text()).toBe('附近没有掉落物')
    expect(useToast().items.value.at(-1)?.message).toBe('附近没有掉落物')
  })

  it('刷新失败时如实报错（不假装看到了）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({ droppedItems: fail(500, 'minecraft.action_failed', '读取实体列表失败') }),
    )
    await flushAll()
    await refreshDropped(wrapper)
    expect(useToast().items.value.at(-1)?.message).toBe('刷新掉落物失败')
    expect(useToast().items.value.at(-1)?.detail).toContain('读取实体列表失败')
    expect(wrapper.get('[data-test="mc-dropped-summary"]').text()).toBe('还没有刷新过')
  })

  it('点一行"用作目标" → 填入 entity_id 与物品名', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    await refreshDropped(wrapper)
    await wrapper.get('[data-test="mc-dropped-use-130"]').trigger('click')
    await flushAll()
    expect(
      (wrapper.get('[data-test="mc-pickup-entity-id"]').element as HTMLInputElement).value,
    ).toBe('130')
    expect((wrapper.get('[data-test="mc-pickup-item"]').element as HTMLInputElement).value).toBe(
      'oak_log',
    )
  })

  it('PICKUP 把 entity_id 与 expected_item 一起发到后端', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-pickup-entity-id"]').setValue('123')
    await wrapper.get('[data-test="mc-pickup-item"]').setValue('dirt')
    await wrapper.get('[data-test="mc-pickup-run"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/pickup_item'))
    expect(call).toBeTruthy()
    expect(call?.body).toEqual({ entity_id: 123, expected_item: 'dirt' })
  })

  it('PICKUP 本地拦截非法 entity_id / 缺物品名（不发请求）', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-pickup-entity-id"]').setValue('abc')
    await wrapper.get('[data-test="mc-pickup-item"]').setValue('dirt')
    await wrapper.get('[data-test="mc-pickup-run"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('entity_id 不合法')

    await wrapper.get('[data-test="mc-pickup-entity-id"]').setValue('123')
    await wrapper.get('[data-test="mc-pickup-item"]').setValue('   ')
    await wrapper.get('[data-test="mc-pickup-run"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('缺少 expected_item')
    expect(calls.some((c) => c.url.endsWith('/minecraft/pickup_item'))).toBe(false)
  })

  it('PICKUP 被确认门拒绝时如实报错（WebUI 拿不到执行权）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({
        pickupItem: fail(409, 'minecraft.confirmation_required', '这个 Minecraft 动作需要用户确认'),
      }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-pickup-entity-id"]').setValue('123')
    await wrapper.get('[data-test="mc-pickup-item"]').setValue('dirt')
    await wrapper.get('[data-test="mc-pickup-run"]').trigger('click')
    await flushAll()
    expect(useToast().items.value.at(-1)?.message).toBe('PICKUP 被拒绝')
    expect(useToast().items.value.at(-1)?.detail).toContain('需要用户确认')
  })

  it('未刷新时如实说"还没有刷新过掉落物列表"', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.get('[data-test="mc-dropped"]').text()).toContain('还没有刷新过掉落物列表')
    expect(wrapper.get('[data-test="mc-dropped-summary"]').text()).toBe('还没有刷新过')
  })
})

describe('Minecraft 页 · 确认门（Phase 4A）', () => {
  it('列出待确认动作（工具/风险/目标/用户/状态）与可信玩家', async () => {
    const { wrapper } = await mountPage((request) => {
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname === '/api/v1/minecraft') return ok(ONLINE_OVERVIEW)
      return fail(404, 'resource.not_found', 'no')
    })
    await flushAll()
    const panel = wrapper.get('[data-test="mc-confirmation"]')
    expect(panel.text()).toContain('minecraft_test_medium')
    expect(panel.text()).toContain('MEDIUM')
    expect(panel.text()).toContain('10001')
    expect(panel.text()).toContain('待确认')
    expect(wrapper.get('[data-test="mc-confirmation-ttl"]').text()).toContain('60')
    expect(wrapper.get('[data-test="mc-trusted-players"]').text()).toContain('RinsoraNeko')
  })

  it('CANCEL / EXPIRE 走确认端点，且不带任何「确认」动作', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-confirmation-cancel-cfm_test_1"]').trigger('click')
    await flushAll()
    const cancelCall = calls.find((c) => c.url.endsWith('/minecraft/agent/confirm'))
    expect(cancelCall?.body).toEqual({ action: 'cancel', confirmation_id: 'cfm_test_1' })

    await wrapper.get('[data-test="mc-confirmation-expire-cfm_test_1"]').trigger('click')
    await flushAll()
    const expireCall = calls.filter((c) => c.url.endsWith('/minecraft/agent/confirm')).at(-1)
    expect(expireCall?.body).toEqual({ action: 'expire', confirmation_id: 'cfm_test_1' })
    // 页面从不发送 confirm/consume —— 授权只能由用户在对话里给
    expect(
      calls.some((c) => ['confirm', 'consume'].includes(String((c.body as any)?.action))),
    ).toBe(false)
  })

  it('CREATE TEST CONFIRMATION 只造测试条', async () => {
    const { wrapper, calls } = await mountPage(makeHandler())
    await flushAll()
    await wrapper.get('[data-test="mc-confirmation-create-test"]').trigger('click')
    await flushAll()
    const call = calls.find((c) => c.url.endsWith('/minecraft/agent/confirm'))
    expect(call?.body).toMatchObject({ action: 'create_test' })
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

  // ------------------------------------------------- Phase 5A：多步骤任务

  it('没有任务时给出引导而不是空表', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.find('[data-test="mc-task"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="mc-task-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="mc-task-facts"]').exists()).toBe(false)
  })

  it('有任务时展示目标 / 状态 / 进度 / 步骤 / 授权 / 回滚不可用', async () => {
    const { wrapper } = await mountPage(makeHandler({ task: ok({ task: TASK_VIEW, session_id: 's' }) }))
    await flushAll()
    expect(wrapper.get('[data-test="mc-task-objective"]').text()).toContain('橡木')
    expect(wrapper.get('[data-test="mc-task-source"]').text()).toBe('qq')
    expect(wrapper.get('[data-test="mc-task-state"]').text()).toContain('RUNNING')
    expect(wrapper.get('[data-test="mc-task-progress"]').text()).toContain('1 / 3')
    expect(wrapper.get('[data-test="mc-task-current"]').text()).toContain('minecraft_dig')
    expect(wrapper.get('[data-test="mc-task-action"]').text()).toContain('act_dig_1')
    expect(wrapper.get('[data-test="mc-task-plan-hash"]').text()).toContain('abc123')
    expect(wrapper.get('[data-test="mc-task-rollback"]').text()).toContain('NOT SUPPORTED')
    // 计划逐步展开（每一步一句人话）+ 风险徽标
    const table = wrapper.get('[data-test="mc-task-plan"]').text()
    expect(table).toContain('走到 (12,64,9) 附近')
    expect(table).toContain('挖掉 (12,64,9) 的 oak_log')
    expect(table).toContain('MEDIUM')
    // 面板里绝不出现 raw 世界状态
    expect(wrapper.get('[data-test="mc-task"]').text()).not.toContain('raw')
  })

  it('展示计划版本历史 / 恢复原因 / 重规划次数 / 授权状态（5A.1）', async () => {
    const { wrapper } = await mountPage(
      makeHandler({ task: ok({ task: TASK_VIEW, session_id: 's' }) }),
    )
    await flushAll()
    expect(wrapper.get('[data-test="mc-task-plan-version"]').text()).toContain('v2')
    expect(wrapper.get('[data-test="mc-task-plan-version"]').text()).toContain('PENDING_CONFIRMATION')
    expect(wrapper.get('[data-test="mc-task-replans"]').text()).toBe('0')
    expect(wrapper.get('[data-test="mc-task-replan-reason"]').text()).toContain('WORLD_CHANGED')
    expect(wrapper.get('[data-test="mc-task-recovery"]').text()).toContain('TARGET_ALREADY_DONE')
    expect(wrapper.get('[data-test="mc-task-authorization"]').text()).toContain('已过期')
    // v1 SUPERSEDED 与 v2 PENDING_CONFIRMATION 同时看得见（不能只显示当前计划）
    const history = wrapper.get('[data-test="mc-task-plan-history"]').text()
    expect(history).toContain('v1')
    expect(history).toContain('SUPERSEDED')
    expect(history).toContain('v2')
    expect(history).toContain('PENDING_CONFIRMATION')
    expect(wrapper.get('[data-test="mc-task-plan-v1"]').text()).toContain('WORLD_CHANGED')
  })

  it('PAUSE 调任务端点并在面板上体现新状态', async () => {
    const { wrapper, calls } = await mountPage(
      makeHandler({ task: ok({ task: TASK_VIEW, session_id: 's' }) }),
    )
    await flushAll()
    await wrapper.get('[data-test="mc-task-pause"]').trigger('click')
    await flushAll()
    expect(
      calls.some((call) => call.url.endsWith('/minecraft/task/task_abc/pause')),
    ).toBe(true)
    expect(wrapper.get('[data-test="mc-task-state"]').text()).toContain('PAUSED')
  })

  it('没有任务时 PAUSE / CANCEL 不可用（没有可操作对象）', async () => {
    const { wrapper } = await mountPage(makeHandler())
    await flushAll()
    expect(wrapper.find('[data-test="mc-task-pause"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="mc-task-cancel"]').exists()).toBe(false)
  })
})
