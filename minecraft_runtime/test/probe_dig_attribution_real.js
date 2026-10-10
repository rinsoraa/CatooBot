'use strict'
/**
 * Phase 7D Follow-up —— 真实服务器上的**挖掘归因取证探针**（可选，不进 CI）。
 *
 * 目的：把 `dig_attribution.js` 的判定放到真实 Minecraft 服务器上验证三个关键问题：
 *
 *   S. **自己挖**：真实自挖会被判成 SELF_CONFIRMED 吗？依据是哪一种
 *      （`self_break_progress` 正面进度 / `dig_lifecycle_timing` 时序推断）？
 *      `removal.ratio` 是不是接近 1（说明"我们自己的完成时刻"确实是因果分界）？
 *   E. **挖到一半被外部改掉**（Phase 7D §10.4 的形状，用 `/setblock ... air`）：
 *      必须**不能**被判成 SELF_CONFIRMED（应为 AMBIGUOUS/removed_before_self_dig_completion）。
 *   X. **另一个真实客户端在挖同一个方块**：必须出现**正面外部证据**
 *      （`blockBreakProgressObserved` 带对方实体），绝不能写成自挖成功。
 *
 * 认证沿用 `minecraft_runtime/auth.json`（本机测试用；绝不进 Git）。服务器没开 / 连不上
 * → 打印 NOT AVAILABLE 并以 0 退出。跑完会把临时方块清成 air、断开并关掉自己拉起的 runtime
 * 进程（**不动**用户正在跑的那个 runtime：这里用的是自己的随机端口）。
 *
 * **前置条件（2026-10-10 实测记录）**：目标服务器必须允许 `auth: offline` 的客户端
 * （即 `online-mode=false`）。当前 `D:\Minecraft server\1.21.1` 的 `server.properties`
 * 是 `online-mode=true`，本机 bridge 用 offline 认证会被服务器立刻踢掉
 * （`multiplayer.disconnect.unverified_username`）——此时本探针**按设计**输出
 * "连接失败，跳过（不伪造结论）" 并以 0 退出，不产生也不是真实服务器结论。
 * 想真跑：改用局域网开服（默认 offline）、或把服务器改成 online-mode=false、
 * 或另备一份 microsoft 认证的 auth 文件（用 `MC_AUTH_FILE` 指过去）。
 *
 * 运行：node minecraft_runtime/test/probe_dig_attribution_real.js
 */
'use strict'

const fs = require('fs')
const http = require('http')
const net = require('net')
const path = require('path')
const { spawn } = require('child_process')

const mineflayer = require('mineflayer')
const { Vec3 } = require('vec3')

const RUNTIME_DIR = path.join(__dirname, '..')
const HOST = process.env.SMOKE_HOST || '127.0.0.1'
const PORT = Number.parseInt(process.env.SMOKE_PORT || '25565', 10)
const TERMINAL_EVENTS = [
  'minecraft.action.completed',
  'minecraft.action.failed',
  'minecraft.action.cancelled',
  'minecraft.action.timeout',
]
//: 外部改世界要在"挖到一半"落地：石头徒手 ~7.5s，等 800ms 足够早
const MID_DIG_DELAY_MS = Number.parseInt(process.env.PROBE_MID_DIG_DELAY_MS || '800', 10)
const SLOW_BLOCK = process.env.PROBE_SLOW_BLOCK || 'minecraft:stone'

const results = {}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.unref()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

function portOpen(host, port, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const socket = new net.Socket()
    socket.setTimeout(timeoutMs)
    socket.on('connect', () => {
      socket.destroy()
      resolve(true)
    })
    socket.on('timeout', () => {
      socket.destroy()
      resolve(false)
    })
    socket.on('error', () => resolve(false))
    socket.connect(port, host)
  })
}

function request(port, method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body)
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        method,
        path: urlPath,
        headers: {
          'Content-Type': 'application/json',
          ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
        },
      },
      (res) => {
        let raw = ''
        res.on('data', (chunk) => (raw += chunk))
        res.on('end', () => {
          try {
            resolve({ status: res.statusCode, body: raw ? JSON.parse(raw) : null })
          } catch (error) {
            reject(error)
          }
        })
      },
    )
    req.on('error', reject)
    req.setTimeout(20000, () => req.destroy(new Error('request timeout')))
    if (payload) req.write(payload)
    req.end()
  })
}

async function waitForValue(predicate, label, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await predicate()
    if (value) return value
    await sleep(200)
  }
  console.log(`[probe] 等待超时：${label}`)
  return null
}

async function main() {
  if (!(await portOpen(HOST, PORT))) {
    console.log(`[probe] REAL SERVER: NOT AVAILABLE（${HOST}:${PORT} 无响应）`)
    process.exit(0)
  }
  const authFile = process.env.MC_AUTH_FILE || path.join(RUNTIME_DIR, 'auth.json')
  if (!fs.existsSync(authFile)) {
    console.log(`[probe] REAL SERVER: NOT AVAILABLE（缺少认证文件 ${authFile}）`)
    process.exit(0)
  }
  const auth = JSON.parse(fs.readFileSync(authFile, 'utf8'))
  const botName = String(auth.username || '')

  const runtimePort = await freePort()
  const callbackPort = await freePort()
  const events = []
  const receiver = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      try {
        events.push(JSON.parse(raw))
      } catch {
        /* 非 JSON 丢弃 */
      }
      res.writeHead(200).end('{"ok":true}')
    })
  })
  await new Promise((resolve) => receiver.listen(callbackPort, '127.0.0.1', resolve))

  const child = spawn(process.execPath, [path.join(RUNTIME_DIR, 'runtime.js')], {
    env: {
      ...process.env,
      MC_RUNTIME_PORT: String(runtimePort),
      MC_CALLBACK_URL: `http://127.0.0.1:${callbackPort}/events`,
      MC_CALLBACK_TOKEN: 'probe-token',
      MC_AUTH_FILE: authFile,
      MC_CONNECT_TIMEOUT: '90',
    },
    stdio: ['ignore', 'pipe', 'inherit'],
  })
  let childExit = null
  child.on('exit', (code, signal) => {
    childExit = { code, signal }
    console.log(`[probe] runtime 子进程退出 code=${code} signal=${signal}`)
  })
  child.stdout.on('data', (chunk) => process.stdout.write(`[runtime] ${chunk}`))

  const status = async () => (await request(runtimePort, 'GET', '/minecraft/status')).body
  const say = async (message) => request(runtimePort, 'POST', '/minecraft/chat', { message })
  const terminal = (actionId) =>
    events.find((e) => TERMINAL_EVENTS.includes(e.event) && e.action_id === actionId)

  const ready = await waitForValue(
    async () => {
      if (childExit) return null
      try {
        const health = await request(runtimePort, 'GET', '/minecraft/health')
        return health.status === 200 ? true : null
      } catch {
        return null
      }
    },
    'runtime 就绪',
    30000,
  )
  if (!ready) {
    console.log(`[probe] runtime 没起来（childExit=${JSON.stringify(childExit)}）→ ABORT`)
    child.kill()
    receiver.close()
    process.exit(1)
  }

  let mate = null
  const placed = []
  const probe = async (label, spot, block) => {
    await say(`/setblock ${spot.x} ${spot.y} ${spot.z} ${block}`)
    placed.push(spot)
    await sleep(400)
  }

  try {
    await request(runtimePort, 'POST', '/minecraft/connect', { host: HOST, port: PORT })
    const online = await waitForValue(
      async () => ((await status()).status === 'ONLINE' ? await status() : null),
      'ONLINE',
      90000,
    )
    if (!online) {
      console.log('[probe] 连接失败，跳过（不伪造结论）')
      results.connect = 'FAILED'
      return
    }
    const origin = online.position
    const base = { x: Math.round(origin.x), y: Math.round(origin.y), z: Math.round(origin.z) }
    console.log(`[probe] 已上线 ${botName} @ ${JSON.stringify(base)}`)

    // ---------------- Case S：自己挖（期望 SELF_CONFIRMED）
    const sSpot = { x: base.x + 1, y: base.y, z: base.z }
    await probe('S', sSpot, SLOW_BLOCK)
    const digS = await request(runtimePort, 'POST', '/minecraft/dig', {
      ...sSpot,
      expected_block: SLOW_BLOCK,
    })
    const doneS = await waitForValue(() => terminal(digS.body.action_id), 'S 终态', 60000)
    results.self_dig = doneS
      ? { event: doneS.event, result: doneS.result || doneS.detail || null }
      : null
    console.log('[probe] S 自己挖 →', JSON.stringify(results.self_dig))

    // ---------------- Case E：挖到一半被外部改掉（期望**不是** SELF_CONFIRMED）
    const eSpot = { x: base.x - 1, y: base.y, z: base.z }
    await probe('E', eSpot, SLOW_BLOCK)
    const digE = await request(runtimePort, 'POST', '/minecraft/dig', {
      ...eSpot,
      expected_block: SLOW_BLOCK,
    })
    await sleep(MID_DIG_DELAY_MS)
    await say(`/setblock ${eSpot.x} ${eSpot.y} ${eSpot.z} air`)
    const doneE = await waitForValue(() => terminal(digE.body.action_id), 'E 终态', 60000)
    results.external_mid_dig = doneE
      ? { event: doneE.event, result: doneE.result || doneE.detail || null }
      : null
    console.log('[probe] E 中途被外部改掉 →', JSON.stringify(results.external_mid_dig))

    // ---------------- Case X：另一个真实客户端挖同一个方块（期望 EXTERNAL/AMBIGUOUS）
    const xSpot = { x: base.x, y: base.y, z: base.z + 1 }
    await probe('X', xSpot, SLOW_BLOCK)
    mate = mineflayer.createBot({
      host: HOST,
      port: PORT,
      username: `${botName}Mate`.slice(0, 16),
      auth: 'offline',
      version: process.env.PROBE_MC_VERSION || undefined,
    })
    const mateSpawned = await waitForValue(
      () => (mate && mate.entity ? true : null),
      '第二个客户端上线',
      60000,
    )
    if (!mateSpawned) {
      results.two_clients = 'MATE_FAILED'
      console.log('[probe] X 第二个客户端没上来 → SKIPPED')
    } else {
      await say(`/tp ${mate.username} ${base.x} ${base.y + 1} ${base.z + 2}`)
      await sleep(1200)
      const digX = await request(runtimePort, 'POST', '/minecraft/dig', {
        ...xSpot,
        expected_block: SLOW_BLOCK,
      })
      await sleep(1200)
      const mateBlock = mate.blockAt(new Vec3(xSpot.x, xSpot.y, xSpot.z))
      let mateDigError = null
      if (mateBlock) {
        mate.dig(mateBlock).catch((error) => {
          mateDigError = String(error && error.message ? error.message : error)
        })
      } else {
        mateDigError = 'mate 看不到那个方块'
      }
      const doneX = await waitForValue(() => terminal(digX.body.action_id), 'X 终态', 90000)
      results.two_clients = doneX
        ? { event: doneX.event, result: doneX.result || doneX.detail || null, mate_dig_error: mateDigError }
        : { mate_dig_error: mateDigError }
      console.log('[probe] X 双客户端 →', JSON.stringify(results.two_clients))
    }
  } finally {
    // 还原世界（临时方块清成 air），安静退出
    try {
      for (const spot of placed) {
        await say(`/setblock ${spot.x} ${spot.y} ${spot.z} air`)
      }
    } catch {
      /* 清理失败不影响取证结论 */
    }
    if (mate) {
      try {
        mate.quit()
      } catch {
        /* ignore */
      }
    }
    try {
      await request(runtimePort, 'POST', '/minecraft/disconnect', {})
    } catch {
      /* ignore */
    }
    await sleep(500)
    child.kill()
    receiver.close()
  }

  // ---------------- 结论（只打印事实 + 与预期的对照，不代替人工判断）
  const attributionOf = (entry) =>
    entry && entry.result && entry.result.attribution ? entry.result.attribution : null
  const self = attributionOf(results.self_dig)
  const external = attributionOf(results.external_mid_dig)
  const two = attributionOf(results.two_clients)
  const checks = [
    ['S 自己挖：world_effect = BLOCK_REMOVED', Boolean(self) && self.world_effect === 'BLOCK_REMOVED'],
    ['S 自己挖：attribution = SELF_CONFIRMED', Boolean(self) && self.attribution === 'SELF_CONFIRMED'],
    ['E 中途被外部改掉：不是 SELF_CONFIRMED', Boolean(external) && external.attribution !== 'SELF_CONFIRMED'],
    ['X 双客户端：不是 SELF_CONFIRMED', Boolean(two) && two.attribution !== 'SELF_CONFIRMED'],
  ]
  console.log('\n[probe] ===== 结果 =====')
  for (const [label, ok] of checks) console.log(`[probe] ${ok ? '✓' : '✗'} ${label}`)
  console.log(
    '[probe] 归因摘要：' +
      JSON.stringify(
        {
          self: self && {
            attribution: self.attribution,
            basis: self.confirm_basis,
            reason: self.reason_code,
            strict: self.strict_self_proof,
            ratio: self.removal && self.removal.ratio,
            removal_source: self.removal && self.removal.source,
            flags: self.flags,
          },
          external_mid_dig: external && {
            attribution: external.attribution,
            reason: external.reason_code,
            ratio: external.removal && external.removal.ratio,
            flags: external.flags,
          },
          two_clients: two && {
            attribution: two.attribution,
            reason: two.reason_code,
            basis: two.confirm_basis,
            entities: two.flags && two.flags.external_break_entities,
            ratio: two.removal && two.removal.ratio,
            flags: two.flags,
          },
        },
        null,
        2,
      ),
  )
  // 取证探针：只报事实。任何一项不成立 → 退出码 1（让脚本化调用能看出来）。
  if (checks.some(([, ok]) => !ok)) process.exit(1)
}

main().catch((error) => {
  console.error('[probe] 崩溃', error)
  process.exit(1)
})
