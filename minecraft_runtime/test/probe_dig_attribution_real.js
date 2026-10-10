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
const os = require('os')
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
//: 注意：dig 的 `expected_block` 与世界里读出来的方块名都是**裸名**（`stone`，没有命名空间前缀）
const SLOW_BLOCK = process.env.PROBE_SLOW_BLOCK || 'stone'

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
  // 子进程日志留一个环形缓冲：出问题时把尾巴打出来，绝不"静默超时"
  const runtimeLog = []
  child.stdout.on('data', (chunk) => {
    const text = String(chunk)
    for (const line of text.split('\n')) {
      if (line.trim()) runtimeLog.push(line)
    }
    while (runtimeLog.length > 60) runtimeLog.shift()
  })
  const dumpRuntimeLog = (label) => {
    console.log(`[probe] --- runtime 日志尾巴（${label}）---`)
    for (const line of runtimeLog.slice(-25)) console.log(`[probe]   ${line}`)
  }

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
  let nonOpChild = null
  let nonOpRuntimePort = null
  const placed = []
  const bare = (name) => String(name || '').replace(/^minecraft:/, '')
  const readBlock = async (x, y, z) => {
    const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
    const columns =
      (snap.body && snap.body.blocks && snap.body.blocks.near && snap.body.blocks.near.columns) || []
    const hit = columns.find((col) => col.pos && col.pos.x === x && col.pos.y === y && col.pos.z === z)
    return hit ? bare(hit.name) : 'air'
  }
  /** 放夹具并**读回来确认**（放不上就不挖，避免把环境问题算成归因结论）。 */
  const placeFixture = async (label, spot, block) => {
    await say(`/setblock ${spot.x} ${spot.y} ${spot.z} ${block}`)
    placed.push(spot)
    const ok = await waitForValue(
      async () => ((await readBlock(spot.x, spot.y, spot.z)) === bare(block) ? true : null),
      `${label} 夹具就位`,
      10000,
    )
    if (!ok) dumpRuntimeLog(`${label} 夹具没放上`)
    return ok
  }
  /** 发起一次 dig 并等终态：打印 POST 回执与终态载荷（成功和失败都要打印）。 */
  const digAndWait = async (label, spot, block, timeoutMs) => {
    const post = await request(runtimePort, 'POST', '/minecraft/dig', {
      ...spot,
      expected_block: block,
    })
    console.log(`[probe] ${label} dig POST status=${post.status} body=${JSON.stringify(post.body)}`)
    if (post.status !== 200 || !post.body || !post.body.action_id) {
      dumpRuntimeLog(`${label} dig 没起来`)
      return null
    }
    const done = await waitForValue(() => terminal(post.body.action_id), `${label} 终态`, timeoutMs)
    if (!done) {
      dumpRuntimeLog(`${label} 等不到终态`)
      return { action_id: post.body.action_id, event: 'NO_TERMINAL' }
    }
    return done
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
    if (await placeFixture('S', sSpot, SLOW_BLOCK)) {
      const doneS = await digAndWait('S', sSpot, SLOW_BLOCK, 60000)
      results.self_dig = doneS ? { event: doneS.event, result: doneS.result || null, detail: doneS.detail || null, error: doneS.error || null, code: doneS.code || null } : null
      console.log('[probe] S 自己挖 →', JSON.stringify(results.self_dig))
    } else {
      results.self_dig = 'FIXTURE_FAILED'
    }

    // ---------------- Case E：挖到一半被外部改掉（期望**不是** SELF_CONFIRMED）
    const eSpot = { x: base.x - 1, y: base.y, z: base.z }
    if (await placeFixture('E', eSpot, SLOW_BLOCK)) {
      const post = await request(runtimePort, 'POST', '/minecraft/dig', {
        ...eSpot,
        expected_block: SLOW_BLOCK,
      })
      console.log(`[probe] E dig POST status=${post.status} body=${JSON.stringify(post.body)}`)
      if (post.status === 200 && post.body && post.body.action_id) {
        await sleep(MID_DIG_DELAY_MS)
        await say(`/setblock ${eSpot.x} ${eSpot.y} ${eSpot.z} air`)
        const doneE = await waitForValue(() => terminal(post.body.action_id), 'E 终态', 60000)
        if (!doneE) dumpRuntimeLog('E 等不到终态')
        results.external_mid_dig = doneE
          ? { event: doneE.event, result: doneE.result || null, detail: doneE.detail || null, error: doneE.error || null, code: doneE.code || null }
          : null
      } else {
        dumpRuntimeLog('E dig 没起来')
      }
      console.log('[probe] E 中途被外部改掉 →', JSON.stringify(results.external_mid_dig))
    } else {
      results.external_mid_dig = 'FIXTURE_FAILED'
    }

    // ---------------- Case X：另一个真实客户端挖同一个方块（期望 EXTERNAL/AMBIGUOUS）
    const xSpot = { x: base.x, y: base.y, z: base.z + 1 }
    if (await placeFixture('X', xSpot, SLOW_BLOCK)) {
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
        await sleep(1500)
        // **让第二个客户端先挖**：目标方块的移除必须来自它（而不是我们），
        // 我们的 dig 才可能"挖到一半被外部弄没"（这才是要验证的归因场景）。
        const mateBlock = mate.blockAt(new Vec3(xSpot.x, xSpot.y, xSpot.z))
        let mateDigError = null
        if (mateBlock && bare(mateBlock.name) === bare(SLOW_BLOCK)) {
          mate.dig(mateBlock).catch((error) => {
            mateDigError = String(error && error.message ? error.message : error)
          })
          console.log('[probe] X 第二个客户端先开始挖同一个方块')
        } else {
          mateDigError = `mate 看到的方块是 ${mateBlock ? mateBlock.name : 'null'}`
          console.log('[probe] X', mateDigError)
        }
        // 我们的客户端晚 2.5s 才开始：它的完成时刻明显晚于对方的移除时刻
        await sleep(2500)
        const post = await request(runtimePort, 'POST', '/minecraft/dig', {
          ...xSpot,
          expected_block: SLOW_BLOCK,
        })
        console.log(`[probe] X dig POST status=${post.status} body=${JSON.stringify(post.body)}`)
        if (post.status === 200 && post.body && post.body.action_id) {
          const doneX = await waitForValue(() => terminal(post.body.action_id), 'X 终态', 90000)
          if (!doneX) dumpRuntimeLog('X 等不到终态')
          results.two_clients = doneX
            ? {
                event: doneX.event,
                result: doneX.result || null,
                detail: doneX.detail || null,
                error: doneX.error || null,
                code: doneX.code || null,
                mate_dig_error: mateDigError,
              }
            : { mate_dig_error: mateDigError }
        } else {
          dumpRuntimeLog('X dig 没起来')
        }
        console.log('[probe] X 双客户端 →', JSON.stringify(results.two_clients))
      }
    } else {
      results.two_clients = 'FIXTURE_FAILED'
    }

    // ---------------- Case P：只有本地乐观更新、服务器从没确认（真机新发现）
    // 机制：非 op 客户端在 `spawn-protection` 范围内挖掘 → 服务器根本不处理这次破坏，
    // 但 mineflayer 的 `finishDigging()` 仍会做本地乐观更新并 resolve。
    // 期望：`world_effect = UNKNOWN`（不是"世界变了"）→ 归属 AMBIGUOUS，绝不写成自证。
    try {
      const nonOpAuth = path.join(os.tmpdir(), `mc-nonop-${Date.now()}.json`)
      fs.writeFileSync(
        nonOpAuth,
        JSON.stringify({ mode: 'offline', username: 'ProbeNonOp', email: '', password: '' }),
      )
      nonOpRuntimePort = await freePort()
      const nonOpEvents = []
      const nonOpReceiver = http.createServer((req, res) => {
        let raw = ''
        req.on('data', (chunk) => (raw += chunk))
        req.on('end', () => {
          try {
            nonOpEvents.push(JSON.parse(raw))
          } catch {
            /* ignore */
          }
          res.writeHead(200).end('{"ok":true}')
        })
      })
      const nonOpCallbackPort = await freePort()
      await new Promise((resolve) => nonOpReceiver.listen(nonOpCallbackPort, '127.0.0.1', resolve))
      nonOpChild = spawn(process.execPath, [path.join(RUNTIME_DIR, 'runtime.js')], {
        env: {
          ...process.env,
          MC_RUNTIME_PORT: String(nonOpRuntimePort),
          MC_CALLBACK_URL: `http://127.0.0.1:${nonOpCallbackPort}/events`,
          MC_CALLBACK_TOKEN: 'probe-token',
          MC_AUTH_FILE: nonOpAuth,
          MC_CONNECT_TIMEOUT: '90',
        },
        stdio: ['ignore', 'pipe', 'ignore'],
      })
      nonOpChild.stdout.on('data', () => {})
      const nonOpStatus = async () =>
        (await request(nonOpRuntimePort, 'GET', '/minecraft/status')).body
      const readyNonOp = await waitForValue(
        async () => {
          try {
            return (await request(nonOpRuntimePort, 'GET', '/minecraft/health')).status === 200
              ? true
              : null
          } catch {
            return null
          }
        },
        '非 op runtime 就绪',
        30000,
      )
      if (readyNonOp) {
        await request(nonOpRuntimePort, 'POST', '/minecraft/connect', { host: HOST, port: PORT })
        const nonOpOnline = await waitForValue(
          async () => ((await nonOpStatus()).status === 'ONLINE' ? await nonOpStatus() : null),
          '非 op 客户端上线',
          90000,
        )
        if (nonOpOnline) {
          // 夹具放在**主机器人**旁边（主机器人是 op，且在出生点附近 → 目标必定落在保护范围内），
          // 再把非 op 客户端 /tp 过去挖它：非 op 在保护范围内挖掘会被服务器忽略（真机实测）。
          const pSpot = { x: base.x + 3, y: base.y, z: base.z }
          await say(`/setblock ${pSpot.x} ${pSpot.y} ${pSpot.z} stone`)
          placed.push(pSpot)
          const fixtureOk = await waitForValue(
            async () => ((await readBlock(pSpot.x, pSpot.y, pSpot.z)) === 'stone' ? true : null),
            'P 夹具就位',
            10000,
          )
          if (fixtureOk) {
            await say(`/tp ProbeNonOp ${pSpot.x - 1} ${pSpot.y + 1} ${pSpot.z}`)
            await sleep(1500)
            const post = await request(nonOpRuntimePort, 'POST', '/minecraft/dig', {
              ...pSpot,
              expected_block: 'stone',
            })
            console.log(`[probe] P dig POST status=${post.status} body=${JSON.stringify(post.body)}`)
            if (post.status === 200 && post.body && post.body.action_id) {
              const doneP = await waitForValue(
                () =>
                  nonOpEvents.find(
                    (e) => TERMINAL_EVENTS.includes(e.event) && e.action_id === post.body.action_id,
                  ),
                'P 终态',
                60000,
              )
              results.local_view_only = doneP
                ? { event: doneP.event, result: doneP.result || null, error: doneP.error || null, code: doneP.code || null }
                : null
              // 服务器侧的真相：夹具应该**还在**（保护生效 → 世界没变）
              const stillThere = await readBlock(pSpot.x, pSpot.y, pSpot.z)
              if (results.local_view_only) results.local_view_only.server_side_block = stillThere
              console.log('[probe] P 只有本地视图 →', JSON.stringify(results.local_view_only))
            }
          } else {
            results.local_view_only = 'FIXTURE_FAILED'
          }
        } else {
          results.local_view_only = 'OFFLINE'
        }
      } else {
        results.local_view_only = 'RUNTIME_FAILED'
      }
    } catch (error) {
      results.local_view_only = `ERROR:${String(error && error.message).slice(0, 80)}`
      console.log('[probe] P 出错（如实记录）→', results.local_view_only)
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
    if (nonOpChild) {
      try {
        await request(nonOpRuntimePort, 'POST', '/minecraft/disconnect', {})
      } catch {
        /* ignore */
      }
      nonOpChild.kill()
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
  const attributionOf = (entry) => {
    if (!entry || typeof entry !== 'object') return null
    const payload = entry.result || entry.detail || null
    return payload && payload.attribution ? payload.attribution : null
  }
  const self = attributionOf(results.self_dig)
  const external = attributionOf(results.external_mid_dig)
  const two = attributionOf(results.two_clients)
  const localOnly = attributionOf(results.local_view_only)
  const checks = [
    ['S 自己挖：world_effect = BLOCK_REMOVED', Boolean(self) && self.world_effect === 'BLOCK_REMOVED'],
    [
      'S 自己挖：归因为 SELF_CONFIRMED（严格）或 SELF_INFERRED（推断）',
      Boolean(self) && (self.attribution === 'SELF_CONFIRMED' || self.attribution === 'SELF_INFERRED'),
    ],
    [
      'S 自己挖：strict_self_proof 只可能对应 SELF_CONFIRMED（推断不得伪装成严格自证）',
      Boolean(self) &&
        (self.attribution === 'SELF_CONFIRMED'
          ? self.strict_self_proof === true
          : self.strict_self_proof === false),
    ],
    ['E 中途被外部改掉：不是 SELF_CONFIRMED', Boolean(external) && external.attribution !== 'SELF_CONFIRMED'],
    ['X 双客户端：不是 SELF_CONFIRMED', Boolean(two) && two.attribution !== 'SELF_CONFIRMED'],
    [
      'P 服务器拒绝了这次挖掘：world_effect 不是 BLOCK_REMOVED（也不可能是自证）',
      Boolean(localOnly) && localOnly.world_effect !== 'BLOCK_REMOVED',
    ],
    ['P 只有本地乐观更新：不是 SELF_CONFIRMED', Boolean(localOnly) && localOnly.attribution !== 'SELF_CONFIRMED'],
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
          local_view_only: localOnly && {
            attribution: localOnly.attribution,
            world_effect: localOnly.world_effect,
            reason: localOnly.reason_code,
            flags: localOnly.flags,
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
  // 显式退出：spawned runtime 子进程 / receiver 可能仍持有句柄，不能让进程一直挂着。
  process.exit(checks.some(([, ok]) => !ok) ? 1 : 0)
}

main().catch((error) => {
  console.error('[probe] 崩溃', error)
  process.exit(1)
})
