/**
 * 实时通道（§14、§17）：唯一 WebSocket 的封装。
 *
 * 连接 / 断线重连（指数退避）/ 心跳 / topic 分发都只在这里发生；
 * 其他 Store 与组件不得各自创建 WebSocket。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { useRuntimeStore } from '@/stores/runtime'
import type { ConnectionState, RealtimeEvent, RealtimeLogEntry, RealtimeTopic } from '@/types/runtime'

const MAX_FEED = 120
const HEARTBEAT_MS = 25_000
const BACKOFF_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]

export function realtimeUrl(location?: { protocol: string; host: string }): string {
  const source = location ?? (typeof window !== 'undefined' ? window.location : undefined)
  if (!source) return '/ws/events'
  const scheme = source.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${source.host}/ws/events`
}

interface SocketLike {
  close(): void
  send(data: string): void
  onopen: ((event: unknown) => void) | null
  onmessage: ((event: { data: unknown }) => void) | null
  onclose: ((event: unknown) => void) | null
  onerror: ((event: unknown) => void) | null
}

export interface RealtimeOptions {
  /** 注入点：测试传假 socket，生产用全局 WebSocket。 */
  createSocket?: (url: string) => SocketLike
  /** 注入点：测试里避免真实定时器。 */
  setTimeoutFn?: (handler: () => void, ms: number) => number
  clearTimeoutFn?: (handle: number) => void
  setIntervalFn?: (handler: () => void, ms: number) => number
  clearIntervalFn?: (handle: number) => void
}

export const useRealtimeStore = defineStore('realtime', () => {
  const state = ref<ConnectionState>('closed')
  const lastMessageAt = ref(0)
  const reconnectCount = ref(0)
  const connectionError = ref('')
  const feed = ref<RealtimeLogEntry[]>([])
  const topics = ref<Record<string, number>>({})
  const hub = ref<Record<string, unknown> | null>(null)

  const connected = computed(() => state.value === 'connected')
  const label = computed(() => {
    if (state.value === 'connected') return '已连接'
    if (state.value === 'connecting') return '连接中…'
    if (state.value === 'reconnecting') return '重连中…'
    return '未连接'
  })

  let socket: SocketLike | null = null
  let heartbeat: number | null = null
  let retryTimer: number | null = null
  let attempt = 0
  let options: RealtimeOptions = {}
  let timers = {
    setTimeout: ((handler: () => void, ms: number) => setTimeout(handler, ms) as unknown as number) as RealtimeOptions['setTimeoutFn'],
    clearTimeout: ((handle: number) => clearTimeout(handle)) as RealtimeOptions['clearTimeoutFn'],
    setInterval: ((handler: () => void, ms: number) => setInterval(handler, ms) as unknown as number) as RealtimeOptions['setIntervalFn'],
    clearInterval: ((handle: number) => clearInterval(handle)) as RealtimeOptions['clearIntervalFn'],
  }

  function note(topic: RealtimeTopic, text: string, ts: number): void {
    feed.value = [{ ts, topic, text }, ...feed.value].slice(0, MAX_FEED)
  }

  function dispatch(event: RealtimeEvent): void {
    lastMessageAt.value = Date.now()
    topics.value = { ...topics.value, [event.topic]: (topics.value[event.topic] ?? 0) + 1 }
    const runtime = useRuntimeStore()
    switch (event.topic) {
      case 'hello':
        hub.value = (event.data ?? {}) as Record<string, unknown>
        break
      case 'status':
        runtime.applyStatus((event.data ?? {}) as Record<string, unknown>)
        break
      case 'world':
        runtime.applyWorld((event.data ?? {}) as Record<string, unknown>)
        note('world', describeWorld(event.data), event.ts)
        break
      case 'scheduler':
        runtime.applyScheduler((event.data ?? {}) as Record<string, unknown>)
        break
      case 'log':
      case 'narration':
        note(event.topic, describeLog(event.data), event.ts)
        break
      default:
        break
    }
  }

  function connect(overrides: RealtimeOptions = {}): void {
    options = { ...options, ...overrides }
    timers = {
      setTimeout: (options.setTimeoutFn ?? timers.setTimeout) as RealtimeOptions['setTimeoutFn'],
      clearTimeout: (options.clearTimeoutFn ?? timers.clearTimeout) as RealtimeOptions['clearTimeoutFn'],
      setInterval: (options.setIntervalFn ?? timers.setInterval) as RealtimeOptions['setIntervalFn'],
      clearInterval: (options.clearIntervalFn ?? timers.clearInterval) as RealtimeOptions['clearIntervalFn'],
    }
    if (socket) return
    const factory: (url: string) => SocketLike =
      options.createSocket ??
      ((url: string) => {
        const Ctor = (globalThis as { WebSocket?: new (target: string) => SocketLike }).WebSocket
        if (!Ctor) throw new Error('WebSocket 不可用')
        return new Ctor(url)
      })
    state.value = attempt === 0 ? 'connecting' : 'reconnecting'
    let created: SocketLike
    try {
      created = factory(realtimeUrl())
    } catch (error) {
      connectionError.value = error instanceof Error ? error.message : String(error)
      scheduleReconnect()
      return
    }
    socket = created
    created.onopen = () => {
      state.value = 'connected'
      attempt = 0
      connectionError.value = ''
      if (heartbeat !== null) timers.clearInterval?.(heartbeat)
      heartbeat = timers.setInterval?.(() => {
        try {
          socket?.send(JSON.stringify({ type: 'ping', ts: Date.now() }))
        } catch {
          /* the close handler will take over */
        }
      }, HEARTBEAT_MS) as number
    }
    created.onmessage = (event) => {
      let parsed: RealtimeEvent | null = null
      try {
        const raw = typeof event.data === 'string' ? event.data : String(event.data)
        parsed = JSON.parse(raw) as RealtimeEvent
      } catch {
        parsed = null
      }
      if (parsed && typeof parsed.topic === 'string') dispatch(parsed)
    }
    created.onclose = () => {
      cleanupSocket()
      if (state.value !== 'closed') scheduleReconnect()
    }
    created.onerror = () => {
      connectionError.value = '实时连接出错'
    }
  }

  function cleanupSocket(): void {
    if (heartbeat !== null) {
      timers.clearInterval?.(heartbeat)
      heartbeat = null
    }
    socket = null
  }

  function scheduleReconnect(): void {
    state.value = 'reconnecting'
    reconnectCount.value += 1
    const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] ?? 30_000
    attempt += 1
    if (retryTimer !== null) timers.clearTimeout?.(retryTimer)
    retryTimer = timers.setTimeout?.(() => {
      retryTimer = null
      connect()
    }, wait) as number
  }

  function disconnect(): void {
    state.value = 'closed'
    if (retryTimer !== null) {
      timers.clearTimeout?.(retryTimer)
      retryTimer = null
    }
    const current = socket
    cleanupSocket()
    try {
      current?.close()
    } catch {
      /* already gone */
    }
  }

  function clearFeed(): void {
    feed.value = []
  }

  return {
    state,
    connected,
    label,
    lastMessageAt,
    reconnectCount,
    connectionError,
    feed,
    topics,
    hub,
    connect,
    disconnect,
    dispatch,
    clearFeed,
  }
})

function describeLog(data: unknown): string {
  if (typeof data === 'string') return data
  if (data && typeof data === 'object') {
    const record = data as { message?: unknown; level?: unknown; channel?: unknown }
    const channel = record.channel ? `[${String(record.channel)}] ` : ''
    const level = record.level ? `(${String(record.level)}) ` : ''
    return `${channel}${level}${String(record.message ?? JSON.stringify(record))}`
  }
  return String(data ?? '')
}

function describeWorld(data: unknown): string {
  if (data && typeof data === 'object') {
    const record = data as { action?: { name?: string | null }; location?: string | null; phase?: string }
    const place = record.location ? ` @${record.location}` : ''
    const doing = record.action?.name ?? record.phase ?? '状态变化'
    return `${doing}${place}`
  }
  return '世界状态变化'
}
