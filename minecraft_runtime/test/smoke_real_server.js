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
 *   7. Phase 4C：place（单方块，六层证据）
 *   8. Phase 4D：inventory/slots → equip（含 already_equipped）→ inventory_move →
 *      重读槽位表 → **恢复原状**（槽位布局 + 主手）→ ensureIdle → disconnect
 *      （背包里没有可换的物品 / 没有空槽 → 明确 SKIPPED，不伪造结论）
 *   9. disconnect
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
const { normalizeItemName } = require(path.join(RUNTIME_DIR, 'runtime.js'))
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

    // ---- 5. Phase 4C 硬门禁：inventory（只读）+ place（单方块，六层证据） ----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4C：runtime 未空闲，place 硬门禁不能执行')
    } else {
      const inv = await request(runtimePort, 'GET', '/minecraft/inventory')
      check(
        inv.status === 200 && inv.body.online === true,
        `inventory 只读切片（online=${inv.body && inv.body.online}，`
          + `${(inv.body && inv.body.items && inv.body.items.length) || 0} 种物品）`,
      )
      const held = inv.body ? inv.body.held_item : null
      console.log(`[smoke] 主手：${held ? `${held.name}×${held.count}` : '空手（没东西可放）'}`)

      // 目标选择：SMOKE_PLACE_TARGET="x,y,z" 优先；否则找"可达的实心方块，其正上方是空气"
      // （face=up → 参考方块就是那个实心方块，语义最清楚，§三十一）
      const override = (process.env.SMOKE_PLACE_TARGET || '').trim()
      const face = (process.env.SMOKE_PLACE_FACE || 'up').trim().toLowerCase()
      const itemOverride = (process.env.SMOKE_PLACE_ITEM || '').trim()
      let target = null
      let reference = null
      if (override) {
        const [x, y, z] = override.split(',').map((value) => Number.parseInt(value, 10))
        if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
          const delta = { up: [0, -1, 0], down: [0, 1, 0], north: [0, 0, 1], south: [0, 0, -1], east: [-1, 0, 0], west: [1, 0, 0] }[face]
          target = { x, y, z }
          reference = delta ? { x: x + delta[0], y: y + delta[1], z: z + delta[2] } : null
        } else {
          console.log(`[smoke] SMOKE_PLACE_TARGET 格式不对（应为 "x,y,z"）：${override}`)
        }
      } else {
        const snapshotNow = await snapshot('near')
        const columnsNow = (snapshotNow.blocks && snapshotNow.blocks.near && snapshotNow.blocks.near.columns) || []
        const candidates = columnsNow
          .filter((col) => col.pos && col.distance !== undefined)
          .filter((col) => col.distance >= 1.5 && col.distance <= 3.5) // 站得开一点，又能在 5 格内够到上方
          .sort((a, b) => a.distance - b.distance)
        const spot = candidates[0]
        if (spot) {
          target = { x: spot.pos.x, y: spot.pos.y + 1, z: spot.pos.z }
          reference = { x: spot.pos.x, y: spot.pos.y, z: spot.pos.z }
        }
      }

      if (!held) {
        console.log(
          '[smoke] SKIPPED place：主手没有物品（place 不会自动装备/切槽）。'
            + '请在游戏里手持一个方块后再跑（或设 SMOKE_PLACE_ITEM 之前先手持对应物品）',
        )
      } else if (!target || !reference) {
        console.log('[smoke] SKIPPED place：没找到合适的目标（需要"实心方块 + 其上方是空气"且在 5 格内）')
      } else {
        const item = itemOverride || held.name
        console.log(
          `[smoke] place 目标：${item} → (${target.x},${target.y},${target.z})，face=${face}，`
            + `参考方块 (${reference.x},${reference.y},${reference.z})`,
        )
        const before = (await request(runtimePort, 'GET', `/minecraft/inventory`)).body.held_item
        const placeResp = await request(runtimePort, 'POST', '/minecraft/place', {
          x: target.x,
          y: target.y,
          z: target.z,
          face,
          expected_item: item,
        })
        const placeStarted =
          placeResp.status === 200 &&
          placeResp.body.status === 'RUNNING' &&
          Boolean(placeResp.body.action_id)
        if (!placeStarted) {
          console.log(`[smoke]    place 启动失败，完整响应：${JSON.stringify(placeResp)}`)
          check(false, `place 启动必须 200/RUNNING 且带 action_id（HTTP ${placeResp.status}）`)
        } else {
          check(true, `place 启动 → RUNNING（action_id=${placeResp.body.action_id}）`)
          const placeId = placeResp.body.action_id
          const terminal = await waitForActionTerminal(placeId, 'place 终态事件', 30000)
          if (!terminal) {
            check(false, `place 未在 30s 内进入终态（action_id=${placeId}）`)
          } else if (terminal.event !== 'minecraft.action.completed') {
            check(false, `place 终态=${terminal.event}（${terminal.error || terminal.reason || '-'}）`)
          } else {
            const result = terminal.result || {}
            check(
              result.block_before === 'air' && result.block_after !== 'air',
              `real place completed（block_before=${result.block_before} → block_after=${result.block_after}）`,
            )
            check(
              normalizeItemName(result.block_after) === normalizeItemName(item),
              `block_after == expected_item（期望 ${item}，得到 ${result.block_after}）`,
            )
            check(
              result.face === face && result.reference_block,
              `结果带 reference_block=${result.reference_block} / face=${result.face}`,
            )
            // 第二层：真实世界（重新扫描那个坐标）
            const placed = await waitForValue(
              async () => {
                const name = await blockAt(target)
                return name && name !== 'air' ? name : null
              },
              '真实世界出现该方块',
              10000,
            )
            check(Boolean(placed), `world block changed（该位置现在是 ${placed || '未知'}）`)
            // 第三层：感知输入连续 3 次稳定包含它
            let stableScans = 0
            for (let round = 0; round < 3; round += 1) {
              await sleep(1200)
              if ((await blockAt(target)) === placed) stableScans += 1
            }
            check(stableScans === 3, `WorldPerception input stable（连续 3 次都是 ${placed}，得 ${stableScans}/3）`)
            // 物品数量：如实记录，不作为硬门禁（creative/modded 行为不同）
            const after = (await request(runtimePort, 'GET', '/minecraft/inventory')).body.held_item
            console.log(
              `[smoke]    手持物品 before=${before ? `${before.name}×${before.count}` : '空'}`
                + ` after=${after ? `${after.name}×${after.count}` : '空'}`
                + `（result.item_after_count=${result.item_after_count}）`,
            )

            // 收尾：把自己放的那一块挖回去（只在自己放的物品徒手可挖时；否则留给操作者处理）
            const handDiggable = /^(dirt|grass_block|sand|gravel|clay|snow|.*_log|.*_planks|torch)$/
            if (handDiggable.test(normalizeItemName(placed))) {
              const cleanup = await request(runtimePort, 'POST', '/minecraft/dig', {
                x: target.x,
                y: target.y,
                z: target.z,
                expected_block: placed,
              })
              if (cleanup.status === 200 && cleanup.body.action_id) {
                await waitForActionTerminal(cleanup.body.action_id, '清理 dig 终态', 20000)
                await sleep(600)
                const left = await blockAt(target)
                console.log(`[smoke]    已把自己放的那一块挖回（该位置现在 ${left}）`)
              }
            } else {
              console.log(`[smoke]    注意：${placed} 徒手挖不了，留在 (${target.x},${target.y},${target.z})，需要你自己清理`)
            }
          }
        }
      }
    }

    // ---- 6. Phase 4D 硬门禁：slots → equip → inventory_move → 恢复（单物品 / 单槽位） ----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4D：runtime 未空闲，equip / inventory_move 硬门禁不能执行')
    } else {
      const name = (value) => normalizeItemName(value || '')
      /** 槽位表 → 稳定签名（用于"恢复原状"的逐槽比对）；空槽位不出现在表里。 */
      const signature = (rows) =>
        rows
          .map((row) => `${row.slot}:${name(row.name)}×${row.count}`)
          .sort((a, b) => Number.parseInt(a, 10) - Number.parseInt(b, 10))
          .join('|')
      const readSlots = async () => {
        const resp = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        return resp.status === 200 && resp.body ? resp.body.slots || [] : null
      }
      const readSlice = async () => (await request(runtimePort, 'GET', '/minecraft/inventory')).body

      const slotsBefore = await readSlots()
      const sliceBefore = await readSlice()
      const heldBefore = sliceBefore ? sliceBefore.held_item : null
      if (!slotsBefore) {
        check(false, 'inventory/slots 必须 200（Phase 4D 调试槽位视图）')
      } else {
        check(true, `inventory/slots 只读在线（${slotsBefore.length} 个非空槽位）`)
        console.log(
          `[smoke]    主手=${heldBefore ? `${heldBefore.name}×${heldBefore.count}` : '空'}；`
            + `槽位布局=${signature(slotsBefore) || '（空背包）'}`,
        )

        // (a) already_equipped：把"当前主手物品"再 equip 一次 —— 必须如实报 already_equipped=true，
        //     且不改任何槽位（可安全验证，零副作用）
        if (!heldBefore || !heldBefore.name) {
          console.log('[smoke] SKIPPED already_equipped：主手是空手（没有可重复装备的物品）')
        } else {
          const again = await request(runtimePort, 'POST', '/minecraft/equip', {
            item: heldBefore.name,
          })
          const started = again.status === 200 && again.body.status === 'RUNNING' && again.body.action_id
          if (!started) {
            console.log(`[smoke]    equip(already) 启动失败：${JSON.stringify(again)}`)
            check(false, `equip 当前主手物品必须 200/RUNNING（HTTP ${again.status}）`)
          } else {
            const terminal = await waitForActionTerminal(again.body.action_id, 'equip(already) 终态', 30000)
            const result = (terminal && terminal.result) || {}
            check(
              Boolean(terminal) &&
                terminal.event === 'minecraft.action.completed' &&
                result.already_equipped === true,
              `already_equipped 如实上报（event=${terminal && terminal.event}，`
                + `already_equipped=${result.already_equipped}）`,
            )
            const slotsAfter = await readSlots()
            check(
              signature(slotsAfter) === signature(slotsBefore),
              'already_equipped 不改任何槽位（布局与装备前一致）',
            )
          }
        }

        // (b) 真换手：找一个**真实存在**且不是主手物品的物品（动态选，绝不写死 dirt）
        //     这台服务器不给掉落：背包里只有一件物品时，用服务器自己的 /give 造一件夹具
        //     （与 dig STOP 段用 /setblock 造慢方块同一手法；结束后 /clear 清掉）
        const FIXTURE_ITEM = 'minecraft:oak_planks'
        let seeded = false
        let equipTarget = slotsBefore.find(
          (row) => name(row.name) !== name(heldBefore && heldBefore.name),
        )
        let slotsReady = slotsBefore
        if (!equipTarget) {
          const botName = (await status()).username
          if (!botName) {
            console.log('[smoke] ✗ 4D 夹具：拿不到 bot 用户名，无法 /give')
          } else {
            const give = await request(runtimePort, 'POST', '/minecraft/chat', {
              message: `/give ${botName} ${FIXTURE_ITEM} 3`,
            })
            if (give.status !== 200) {
              console.log(`[smoke]    /give 发送失败：${JSON.stringify(give)}`)
            }
            const seededSlots = await waitForValue(async () => {
              const rows = await readSlots()
              return rows && rows.some((row) => name(row.name) === name(FIXTURE_ITEM)) ? rows : null
            }, '4D 夹具物品进入背包', 10000)
            if (seededSlots) {
              seeded = true
              slotsReady = seededSlots
              equipTarget = slotsReady.find((row) => name(row.name) === name(FIXTURE_ITEM))
              console.log(
                `[smoke] 4D 夹具：/give ${botName} ${FIXTURE_ITEM} 3 → 槽位 ${equipTarget.slot}`
                  + '（测试结束后 /clear 清掉）',
              )
            } else {
              console.log(
                '[smoke]    /give 没有生效（服务器不允许 / 命令权限）：无法在真机上造出"第二件物品"',
              )
            }
          }
        }
        if (!equipTarget) {
          console.log(
            '[smoke] SKIPPED equip：背包里没有"不是主手"的物品，且无法用 /give 造夹具'
              + '（换手必然改变你的物品，不做）；请往背包里放一件东西再跑这段',
          )
        } else {
          const item = equipTarget.name
          const sourceSlotBefore = equipTarget.slot
          console.log(`[smoke] equip 目标：${item}（换手前在槽位 ${sourceSlotBefore}）`)
          const resp = await request(runtimePort, 'POST', '/minecraft/equip', { item })
          const started = resp.status === 200 && resp.body.status === 'RUNNING' && resp.body.action_id
          if (!started) {
            console.log(`[smoke]    equip 启动失败：${JSON.stringify(resp)}`)
            check(false, `equip 启动必须 200/RUNNING 且带 action_id（HTTP ${resp.status}）`)
          } else {
            check(true, `equip 启动 → RUNNING（action_id=${resp.body.action_id}）`)
            const terminal = await waitForActionTerminal(resp.body.action_id, 'equip 终态事件', 30000)
            if (!terminal) {
              check(false, 'equip 未在 30s 内进入终态')
            } else if (terminal.event !== 'minecraft.action.completed') {
              check(false, `equip 终态=${terminal.event}（${terminal.error || terminal.reason || '-'}）`)
            } else {
              const result = terminal.result || {}
              check(
                name(result.item) === name(item) && result.destination === 'hand',
                `equip completed（item=${result.item}，destination=${result.destination}）`,
              )
              check(
                result.held_item && name(result.held_item.name) === name(item),
                `结果里的 held_item=${result.held_item ? result.held_item.name : '空'}`,
              )
              // 第二层：真实背包（重读只读切片）
              const heldNow = await waitForValue(async () => {
                const body = await readSlice()
                const held = body && body.held_item
                return held && name(held.name) === name(item) ? held : null
              }, '真实世界主手换成目标物品', 15000)
              check(Boolean(heldNow), `真实主手改变（现在 ${heldNow ? heldNow.name : '未知'}）`)
              // 第三层：WorldPerception 输入（snapshot 的 self 里应能看到它）
              const perceivable = await waitForValue(async () => {
                const snap = await snapshot('near')
                return JSON.stringify(snap.self || {}).includes(name(item)) ? true : null
              }, 'WorldPerception 反映新主手', 10000)
              check(Boolean(perceivable), 'WorldPerception 输入反映新主手（snapshot.self）')

              // (c) inventory_move：把刚拿到手里的那个物品搬 1 个到**空槽**（主背包优先，避开选中快捷栏）
              const slotsNow = await readSlots()
              if (seeded && slotsReady === slotsBefore) slotsReady = slotsNow
              const selectedHotbar = 36 + Number(sliceBefore.selected_hotbar_slot || 0)
              const handSlot = (slotsNow.find(
                (row) => name(row.name) === name(item) && row.slot === selectedHotbar,
              ) || {}).slot
              const occupied = new Set(slotsNow.map((row) => row.slot))
              const emptySlots = []
              for (let slot = 9; slot <= 44; slot += 1) {
                if (!occupied.has(slot) && slot !== selectedHotbar) emptySlots.push(slot)
              }
              const sourceSlot = handSlot || sourceSlotBefore
              const destinationSlot = emptySlots.find((slot) => slot !== sourceSlot)
              if (destinationSlot === undefined) {
                console.log('[smoke] SKIPPED inventory_move：背包里没有空槽位（9~44 全满），不做隐式交换')
              } else {
                const before = (slotsNow.find((row) => row.slot === sourceSlot) || {}).count || 0
                console.log(
                  `[smoke] inventory_move：${item} ×1 从槽位 ${sourceSlot} → ${destinationSlot}`
                    + `（source 原有 ${before} 个）`,
                )
                const moveResp = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
                  source_slot: sourceSlot,
                  destination_slot: destinationSlot,
                  item,
                  count: 1,
                })
                const moveStarted =
                  moveResp.status === 200 &&
                  moveResp.body.status === 'RUNNING' &&
                  Boolean(moveResp.body.action_id)
                if (!moveStarted) {
                  console.log(`[smoke]    inventory_move 启动失败：${JSON.stringify(moveResp)}`)
                  check(false, `inventory_move 启动必须 200/RUNNING（HTTP ${moveResp.status}）`)
                } else {
                  check(true, `inventory_move 启动 → RUNNING（action_id=${moveResp.body.action_id}）`)
                  const moveTerminal = await waitForActionTerminal(
                    moveResp.body.action_id,
                    'inventory_move 终态事件',
                    30000,
                  )
                  if (!moveTerminal) {
                    check(false, 'inventory_move 未在 30s 内进入终态')
                  } else if (moveTerminal.event !== 'minecraft.action.completed') {
                    check(
                      false,
                      `inventory_move 终态=${moveTerminal.event}`
                        + `（${moveTerminal.error || moveTerminal.reason || '-'}）`,
                    )
                  } else {
                    const moveResult = moveTerminal.result || {}
                    const srcAfter = moveResult.source_after
                    const dstAfter = moveResult.destination_after
                    check(
                      name(moveResult.item) === name(item) &&
                        moveResult.source_slot === sourceSlot &&
                        moveResult.destination_slot === destinationSlot,
                      `move completed（source=${moveResult.source_slot} → dest=${moveResult.destination_slot}）`,
                    )
                    check(
                      Boolean(dstAfter) && name(dstAfter.name) === name(item) && dstAfter.count >= 1,
                      `destination 真的多出了 ${item}（${JSON.stringify(dstAfter)}）`,
                    )
                    check(
                      srcAfter === null || srcAfter.count < before,
                      `source 真的少了（before=${before} → ${JSON.stringify(srcAfter)}）`,
                    )
                    // 第二层：重读槽位表（不从事件里抄结论）
                    const slotsMoved = await waitForValue(async () => {
                      const rows = await readSlots()
                      const dest = rows.find((row) => row.slot === destinationSlot)
                      const src = rows.find((row) => row.slot === sourceSlot)
                      const destOk = dest && name(dest.name) === name(item) && dest.count >= 1
                      const srcOk = !src || src.count < before
                      return destOk && srcOk ? rows : null
                    }, '真实槽位表反映这次搬运', 15000)
                    check(Boolean(slotsMoved), '真实槽位表改变（重读确认，不硬编码 +count）')
                    // 第三层：WorldPerception 输入（inventory 层里应该能看到 destinationSlot 上的物品）
                    const perceivableMove = await waitForValue(async () => {
                      const snap = await snapshot('local')
                      return JSON.stringify(snap).includes(name(item)) ? true : null
                    }, 'WorldPerception 反映搬运结果', 10000)
                    check(Boolean(perceivableMove), 'WorldPerception 输入反映搬运结果')

                    // 把搬走的 1 个搬回去（恢复槽位布局；同物品合并 → 允许）
                    const undo = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
                      source_slot: destinationSlot,
                      destination_slot: sourceSlot,
                      item,
                      count: 1,
                    })
                    if (undo.status === 200 && undo.body.action_id) {
                      await waitForActionTerminal(undo.body.action_id, '撤销搬运终态', 30000)
                      console.log(`[smoke]    已把 ${item} ×1 搬回槽位 ${sourceSlot}`)
                    } else {
                      console.log(`[smoke]    ✗ 撤销搬运失败：${JSON.stringify(undo)}`)
                      check(false, '撤销搬运必须成功（否则不恢复原状）')
                    }
                  }
                }
              }

              // (d) 恢复主手：有原主手 → equip 它（一次交换即可还原布局）；原主手是空手 →
              //     把刚拿到手里的东西整堆搬回原槽（手槽变空 = 空手），没有"放回背包"动作也不编造
              const slotsAfterMove = await readSlots()
              if (heldBefore && heldBefore.name) {
                const back = await request(runtimePort, 'POST', '/minecraft/equip', {
                  item: heldBefore.name,
                })
                if (back.status === 200 && back.body.action_id) {
                  await waitForActionTerminal(back.body.action_id, '恢复主手终态', 30000)
                } else {
                  console.log(`[smoke]    ✗ 恢复主手失败：${JSON.stringify(back)}`)
                  check(false, '恢复原主手必须成功')
                }
              } else {
                const handRow = slotsAfterMove.find(
                  (row) => row.slot === selectedHotbar && name(row.name) === name(item),
                )
                const homeSlot = sourceSlotBefore
                if (handRow && homeSlot !== selectedHotbar) {
                  const put = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
                    source_slot: handRow.slot,
                    destination_slot: homeSlot,
                    item,
                    count: handRow.count,
                  })
                  if (put.status === 200 && put.body.action_id) {
                    await waitForActionTerminal(put.body.action_id, '空手恢复终态', 30000)
                  } else {
                    console.log(`[smoke]    ✗ 空手恢复失败：${JSON.stringify(put)}`)
                    check(false, '空手恢复（整堆搬回原槽）必须成功')
                  }
                } else {
                  console.log('[smoke] SKIPPED 空手恢复：拿在手里的物品原本就在快捷栏手槽上（无法还原空手）')
                }
              }

              // (d2) 夹具清理：把自己 /give 出来的物品从背包里清掉（不留测试痕迹）
              if (seeded) {
                const botName = (await status()).username
                const cleared = await request(runtimePort, 'POST', '/minecraft/chat', {
                  message: `/clear ${botName} ${FIXTURE_ITEM}`,
                })
                const gone = await waitForValue(async () => {
                  const rows = await readSlots()
                  return rows && !rows.some((row) => name(row.name) === name(FIXTURE_ITEM))
                    ? rows
                    : null
                }, '夹具物品已清出背包', 10000)
                check(
                  cleared.status === 200 && Boolean(gone),
                  `/clear 夹具物品 ${FIXTURE_ITEM}（背包里已不存在）`,
                )
              }

              // (e) 逐槽比对：布局 + 主手是否回到测试前
              await sleep(600)
              const slotsFinal = await readSlots()
              const sliceFinal = await readSlice()
              const heldFinal = sliceFinal ? sliceFinal.held_item : null
              check(
                signature(slotsFinal) === signature(slotsBefore),
                `槽位布局已恢复（before=${signature(slotsBefore) || '空'} / `
                  + `after=${signature(slotsFinal) || '空'}）`,
              )
              check(
                name(heldFinal && heldFinal.name) === name(heldBefore && heldBefore.name),
                `主手已恢复（before=${heldBefore ? heldBefore.name : '空'} / `
                  + `after=${heldFinal ? heldFinal.name : '空'}）`,
              )
              console.log(
                `[smoke]    Phase 4D 结束状态：槽位=${signature(slotsFinal) || '空'}；`
                  + `主手=${heldFinal ? `${heldFinal.name}×${heldFinal.count}` : '空'}`,
              )
            }
          }
        }
      }
    }

    // equip / inventory_move 都是 MEDIUM 但**毫秒级**：真实服务器上不存在
    // "挖到一半"那种可取消窗口（Node 单测已用假 bot 覆盖 CANCELLED/TIMEOUT/race/cleanup）。
    console.log('[smoke] SKIPPED equip / inventory_move STOP：动作毫秒级完成，真实服务器上没有可取消窗口')

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
