/** 日志页（W5 §45-§50）：历史服务端过滤参数、实时去重、暂停计数、清空只清本地。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import Logs from '@/pages/system/Logs.vue'
import { installFetch, ok, wait, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { useRealtimeStore } from '@/stores/realtime'

class FakeSocket {
  onopen: ((event: unknown) => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onclose: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  sent: string[] = []

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.onclose?.({})
  }

  open(): void {
    this.onopen?.({})
  }

  emit(frame: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

const originalFetch = globalThis.fetch
let requests: MockRequest[] = []
let pinia: Pinia

function tailRow(overrides: Partial<Record<string, unknown>> = {}): Record<string, unknown> {
  return {
    ts: null,
    time: '12:00:00',
    level: 'INFO',
    logger: 'CatooBot',
    channel: 'world',
    message: '世界开始',
    ...overrides,
  }
}

function handler(request: MockRequest): MockReply {
  if (request.url.includes('/api/v1/logs/channels')) {
    return ok({
      channels: [
        { key: 'warn', icon: '!', label: '异常' },
        { key: 'world', icon: '~', label: '世界' },
      ],
    })
  }
  if (request.url.includes('/api/v1/logs/tail')) {
    return ok({ items: [tailRow()], file: 'logs/catoobot.log', truncated: false, parsed: true, total: 1 })
  }
  return { status: 404, payload: { ok: false, error: { code: 'x', message: `unmocked ${request.url}` } } }
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

function connectRealtime(): FakeSocket {
  const socket = new FakeSocket()
  const realtime = useRealtimeStore()
  realtime.connect({
    createSocket: () => socket,
    setIntervalFn: () => 1,
    clearIntervalFn: () => undefined,
    setTimeoutFn: () => 1,
    clearTimeoutFn: () => undefined,
  })
  socket.open()
  return socket
}

async function mountPage(): Promise<VueWrapper> {
  requests = installFetch(handler)
  const wrapper = mount(Logs, { global: { plugins: [pinia] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  requests = []
})

afterEach(() => {
  useRealtimeStore().disconnect()
  globalThis.fetch = originalFetch
})

describe('Logs 页', () => {
  it('sends the filters to logsTail (server-side filtering) and renders the tail', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="logs-connection"]').text()).toContain('未连接')
    expect(wrapper.get('[data-test="log-row"]').text()).toContain('世界开始')
    expect(wrapper.get('[data-test="system-logs"]').text()).toContain('不会删除服务器日志')
    expect(wrapper.get('[data-test="logs-channel"]').text()).toContain('异常')

    await wrapper.get('[data-test="logs-level"]').setValue('ERROR')
    await wrapper.get('[data-test="logs-channel"]').setValue('warn')
    await wrapper.get('[data-test="logs-limit"]').setValue('100')
    await settle()
    await wrapper.get('[data-test="logs-keyword"]').setValue('boom')
    await wrapper.get('[data-test="logs-keyword"]').trigger('input')
    await wait(350)
    await settle()

    const tail = requests.filter((request) => request.url.includes('/api/v1/logs/tail')).at(-1)
    const url = decodeURIComponent(tail?.url ?? '')
    expect(tail?.method).toBe('GET')
    expect(url).toContain('level=ERROR')
    expect(url).toContain('channel=warn')
    expect(url).toContain('q=boom')
    expect(url).toContain('limit=100')
  })

  it('merges history with the websocket feed and dedupes by ts+message', async () => {
    const socket = connectRealtime()
    const wrapper = await mountPage()

    socket.emit({ topic: 'log', ts: 1700000999, data: { channel: 'warn', level: 'ERROR', message: 'boom' } })
    await settle()

    const rows = wrapper.findAll('[data-test="log-row"]')
    const boomRows = rows.filter((row) => row.get('[data-test="log-message"]').text() === 'boom')
    expect(boomRows).toHaveLength(1)
    // 实时行从 WS 帧解析出 level / channel
    expect(boomRows[0]?.get('[data-test="log-level"]').text()).toBe('ERROR')
    expect(boomRows[0]?.get('[data-test="log-channel"]').text()).toBe('[warn]')

    socket.emit({ topic: 'log', ts: 1700000999, data: { channel: 'warn', level: 'ERROR', message: 'boom' } })
    await settle()

    const again = wrapper
      .findAll('[data-test="log-row"]')
      .filter((row) => row.get('[data-test="log-message"]').text() === 'boom')
    expect(again).toHaveLength(1)
  })

  it('pauses appends while counting, and resumes with the pending rows', async () => {
    const socket = connectRealtime()
    const wrapper = await mountPage()

    await wrapper.get('[data-test="logs-pause"]').trigger('click')
    socket.emit({ topic: 'narration', ts: 1700001001, data: { channel: 'warn', level: 'WARNING', message: 'later' } })
    await settle()

    expect(wrapper.get('[data-test="log-paused"]').text()).toBe('已暂停 · 新消息 1 条')
    expect(
      wrapper.findAll('[data-test="log-row"]').some((row) => row.text().includes('later')),
    ).toBe(false)

    await wrapper.get('[data-test="logs-pause"]').trigger('click')
    await settle()

    expect(wrapper.find('[data-test="log-paused"]').exists()).toBe(false)
    expect(
      wrapper.findAll('[data-test="log-row"]').some((row) => row.text().includes('later')),
    ).toBe(true)
  })
})
