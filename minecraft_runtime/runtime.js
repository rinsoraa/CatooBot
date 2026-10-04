'use strict'
/**
 * CatooBot Minecraft Runtime（Phase 1 · 连接层）。
 *
 * 一个长期存活的 Node 进程，管理恰好一个 mineflayer bot，并把它包成本地
 * HTTP Bridge API（任务书 §CatooBot → Minecraft Bridge API）：
 *
 *   POST /minecraft/connect     { host, port }      → { session_id, status }
 *   GET  /minecraft/status                          → 连接状态 / 基础世界状态
 *   POST /minecraft/chat        { message }         → 让 bot 在服务器里发言
 *   POST /minecraft/disconnect                      → 主动退出
 *   GET  /minecraft/health                          → 进程级存活探针
 *
 * 生命周期是显式状态机（任务书 §生命周期）：
 *   DISCONNECTED → CONNECTING → AUTHENTICATING → CONNECTED → SPAWNING → ONLINE
 *   任意活动态 → DISCONNECTING → DISCONNECTED；失败落 ERROR（可重新 connect）。
 *
 * Bridge 事件通过 HTTP POST 回调推给 CatooBot（MC_CALLBACK_URL +
 * MC_CALLBACK_TOKEN，带退避重试与有界队列）。认证信息只从本地 auth.json
 * 读取，绝不经过 Bridge API 传输，也绝不写进日志。
 *
 * 本进程被设计为「永不因 bot 故障而崩溃」：未捕获异常只记录并转成
 * minecraft.error 事件；HTTP 服务器才是进程的心跳。
 */

const http = require('http')
const path = require('path')
const fs = require('fs')
const mineflayer = require('mineflayer')

// --------------------------------------------------------------- configuration

const RUNTIME_VERSION = 'phase1.0'
const PORT = Number.parseInt(process.env.MC_RUNTIME_PORT || '25580', 10)
const CALLBACK_URL = process.env.MC_CALLBACK_URL || ''
const CALLBACK_TOKEN = process.env.MC_CALLBACK_TOKEN || ''
const AUTH_FILE = process.env.MC_AUTH_FILE || path.join(__dirname, 'auth.json')
const CONNECT_TIMEOUT_S = Number.parseFloat(process.env.MC_CONNECT_TIMEOUT || '75')
const BRIDGE_TOKEN = process.env.MC_BRIDGE_TOKEN || ''

const CHAT_MAX_CHARS = 256
const CONNECT_TIMEOUT_MS = Math.max(5, CONNECT_TIMEOUT_S) * 1000
const EVENT_RETRY_DELAYS_MS = [0, 1000, 2000, 4000, 8000, 16000]
const EVENT_QUEUE_MAX = 200
const CHAT_DEDUPE_TTL_MS = 1000
const HEALTH_LOG_INTERVAL_MS = 60000

// ----------------------------------------------------------------- state machine

const PHASES = [
  'DISCONNECTED',
  'CONNECTING',
  'AUTHENTICATING',
  'CONNECTED',
  'SPAWNING',
  'ONLINE',
  'DISCONNECTING',
  'ERROR',
]
const ACTIVE_PHASES = new Set(['CONNECTING', 'AUTHENTICATING', 'CONNECTED', 'SPAWNING', 'ONLINE'])
const CONNECTING_PHASES = new Set(['CONNECTING', 'AUTHENTICATING'])

const state = {
  phase: 'DISCONNECTED',
  sessionId: null,
  bot: null,
  host: null,
  port: null,
  username: null,
  authMode: null,
  dimension: null,
  position: null,
  health: null,
  lastError: null,
  kickedReason: null,
  connectedAt: null,
  startedAt: Date.now(),
  connectTimer: null,
  stopping: false,
}

function log(level, message, fields) {
  const line = { ts: new Date().toISOString(), level, scope: 'mc-runtime', message, ...fields }
  console.log(JSON.stringify(line))
}

// ------------------------------------------------------------------ event push

// 有界回调队列：CatooBot 暂时够不着时事件先排队，超出上限丢最旧的。
const eventQueue = []
let flushTimer = null
let flushing = false

function pushEvent(event, fields) {
  const payload = {
    event,
    session_id: state.sessionId,
    timestamp: Date.now() / 1000,
    ...fields,
  }
  if (eventQueue.length >= EVENT_QUEUE_MAX) {
    eventQueue.shift()
    log('warn', 'event queue full; dropped oldest event', { event })
  }
  eventQueue.push({ payload, attempts: 0, nextAt: 0 })
  scheduleFlush(0)
}

function scheduleFlush(delayMs) {
  if (flushTimer !== null) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flushEvents()
  }, delayMs)
  if (typeof flushTimer.unref === 'function') flushTimer.unref()
}

async function flushEvents() {
  // 互斥：并发 flush 会把同一条事件发两遍、或把没发过的 shift 掉。
  if (flushing) {
    scheduleFlush(50)
    return
  }
  flushing = true
  try {
    while (eventQueue.length > 0) {
      const item = eventQueue[0]
      const now = Date.now()
      if (item.nextAt > now) {
        scheduleFlush(item.nextAt - now)
        return
      }
      if (!CALLBACK_URL) {
        // 没有回调地址（WebUI 未开或外部托管未配置）：只能丢弃，靠轮询兜底。
        eventQueue.shift()
        log('warn', 'callback url not configured; event dropped', {
          event: item.payload.event,
        })
        continue
      }
      try {
        const headers = { 'Content-Type': 'application/json' }
        if (CALLBACK_TOKEN) headers.Authorization = `Bearer ${CALLBACK_TOKEN}`
        const response = await fetch(CALLBACK_URL, {
          method: 'POST',
          headers,
          body: JSON.stringify(item.payload),
          signal: AbortSignal.timeout(5000),
        })
        if (!response.ok) throw new Error(`callback answered ${response.status}`)
        eventQueue.shift()
        item.attempts = 0
      } catch (error) {
        item.attempts += 1
        if (item.attempts >= EVENT_RETRY_DELAYS_MS.length) {
          eventQueue.shift()
          log('error', 'event delivery failed permanently; dropped', {
            event: item.payload.event,
            error: String(error && error.message ? error.message : error),
          })
          continue
        }
        const retryDelay = EVENT_RETRY_DELAYS_MS[item.attempts]
        item.nextAt = Date.now() + retryDelay
        scheduleFlush(retryDelay)
        return
      }
    }
  } finally {
    flushing = false
  }
  // 排队期间又有新事件进来 → 再跑一轮。
  if (eventQueue.length > 0) scheduleFlush(0)
}

// ----------------------------------------------------------------- auth（本地文件）

function readAuth() {
  // 认证信息只在本地：auth.json 不进 Git、不经 HTTP 传输。读不到就用
  // offline 默认身份，让用户第一眼就能连上 offline 服务器。
  try {
    const raw = fs.readFileSync(AUTH_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    const mode = parsed.mode === 'microsoft' ? 'microsoft' : 'offline'
    if (mode === 'microsoft') {
      return {
        mode,
        email: String(parsed.email || parsed.username || ''),
        password: String(parsed.password || ''),
      }
    }
    return { mode, username: String(parsed.username || 'GuanTou') }
  } catch {
    return { mode: 'offline', username: 'GuanTou' }
  }
}

// ------------------------------------------------------------------- bot wiring

function describeReason(reason) {
  if (reason == null) return ''
  if (typeof reason === 'string') return reason
  try {
    return JSON.stringify(reason)
  } catch {
    return String(reason)
  }
}

const recentChats = new Map() // "username\u0000message" → ts

function seenChatBefore(key) {
  const now = Date.now()
  for (const [existing, ts] of recentChats) {
    if (now - ts > CHAT_DEDUPE_TTL_MS) recentChats.delete(existing)
  }
  const previous = recentChats.get(key)
  recentChats.set(key, now)
  return previous !== undefined
}

function wireBot(bot) {
  bot.on('login', () => {
    state.username = bot.username
    recentChats.clear() // 新会话从零开始，旧会话的去重记录不能吞掉新消息
    if (CONNECTING_PHASES.has(state.phase)) {
      state.phase = 'CONNECTED'
      log('info', 'login ok', { username: state.username })
      pushEvent('minecraft.connected', { username: state.username })
    }
  })

  bot.on('spawn', () => {
    const firstSpawn = state.phase !== 'ONLINE'
    state.phase = 'ONLINE'
    state.lastError = null
    state.kickedReason = null
    state.connectedAt = Date.now() / 1000
    captureWorldState(bot)
    log('info', 'spawned', { dimension: state.dimension, position: state.position })
    if (firstSpawn) pushEvent('minecraft.spawned', { username: state.username })
  })

  bot.on('move', () => {
    if (state.phase === 'ONLINE' && bot.entity) captureWorldState(bot)
  })

  bot.on('health', () => {
    if (state.phase === 'ONLINE') state.health = typeof bot.health === 'number' ? bot.health : null
  })

  bot.on('chat', (username, message) => {
    if (typeof username !== 'string' || typeof message !== 'string') return
    if (username === bot.username) return // 自己的话由服务器回声，不再上报
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message })
  })

  bot.on('whisper', (username, message) => {
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message, private: true })
  })

  // 兜底：非原版聊天格式的服务器不会触发 'chat'，但 'message' 一定有。
  bot.on('message', (jsonMsg) => {
    if (state.phase !== 'ONLINE') return
    const text = typeof jsonMsg?.toString === 'function' ? jsonMsg.toString() : ''
    if (!text) return
    // 加入/离开提示属于系统行（player_joined / player_left 已单独上报），
    // 不该混进 chat 事件。
    if (/(?:joined|left) the game\.?\s*$/i.test(text)) return
    const match = /^<(.{1,16}?)>\s?([\s\S]*)$/.exec(text)
    const username = match ? match[1] : ''
    const message = match ? match[2] : text
    if (username === bot.username) return
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message, raw: text })
  })

  bot.on('playerJoined', (player) => {
    pushEvent('minecraft.player_joined', { username: player?.username ?? null })
  })

  bot.on('playerLeft', (player) => {
    pushEvent('minecraft.player_left', { username: player?.username ?? null })
  })

  bot.on('kicked', (reason) => {
    state.kickedReason = describeReason(reason)
    log('warn', 'kicked', { reason: state.kickedReason })
    pushEvent('minecraft.kicked', { reason: state.kickedReason, username: state.username })
  })

  bot.on('error', (error) => {
    state.lastError = String(error && error.message ? error.message : error)
    state.phase = 'ERROR'
    clearConnectTimer()
    log('error', 'bot error', { error: state.lastError })
    pushEvent('minecraft.error', { error: state.lastError })
  })

  bot.on('end', () => {
    clearConnectTimer()
    const wasActive = state.bot === bot
    state.bot = null
    if (!wasActive && !state.stopping) return // 旧实例的余波
    if (state.phase !== 'ERROR') state.phase = 'DISCONNECTED'
    state.dimension = null
    state.position = null
    state.health = null
    log('info', 'bot ended', { phase: state.phase })
    pushEvent('minecraft.disconnected', {
      username: state.username,
      reason: state.kickedReason || state.lastError || null,
    })
  })
}

function captureWorldState(bot) {
  state.dimension = bot.game?.dimension ?? null
  const position = bot.entity?.position
  state.position =
    position && typeof position.x === 'number'
      ? {
          x: Math.round(position.x * 100) / 100,
          y: Math.round(position.y * 100) / 100,
          z: Math.round(position.z * 100) / 100,
        }
      : null
  state.health = typeof bot.health === 'number' ? bot.health : null
}

function clearConnectTimer() {
  if (state.connectTimer !== null) {
    clearTimeout(state.connectTimer)
    state.connectTimer = null
  }
}

// ----------------------------------------------------------------- lifecycle ops

class BridgeError extends Error {
  constructor(message, code) {
    super(message)
    this.code = code
  }
}

function startConnect(host, port) {
  if (state.stopping) {
    throw new BridgeError('runtime 正在关闭，暂不接受新的连接', 'runtime.stopping')
  }
  if (ACTIVE_PHASES.has(state.phase)) {
    throw new BridgeError('已有 bot 会话在运行，先 disconnect 再重新 connect', 'session.active')
  }
  const auth = readAuth()
  state.sessionId = `mc_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
  state.host = host
  state.port = port
  state.authMode = auth.mode
  state.username = null
  state.dimension = null
  state.position = null
  state.health = null
  state.lastError = null
  state.kickedReason = null
  state.phase = auth.mode === 'microsoft' ? 'AUTHENTICATING' : 'CONNECTING'

  const options = { host, port, auth: auth.mode, hideErrors: true }
  if (auth.mode === 'microsoft') {
    options.username = auth.email
    if (auth.password) options.password = auth.password
  } else {
    options.username = auth.username
  }

  log('info', 'connecting', { host, port, auth_mode: auth.mode, session_id: state.sessionId })
  pushEvent('minecraft.connecting', { host, port })

  try {
    state.bot = mineflayer.createBot(options)
  } catch (error) {
    state.phase = 'ERROR'
    state.lastError = String(error && error.message ? error.message : error)
    pushEvent('minecraft.error', { error: state.lastError })
    throw new BridgeError(`无法启动 Minecraft 连接：${state.lastError}`, 'connect.failed')
  }
  wireBot(state.bot)

  // 连接看门狗：限时未进世界 → 报错并清理，绝不卡在中间态。
  state.connectTimer = setTimeout(() => {
    if (state.phase === 'ONLINE' || state.phase === 'DISCONNECTED' || state.phase === 'ERROR') return
    const message = `连接超时：${CONNECT_TIMEOUT_S}s 内没有进入世界（host=${host}:${port}）`
    log('error', message)
    state.lastError = message
    state.phase = 'ERROR'
    pushEvent('minecraft.error', { error: message })
    try {
      state.bot?.quit()
      state.bot?.end()
    } catch {
      state.bot = null
    }
  }, CONNECT_TIMEOUT_MS)
  if (typeof state.connectTimer.unref === 'function') state.connectTimer.unref()

  return { session_id: state.sessionId, status: state.phase }
}

function requestDisconnect() {
  clearConnectTimer()
  if (state.bot !== null) {
    state.stopping = false
    if (ACTIVE_PHASES.has(state.phase)) state.phase = 'DISCONNECTING'
    const bot = state.bot
    try {
      bot.quit()
    } catch {
      try {
        bot.end()
      } catch {
        state.bot = null
        if (state.phase === 'DISCONNECTING') state.phase = 'DISCONNECTED'
      }
    }
    return true
  }
  // 幂等：没有 bot 时直接落回 DISCONNECTED（ERROR 也要能被 disconnect 清掉）。
  if (state.phase !== 'DISCONNECTED') {
    state.phase = 'DISCONNECTED'
    state.lastError = null
  }
  return false
}

function statusPayload() {
  return {
    runtime_version: RUNTIME_VERSION,
    status: state.phase,
    session_id: state.sessionId,
    host: state.host,
    port: state.port,
    username: state.username,
    auth_mode: state.authMode,
    dimension: state.dimension,
    position: state.position,
    health: state.health,
    last_error: state.lastError,
    kicked_reason: state.kickedReason,
    connected_at: state.connectedAt,
    uptime_seconds: Math.round((Date.now() - state.startedAt) / 1000),
  }
}

// ------------------------------------------------------------------ HTTP server

const MAX_BODY_BYTES = 1 << 16

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    request.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY_BYTES) {
        reject(new BridgeError('请求体过大', 'request.too_large'))
        request.destroy()
        return
      }
      chunks.push(chunk)
    })
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw) {
        resolve({})
        return
      }
      try {
        const parsed = JSON.parse(raw)
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          reject(new BridgeError('请求体必须是 JSON 对象', 'request.malformed'))
          return
        }
        resolve(parsed)
      } catch (error) {
        reject(new BridgeError(`JSON 解析失败：${error.message}`, 'request.malformed'))
      }
    })
    request.on('error', () => reject(new BridgeError('读取请求体失败', 'request.malformed')))
  })
}

function jsonResponse(response, status, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  })
  response.end(body)
}

function parseTarget(body) {
  const host = String(body.host ?? '').trim()
  if (!host) throw new BridgeError('缺少 host', 'target.invalid')
  if (host.length > 253) throw new BridgeError('host 过长', 'target.invalid')
  const rawPort = body.port === undefined || body.port === null || body.port === '' ? 25565 : body.port
  const port = Number(rawPort)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new BridgeError('port 必须是 1-65535 的整数', 'target.invalid')
  }
  return { host, port }
}

const server = http.createServer((request, response) => {
  void handleRequest(request, response)
})

async function handleRequest(request, response) {
  const path = (request.url || '/').split('?')[0]
  try {
    if (BRIDGE_TOKEN) {
      const supplied = request.headers.authorization || ''
      if (supplied !== `Bearer ${BRIDGE_TOKEN}`) {
        jsonResponse(response, 401, { ok: false, error: { code: 'auth.invalid', message: 'bridge token 无效' } })
        return
      }
    }
    if (request.method === 'GET' && path === '/minecraft/health') {
      jsonResponse(response, 200, { ok: true, uptime_seconds: Math.round((Date.now() - state.startedAt) / 1000) })
      return
    }
    if (request.method === 'GET' && path === '/minecraft/status') {
      jsonResponse(response, 200, { ok: true, ...statusPayload() })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/connect') {
      const body = await readBody(request)
      const { host, port } = parseTarget(body)
      const result = startConnect(host, port)
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/chat') {
      const body = await readBody(request)
      const message = String(body.message ?? '')
      if (!message.trim()) {
        throw new BridgeError('message 不能为空', 'chat.empty')
      }
      if (message.length > CHAT_MAX_CHARS) {
        throw new BridgeError(`message 超过 ${CHAT_MAX_CHARS} 字符上限`, 'chat.too_long')
      }
      if (state.phase !== 'ONLINE' || state.bot === null) {
        throw new BridgeError('bot 不在线，无法发言', 'chat.not_online')
      }
      state.bot.chat(message)
      jsonResponse(response, 200, { ok: true, sent: true })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/disconnect') {
      await readBody(request).catch(() => ({}))
      const hadBot = requestDisconnect()
      jsonResponse(response, 200, { ok: true, status: state.phase, had_bot: hadBot })
      return
    }
    jsonResponse(response, 404, { ok: false, error: { code: 'route.unknown', message: `未知路由 ${request.method} ${path}` } })
  } catch (error) {
    if (error instanceof BridgeError) {
      jsonResponse(response, error.code === 'session.active' ? 409 : 400, {
        ok: false,
        error: { code: error.code, message: error.message },
      })
      return
    }
    log('error', 'bridge request failed', { error: String(error && error.stack ? error.stack : error) })
    jsonResponse(response, 500, { ok: false, error: { code: 'internal.error', message: 'runtime 内部错误' } })
  }
}

// ------------------------------------------------------------------ resilience

process.on('uncaughtException', (error) => {
  // bot 层的意外异常绝不带走进程：记录、上报、继续服务。
  const message = String(error && error.message ? error.message : error)
  log('error', 'uncaught exception', { error: message })
  try {
    state.lastError = message
    pushEvent('minecraft.error', { error: message, uncaught: true })
  } catch {
    /* 事件通道坏了也要活 */
  }
})

process.on('unhandledRejection', (reason) => {
  const message = String(reason instanceof Error ? reason.message : reason)
  log('error', 'unhandled rejection', { error: message })
})

function shutdown(signal) {
  log('info', 'shutting down', { signal })
  state.stopping = true
  clearConnectTimer()
  if (state.bot !== null) {
    try {
      state.bot.quit()
    } catch {
      /* 忽略 */
    }
  }
  // 先关 HTTP 服务器再退出；兜底定时器不 unref，保证进程一定死。
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 1500)
}

process.on('SIGINT', () => shutdown('SIGINT'))
process.on('SIGTERM', () => shutdown('SIGTERM'))

server.listen(PORT, '127.0.0.1', () => {
  log('info', 'minecraft runtime listening', {
    port: PORT,
    callback: CALLBACK_URL ? 'configured' : 'none (events poll-only)',
    auth_file: AUTH_FILE,
  })
})

server.on('error', (error) => {
  // 端口被占/权限问题：HTTP 起不来进程就没有存在意义，如实退出让上层重启。
  log('error', 'http server error; exiting', { error: String(error && error.message ? error.message : error) })
  process.exit(1)
})
