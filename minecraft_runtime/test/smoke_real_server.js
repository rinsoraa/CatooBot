'use strict'
/**
 * 真实 Minecraft 服务器 Smoke Test（可选，不进 CI）。
 *
 * 用**本项目自己的 runtime 进程**（独立端口 + 本地回调接收器）连接真实服务器
 * （默认 127.0.0.1:25565，见 SMOKE_HOST / SMOKE_PORT / SMOKE_USERNAME 环境变量），
 * 按当前 ActionRuntime 契约依次验证：
 *
 *   0. runtime 就绪 → connect → ONLINE
 *   1. move_to（近距离，若干方向反复试）—— Phase 3E 起是**持续型动作**：
 *      HTTP 只回 RUNNING，终态（completed/failed/cancelled/timeout）经事件送达；
 *      **前一个 exclusive 动作没进终态之前，绝不提交下一个**。
 *   2. move_to（远一点）→ STOP → CANCELLED + goal null + isMoving false + 位置停住
 *   3. follow_player（第二个真实客户端当目标）→ 目标走动 → 继续跟 → STOP
 *   4. HARD IDLE BARRIER：确认没有任何前台动作/导航残留，才进入 Phase 4B
 *   5. dig（Phase 4B 硬门禁）：错误 expected_block → block.changed；
 *      真挖 → RUNNING → completed → 三层验证（Action 结果 / 真实世界 / WorldPerception）；
 *      同位置再挖 → block.not_found
 *   6. dig + STOP（附近有"徒手要挖几秒"的方块才跑，否则明确 SKIPPED）
 *   7. disconnect
 *
 * 认证：默认用 minecraft_runtime/auth.json（本地文件，绝不进 Git）。
 * 服务器没开 / 连不上 → 打印 NOT AVAILABLE 并以 0 退出（文档记录用）；
 * 连接成功但行为不符合预期 → FAIL，退出码 1。
 *
 * 运行：node minecraft_runtime/test/smoke_real_server.js
 */

const { spawn } = require('child_process')
const http = require('http')
const net = require('net')
const path = require('path')
const fs = require('fs')

const RUNTIME_DIR = path.join(__dirname, '..')
const HOST = process.env.SMOKE_HOST || '127.0.0.1'
const PORT = Number.parseInt(process.env.SMOKE_PORT || '25565', 10)

//: 四个终态事件（ActionRuntime 契约）
const TERMINAL_EVENTS = [
  'minecraft.action.completed',
  'minecraft.action.failed',
  'minecraft.action.cancelled',
  'minecraft.action.timeout',
]

// ------------------------------------------------------------------ helpers

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

async function waitFor(predicate, label, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return true
    await sleep(300)
  }
  console.log(`[smoke] 等待超时：${label}`)
  return false
}

/** 等谓词返回「真值」并把它返回；超时 → null（区别于 waitFor 的 true/false）。 */
async function waitForValue(predicate, label, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await predicate()
    if (value) return value
    await sleep(300)
  }
  console.log(`[smoke] 等待超时：${label}`)
  return null
}

function distance2d(a, b) {
  return Math.hypot(a.x - b.x, a.z - b.z)
}

// ------------------------------------------------------------------ main

async function main() {
  if (!(await portOpen(HOST, PORT))) {
    console.log(`[smoke] REAL SERVER: NOT AVAILABLE（${HOST}:${PORT} 无响应）`)
    process.exit(0)
  }
  const authFile = process.env.MC_AUTH_FILE || path.join(RUNTIME_DIR, 'auth.json')
  if (!fs.existsSync(authFile)) {
    console.log(`[smoke] REAL SERVER: NOT AVAILABLE（缺少认证文件 ${authFile}）`)
    process.exit(0)
  }

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
      MC_CALLBACK_TOKEN: 'smoke-token',
      MC_AUTH_FILE: authFile,
      MC_CONNECT_TIMEOUT: '60',
    },
    stdio: ['ignore', 'pipe', 'inherit'],
  })
  const runtimeLog = []
  child.stdout.on('data', (d) => runtimeLog.push(d.toString()))

  let failed = false
  const check = (ok, label) => {
    console.log(`[smoke] ${ok ? '✓' : '✗'} ${label}`)
    if (!ok) failed = true
  }

  const status = async () => (await request(runtimePort, 'GET', '/minecraft/status')).body

  /**
   * 等一个 action 进入终态，返回 `{event, data}`（data = 事件载荷）。
   * 四种终态都算「有结果」——绝不只等 completed 再猜失败。
   */
  const waitForActionTerminal = (actionId, label, timeoutMs = 60000) =>
    waitForValue(
      () => events.find((e) => TERMINAL_EVENTS.includes(e.event) && e.action_id === actionId),
      label,
      timeoutMs,
    )

  /** 前台是否空闲（按 /minecraft/status 现有字段判定，不发明新 API）。 */
  const idleState = async () => {
    const snap = await status()
    const action = snap.action || {}
    const pathfinder = snap.pathfinder || {}
    const busy = Boolean(action.active_count) || ['RUNNING', 'QUEUED'].includes(action.status)
    return {
      idle: !busy && pathfinder.moving !== true && pathfinder.goal === null,
      busy,
      action,
      pathfinder,
    }
  }

  /** HARD IDLE BARRIER：保证进入下一段之前没有任何前台动作/导航残留。 */
  const ensureIdle = async (label) => {
    const stop = await request(runtimePort, 'POST', '/minecraft/stop', {})
    if (stop.body && Array.isArray(stop.body.cancelled) && stop.body.cancelled.length) {
      // 还有动作被叫停：等它们的终态事件落地（STOP 是唯一允许打断前台动作的入口）
      for (const actionId of stop.body.cancelled) {
        await waitForActionTerminal(actionId, `${label}：${actionId} 终态`, 10000)
      }
    }
    const settled = await waitForValue(async () => {
      const state = await idleState()
      return state.idle ? state : null
    }, `${label}：runtime 回到 IDLE`, 12000)
    if (!settled) {
      const state = await idleState()
      console.log(
        `[smoke]    残留状态：action=${JSON.stringify(state.action)} pathfinder=${JSON.stringify(state.pathfinder)}`,
      )
      check(false, `${label}：前置动作隔离失败（runtime 未回到 IDLE）`)
      return false
    }
    check(true, `${label}：runtime 已 IDLE（无前台动作 / goal=null / isMoving=false）`)
    return true
  }

  try {
    // 等 runtime 就绪
    const ready = await waitFor(async () => {
      try {
        const health = await request(runtimePort, 'GET', '/minecraft/health')
        return health.status === 200
      } catch {
        return false
      }
    }, 'runtime health', 20000)
    if (!ready) throw new Error('runtime 未就绪')

    const join = await request(runtimePort, 'POST', '/minecraft/connect', { host: HOST, port: PORT })
    check(join.status === 200, `connect ${HOST}:${PORT} → ${join.status}`)
    const online = await waitFor(async () => (await status()).status === 'ONLINE', '进入世界', 60000)
    if (!online) {
      const snap = await status()
      console.log(`[smoke] REAL SERVER: NOT AVAILABLE（无法进入世界：${snap && snap.last_error}）`)
      process.exit(0)
    }
    const origin = (await status()).position
    console.log(`[smoke] 已进入世界 @ ${JSON.stringify(origin)}`)

    // ---- 1. 近距离 move_to（持续型动作：必须等终态才能提交下一个） ----
    let moved = null
    let movedDir = null
    let lastMoveFailure = null
    for (const [dx, dz] of [[4, 0], [-4, 0], [0, 4], [0, -4]]) {
      const resp = await request(runtimePort, 'POST', '/minecraft/move_to', {
        x: origin.x + dx,
        y: origin.y,
        z: origin.z + dz,
      })
      if (!(resp.status === 200 && resp.body.status === 'RUNNING' && resp.body.action_id)) {
        lastMoveFailure = `启动被拒：${JSON.stringify(resp.body)}`
        continue
      }
      const actionId = resp.body.action_id
      const terminal = await waitForActionTerminal(actionId, `move_to ${dx},${dz} 终态`, 40000)
      if (terminal && terminal.event === 'minecraft.action.completed') {
        moved = { ...resp.body, result: terminal.result }
        movedDir = [dx, dz]
        break
      }
      lastMoveFailure = terminal
        ? `${terminal.event}${terminal.error ? `（${terminal.error}）` : ''}`
        : '终态事件超时'
    }
    check(
      Boolean(moved),
      `move_to 近距离 → completed（${moved ? JSON.stringify(moved.result) : `四个方向都失败，最后：${lastMoveFailure}`}）`,
    )

    if (moved) {
      // ---- 2. 远一点 → STOP → 位置必须停住 ----
      const dirLen = Math.hypot(movedDir[0], movedDir[1]) || 1
      const ux = movedDir[0] / dirLen
      const uz = movedDir[1] / dirLen
      const now = (await status()).position
      const moveResp = await request(runtimePort, 'POST', '/minecraft/move_to', {
        x: now.x + ux * 25,
        y: now.y,
        z: now.z + uz * 25,
      })
      check(
        moveResp.status === 200 && moveResp.body.status === 'RUNNING',
        `远距离 move_to 启动 → RUNNING（${JSON.stringify(moveResp.body)}）`,
      )
      const moveId = moveResp.body.action_id
      if (moveResp.body.status === 'RUNNING' && moveId) {
        const moving = await waitFor(async () => {
          const snap = await status()
          return Boolean(snap.pathfinder && snap.pathfinder.moving)
        }, '导航开始', 8000)
        if (moving) {
          const stop = await request(runtimePort, 'POST', '/minecraft/stop', {})
          check(stop.body.cancelled.includes(moveId), 'STOP 取消了移动中的 move_to')
          const terminal = await waitForActionTerminal(moveId, 'move_to cancelled 事件', 10000)
          check(
            Boolean(terminal) && terminal.event === 'minecraft.action.cancelled',
            `move_to 终态 = cancelled（${terminal ? terminal.event : '超时'}）`,
          )
          const stopped = await status()
          check(stopped.pathfinder.goal === null, 'goal == null')
          check(stopped.pathfinder.moving === false, 'isMoving == false')
          await sleep(600)
          const later = await status()
          // 半格余量：真实服务器的惯性/下落收尾不算"还在走"
          check(distance2d(later.position, stopped.position) <= 0.6, '停止后位置不再漂移')
        } else {
          // 没开始移动：也要等它自己落定，避免污染后面的段落
          const terminal = await waitForActionTerminal(moveId, '远距离 move_to 终态', 20000)
          console.log(
            `[smoke] 远距离目标未能开始移动（${terminal ? terminal.event : '终态超时'}）——跳过 STOP 段`,
          )
        }
      }
    }

    await ensureIdle('move_to 段收尾')

    // ---- 3. follow_player 真实验证（第二个客户端当目标） ----
    const mineflayer = require('mineflayer')
    const targetName = process.env.SMOKE_FOLLOW_TARGET || 'SmokeTgt'
    console.log(`[smoke] 跟随测试：拉起目标客户端 ${targetName}`)
    const targetBot = mineflayer.createBot({
      host: HOST,
      port: PORT,
      username: targetName,
      hideErrors: true,
      auth: 'offline',
    })
    const targetSpawned = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), 30000)
      targetBot.once('spawn', () => {
        clearTimeout(timer)
        resolve(true)
      })
      targetBot.on('error', () => {
        clearTimeout(timer)
        resolve(false)
      })
    })
    if (!targetSpawned) {
      console.log('[smoke] 目标客户端进不了服务器（在线模式/白名单？）——跳过 follow 段')
      try {
        targetBot.quit()
      } catch {
        /* ignore */
      }
    } else {
      const meNow = (await status()).position
      const before = meNow
      // 目标先走到罐头附近（真实服务器可能很远）
      targetBot.chat(`/tp ${targetName} ${Math.round(meNow.x + 3)} ${Math.round(meNow.y)} ${Math.round(meNow.z)}`)
      await sleep(1200)
      const followResp = await request(runtimePort, 'POST', '/minecraft/follow_player', {
        username: targetName,
      })
      check(
        followResp.status === 200 && followResp.body.status === 'RUNNING',
        `follow_player 启动 → RUNNING（${JSON.stringify(followResp.body)}）`,
      )
      const followId = followResp.body.action_id
      // 目标往前走一段（真实走动），罐头应当跟上且 action 仍在 RUNNING
      targetBot.look(meNow.x, meNow.y, meNow.z, true)
      targetBot.setControlState('forward', true)
      targetBot.setControlState('sprint', true)
      await sleep(2500)
      targetBot.setControlState('forward', false)
      targetBot.setControlState('sprint', false)
      await sleep(1500)
      const during = await status()
      const targetEntity = targetBot.entity ? targetBot.entity.position : null
      const gap = targetEntity
        ? Math.hypot(during.position.x - targetEntity.x, during.position.z - targetEntity.z)
        : null
      check(
        during.action && during.action.status === 'RUNNING',
        `目标移动后 follow 仍在运行（${during.action && during.action.status}）`,
      )
      check(
        during.pathfinder && during.pathfinder.goal === 'GoalFollow',
        `status.pathfinder.goal = GoalFollow（${during.pathfinder && during.pathfinder.goal}）`,
      )
      check(gap !== null && gap <= 12, `跟到目标附近（gap=${gap === null ? '-' : gap.toFixed(1)} 格）`)
      const followedDistance = Math.hypot(during.position.x - before.x, during.position.z - before.z)
      check(followedDistance >= 1, `罐头真的跟走了（位移 ${followedDistance.toFixed(1)} 格）`)

      const runningNow = during.action && during.action.status === 'RUNNING'
      const stopFollow = await request(runtimePort, 'POST', '/minecraft/stop', {})
      if (runningNow) {
        check(stopFollow.body.cancelled.includes(followId), 'STOP 取消跟随')
      } else {
        console.log(`[smoke] 跟随已自行结束（${during.action && during.action.status}）——不要求 STOP 取消`)
      }
      if (followId) await waitForActionTerminal(followId, 'follow 终态', 10000)
      const stoppedFollow = await status()
      check(stoppedFollow.pathfinder.goal === null, '跟随 STOP 后 goal == null')
      check(stoppedFollow.pathfinder.moving === false, '跟随 STOP 后 isMoving == false')
      await sleep(600)
      const laterFollow = await status()
      check(
        distance2d(laterFollow.position, stoppedFollow.position) <= 0.6,
        '跟随 STOP 后位置不再漂移',
      )
      try {
        targetBot.quit()
      } catch {
        /* ignore */
      }
    }

    // ---- 4. HARD IDLE BARRIER：Phase 4B 硬门禁之前必须真的空闲 ----
    const digReady = await ensureIdle('Phase 4B 前置')

    // ---- 5. Phase 4B 硬门禁：单方块 dig 真实验证 ----
    const snapshot = async (layers) =>
      (await request(runtimePort, 'GET', `/minecraft/world/snapshot?layers=${layers}`)).body
    const blockAt = async (pos) => {
      const snap = await snapshot('near')
      const columns = (snap.blocks && snap.blocks.near && snap.blocks.near.columns) || []
      const hit = columns.find(
        (col) => col.pos && col.pos.x === pos.x && col.pos.y === pos.y && col.pos.z === pos.z,
      )
      return hit ? hit.name : null
    }
    // 徒手可挖（不需要工具）的方块
    const HAND_DIGGABLE = ['dirt', 'grass_block', 'sand', 'gravel', 'clay', 'snow', 'oak_log']
    // STOP 段要"徒手要挖几秒"的方块：只认**天然木头**（约 3s，且不会去动玩家的建筑）。
    // 需要指定别的目标时用 SMOKE_DIG_STOP_TARGET="x,y,z:block"（例如自己用 /setblock 放一块）；
    // 或者用 SMOKE_DIG_STOP_TRY_SETBLOCK=1 让脚本自己放（需要 bot 有 op，会在世界里留一条命令回执）。
    const isSlowByHand = (name) => /(_log|_stem|_hyphae)$/.test(name)

    const near = await snapshot('near')
    const columns = (near.blocks && near.blocks.near && near.blocks.near.columns) || []
    const reachable = columns
      .filter((col) => col.distance !== undefined && col.distance <= 4 && col.pos)
      .sort((a, b) => a.distance - b.distance)
    const target = reachable.find((col) => HAND_DIGGABLE.includes(col.name))

    if (!digReady || !target) {
      console.log(
        target
          ? '[smoke] ✗ Phase 4B：runtime 未空闲，dig 硬门禁不能执行（前置动作隔离失败）'
          : '[smoke] SKIPPED Phase 4B dig：附近没有徒手可挖的方块（dirt/grass/log…）',
      )
    } else {
      const pos = target.pos
      console.log(`[smoke] dig 目标：${target.name} @ ${JSON.stringify(pos)}（${target.distance} 格）`)

      // §七十五：先故意报错方块名 → block.changed（结构化 expected/actual，且不破坏）
      const wrong = await request(runtimePort, 'POST', '/minecraft/dig', {
        x: pos.x,
        y: pos.y,
        z: pos.z,
        expected_block: 'minecraft:bedrock',
      })
      const wrongCode = wrong.body && wrong.body.error && wrong.body.error.code
      if (wrongCode === 'action.busy') {
        check(false, '前置动作隔离失败：dig 得到 action.busy（应先在空态执行 block.changed 测试）')
      } else {
        check(
          wrong.status === 409 && wrongCode === 'block.changed',
          `expected_block 不符 → block.changed（HTTP ${wrong.status}，${JSON.stringify(wrong.body && wrong.body.error)}）`,
        )
        check(
          wrong.body.error.detail &&
            wrong.body.error.detail.expected === 'minecraft:bedrock' &&
            wrong.body.error.detail.actual === target.name,
          `block.changed 带 expected/actual（${JSON.stringify(wrong.body.error.detail)}）`,
        )
        check((await blockAt(pos)) === target.name, '拒绝后目标方块原地未动')
      }

      // §七十三：真挖 → RUNNING → completed → 三层验证
      if ((await idleState()).idle) {
        const digResp = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: pos.x,
          y: pos.y,
          z: pos.z,
          expected_block: target.name,
        })
        const startedOk =
          digResp.status === 200 &&
          digResp.body.status === 'RUNNING' &&
          Boolean(digResp.body.action_id)
        if (!startedOk) {
          console.log(`[smoke]    dig 启动失败，完整响应：${JSON.stringify(digResp)}`)
          check(false, `dig 启动必须 200/RUNNING 且带 action_id（HTTP ${digResp.status}）`)
        } else {
          check(true, `dig 启动 → RUNNING（action_id=${digResp.body.action_id}）`)
          const digId = digResp.body.action_id
          const terminal = await waitForActionTerminal(digId, 'dig 终态事件', 60000)
          if (!terminal) {
            check(false, `dig 未在 60s 内进入终态（action_id=${digId}）`)
          } else if (terminal.event !== 'minecraft.action.completed') {
            check(false, `dig 终态=${terminal.event}（${terminal.error || terminal.reason || '-'}）`)
          } else {
            const result = terminal.result || {}
            check(
              result.block_before === target.name && result.block_after !== target.name,
              `real dig completed（block_before=${result.block_before} → block_after=${result.block_after}）`,
            )
            // 第二层：真实世界（runtime 重新扫描一次；不再有那块）
            const worldGone = await waitForValue(async () => {
              const name = await blockAt(pos)
              return name !== target.name ? name || 'air' : null
            }, '真实世界方块变化', 15000)
            check(Boolean(worldGone), `world block changed（该位置现在是 ${worldGone || '未知'}）`)
            // 第三层：感知输入（near 扫描）在随后的两个节拍里**稳定**不含它——
            // 这是 Python 侧 WorldPerception diff 的输入；真正的语义事件（world.changed）
            // 由 tests/test_minecraft_dig_flow.py 与 flying-squid E2E 覆盖。
            let stableScans = 0
            for (let round = 0; round < 3; round += 1) {
              await sleep(1200)
              if ((await blockAt(pos)) !== target.name) stableScans += 1
            }
            check(
              stableScans === 3,
              `WorldPerception input stable（连续 3 次扫描都不含它，得 ${stableScans}/3）`,
            )
          }
        }

        // §七：同位置再挖 → block.not_found，且不启动新动作
        const beforeRedig = await idleState()
        const reDig = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: pos.x,
          y: pos.y,
          z: pos.z,
          expected_block: target.name,
        })
        check(
          reDig.body.error && reDig.body.error.code === 'block.not_found',
          `同一位置再挖 → block.not_found（${JSON.stringify(reDig.body && reDig.body.error)}）`,
        )
        const afterRedig = await idleState()
        check(
          !reDig.body.action_id &&
            afterRedig.action.active_count === beforeRedig.action.active_count,
          'block.not_found 不启动新 dig action',
        )
      } else {
        check(false, '前置动作隔离失败：真挖之前 runtime 仍有前台动作')
      }

      // ---- 6. dig + STOP（要一个够慢的方块；没有就明确 SKIPPED） ----
      const override = (process.env.SMOKE_DIG_STOP_TARGET || '').trim()
      let slow = null
      if (override) {
        const [coords, blockName] = override.split(':')
        const [x, y, z] = coords.split(',').map((value) => Number.parseFloat(value))
        if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z) && blockName) {
          slow = { pos: { x, y, z }, name: blockName.trim() }
        } else {
          console.log(`[smoke] SMOKE_DIG_STOP_TARGET 格式不对（应为 "x,y,z:block"）：${override}`)
        }
      } else {
        slow = reachable.find((col) => isSlowByHand(col.name))
        const trySetBlock = process.env.SMOKE_DIG_STOP_TRY_SETBLOCK === '1'
        if (!slow && trySetBlock) {
          // 附近没有天然木头：**显式开启时**才自己放一块（本机测试世界；bot 有 op 才放得成）。
          // 默认不动操作者的世界（失败的命令会往公屏扔一行提示）。
          // 放不成仍然如实 SKIPPED——绝不把"没测到"写成"通过"。
          const here = (await status()).position
          const spot = {
            x: Math.round(here.x) + 2,
            y: Math.round(here.y),
            z: Math.round(here.z),
          }
          console.log(`[smoke] 附近没有天然慢速方块：尝试 /setblock 放一块 oak_log @ ${JSON.stringify(spot)}（需要 op）`)
          await request(runtimePort, 'POST', '/minecraft/chat', {
            message: `/setblock ${spot.x} ${spot.y} ${spot.z} oak_log`,
          })
          const placed = await waitForValue(
            async () => ((await blockAt(spot)) === 'oak_log' ? spot : null),
            'setblock oak_log（bot 没 op 就会超时）',
            6000,
          )
          if (placed) {
            slow = { pos: spot, name: 'oak_log', placedBySmoke: true }
            console.log('[smoke] 已放好一块 oak_log 作为 STOP 目标（结束后会清掉）')
          }
        }
      }
      if (!slow) {
        console.log('[smoke] SKIPPED dig STOP 段：附近没有"徒手要挖几秒"的方块（不伪造结论）')
      } else if (!(await idleState()).idle) {
        check(false, '前置动作隔离失败：STOP 段之前 runtime 仍有前台动作')
      } else {
        const slowPos = slow.pos
        const stopDig = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: slowPos.x,
          y: slowPos.y,
          z: slowPos.z,
          expected_block: slow.name,
        })
        const stopStarted =
          stopDig.status === 200 && stopDig.body.status === 'RUNNING' && Boolean(stopDig.body.action_id)
        check(stopStarted, `STOP 段：dig ${slow.name} 启动 → RUNNING（HTTP ${stopDig.status}）`)
        if (stopStarted) {
          const digId = stopDig.body.action_id
          const startedEvent = await waitForValue(
            () => events.find((e) => e.event === 'minecraft.action.started' && e.action_id === digId),
            'dig started 事件',
            8000,
          )
          check(Boolean(startedEvent), 'dig started 事件已送达')
          const stop = await request(runtimePort, 'POST', '/minecraft/stop', {})
          check(stop.body.cancelled.includes(digId), 'STOP 取消了挖掘中的 dig')
          const terminal = await waitForActionTerminal(digId, 'dig cancelled 事件', 10000)
          check(
            Boolean(terminal) && terminal.event === 'minecraft.action.cancelled',
            `dig 终态 = cancelled（${terminal ? terminal.event : '超时'}）`,
          )
          await sleep(800)
          check((await blockAt(slowPos)) === slow.name, 'STOP 后方块仍在（没有继续破坏）')
          if (slow.placedBySmoke) {
            // 自己放的测试方块用完即清（不把测试痕迹留在操作者的世界里）
            await request(runtimePort, 'POST', '/minecraft/chat', {
              message: `/setblock ${slowPos.x} ${slowPos.y} ${slowPos.z} air`,
            })
            await sleep(500)
            console.log('[smoke] 已清掉测试用的 oak_log')
          }
        }
      }
    }

    await ensureIdle('smoke 收尾')

    await request(runtimePort, 'POST', '/minecraft/disconnect', {})
    await waitFor(async () => (await status()).status === 'DISCONNECTED', '主动离开', 15000)
    check(true, '主动离开完成')
    console.log(failed ? '[smoke] REAL SERVER: FAIL' : '[smoke] REAL SERVER: PASS')
    process.exitCode = failed ? 1 : 0
  } catch (error) {
    console.log(`[smoke] REAL SERVER: FAIL（${error.message}）`)
    console.log(runtimeLog.join('').split('\n').slice(-10).join('\n'))
    process.exitCode = 1
  } finally {
    child.kill('SIGKILL')
    receiver.close()
  }
}

main()
