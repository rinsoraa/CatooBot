/** Runtime 页（W5 §51-§58）：各卡片渲染、tick 需确认、错误重试。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import Runtime from '@/pages/system/Runtime.vue'
import { fail, installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { useRealtimeStore } from '@/stores/realtime'
import type { OverviewData } from '@/types/runtime'

const OVERVIEW: OverviewData = {
  qq: { online: true, self_id: 10001 },
  ai: { enabled: true, current_model: 'planner-x', models_ok: 1, models_total: 2, requests: 5, errors: 1, rate_limited: 0 },
  world: {
    phase: 'day',
    location: 'home',
    action: { name: 'reading', progress: 0.5 },
    modes: ['normal'],
    world_revision: 7,
    cognitive_revision: 3,
    interrupted: false,
    session: { active: false, person_id: null },
  },
  runtime: { scheduler: { running: true, interval_seconds: 30, ticks: 9, catchups: 0, last_tick_at: 1700000000 } },
  counts: { memories: 10, experiences: 2, goals_open: 2, commitments_open: 1 },
}

const RUNTIME = {
  scheduler: { running: true, interval_seconds: 30, ticks: 9, catchups: 1, last_tick_at: 1700000000, last_report: { minutes: 15, events: 2 } },
  watchdog: { last_lag_ms: 1, max_lag_ms: 5, lag_events: 0 },
  database: { connected: true, size_bytes: 2048 },
  hub: { subscribers: 1, published: 10, dropped: 0, queue_size: 0 },
  process: { uptime_seconds: 3661, started_at: 1700000000, version: '1.0.0', python: '3.12.0' },
  onebot: {
    state: 'running',
    connected: true,
    self_id: 10001,
    last_event_at: 1700000000,
    received: 5,
    accepted: 5,
    deduped: 0,
    dropped: 0,
    self_ignored: 0,
    responses: 4,
    sent: 4,
    failed: 0,
    pending_outbound: 0,
    busy: false,
    lanes: [{ lane: 'default', pending: 0, busy: false }],
  },
}

const originalFetch = globalThis.fetch
let requests: MockRequest[] = []
let pinia: Pinia
let failFirst = false

function handler(request: MockRequest): MockReply {
  if (request.url.includes('/api/v1/runtime/tick')) {
    return ok({ ran: true, minutes: 15, report: { minutes: 15, events: 2 }, ticks: 10 })
  }
  if (request.url.includes('/api/v1/runtime/actions/')) {
    return ok({ done: true, action: 'reload_persona', detail: 'persona=Yuki' })
  }
  if (failFirst) return fail(500, 'internal.error', '运行时不稳')
  if (request.url.endsWith('/api/v1/overview')) return ok(OVERVIEW)
  if (request.url.endsWith('/api/v1/runtime')) return ok(RUNTIME)
  return fail(404, 'not_found', `unmocked ${request.url}`)
}

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  requests = installFetch(handler)
  const wrapper = mount(Runtime, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  failFirst = false
})

afterEach(() => {
  useRealtimeStore().disconnect()
  globalThis.fetch = originalFetch
})

describe('Runtime 页', () => {
  it('renders process, scheduler, world, onebot, ai, database and websocket cards', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="runtime-process"]').text()).toContain('1.0.0')
    expect(wrapper.get('[data-test="runtime-process"]').text()).toContain('3.12.0')
    expect(wrapper.get('[data-test="runtime-process"]').text()).toContain('小时')

    expect(wrapper.get('[data-test="runtime-scheduler"]').text()).toContain('9')
    expect(wrapper.get('[data-test="scheduler-note"]').text()).toContain(
      'interval 是调度器唤醒频率，不代表世界每 N 秒更新一次',
    )

    expect(wrapper.get('[data-test="runtime-world"]').text()).toContain('reading')
    expect(wrapper.get('[data-test="world-interrupted"]').text()).toContain('当前没有未完成的打断')

    expect(wrapper.get('[data-test="onebot-lanes"]').text()).toContain('default')
    expect(wrapper.get('[data-test="runtime-ai"]').text()).toContain('planner-x')
    expect(wrapper.get('[data-test="database-size"]').text()).toBe('2.0 KB')
    expect(wrapper.get('[data-test="ws-connection"]').text()).toContain('未连接')
  })

  it('requires a confirm before a manual world tick and shows the report', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="runtime-tick"]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('真实调用')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const tick = requests.find((request) => request.method === 'POST')
    expect(tick?.url).toContain('/api/v1/runtime/tick')
    expect(wrapper.get('[data-test="tick-report"]').text()).toContain('15')
  })

  it('confirms each runtime action before calling it', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="runtime-action-reload_persona"]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('重载角色人设')

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const action = requests.find((request) => request.method === 'POST')
    expect(action?.url).toContain('/api/v1/runtime/actions/reload_persona')
  })

  it('shows an ErrorState with retry when loading fails', async () => {
    failFirst = true
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('运行时不稳')
    failFirst = false

    await wrapper.get('[data-test="retry"]').trigger('click')
    await settle()

    expect(wrapper.find('[data-test="retry"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="runtime-process"]').text()).toContain('1.0.0')
  })
})
