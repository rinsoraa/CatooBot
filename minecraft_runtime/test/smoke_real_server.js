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
 *   1b. REAL MOVE_TO FALSE-SUCCESS GUARD（Phase 4H.1 §二十七-§三十二）：对**真的不可达**
 *      的目标（不改世界）发 move_to → 必须是结构化失败（path.not_found / path.not_reached），
 *      **绝不允许 completed**；终态之后 goal 必须已清（不再需要 smoke 自己收残留）
 *   2. move_to（远一点）→ STOP → CANCELLED + goal null + isMoving false + 位置停住
 *   3. follow_player（第二个真实客户端当目标）→ 目标走动 → 继续跟 → STOP
 *   4. HARD IDLE BARRIER：确认没有任何前台动作/导航残留，才进入 Phase 4B
 *   5. dig（Phase 4B 硬门禁）：错误 expected_block → block.changed；
 *      真挖 → RUNNING → completed → 三层验证（Action 结果 / 真实世界 / WorldPerception）；
 *      同位置再挖 → block.not_found
 *   6. dig + STOP（附近有"徒手要挖几秒"的方块才跑，否则明确 SKIPPED）
 *   6b. Phase 4I：工具感知 dig（optional expected_tool）—— /give 夹具 → 错误工具同步被拒
 *      （方块不变 / 没有新 Action / 主手没变）→ 自己 equip → 重读主手 → dig(expected_tool)
 *      → 真实世界复核 → 不带 expected_tool 的 legacy dig → 还原临时方块 + 清夹具
 *      （支持 SMOKE_TOOL_ITEM="minecraft:stone_pickaxe"；拿不到工具就 SKIPPED）
 *   7. Phase 4C：place（单方块，六层证据）
 *   8. Phase 4D：inventory/slots → equip（含 already_equipped）→ inventory_move →
 *      重读槽位表 → **恢复原状**（槽位布局 + 主手）→ ensureIdle → disconnect
 *      （背包里没有可换的物品 / 没有空槽 → 明确 SKIPPED，不伪造结论）
 *   9. Phase 4E：container（Chest / Barrel）—— inspect（open → read → close，两次证明没漏窗口）
 *      → withdraw 1 个 → 重读容器与背包 → deposit 放回原槽 → 逐槽比对恢复原状 → 清夹具
 *      （支持 SMOKE_CONTAINER_TARGET="x,y,z" 用操作者自己的箱子；否则就地造临时箱子）
 *  10. Phase 4F：crafting（玩家 2×2）—— /give 木板 → recipe_lookup 拿到 recipe_id（不硬编码）
 *      → craft（RUNNING → completed）→ 重读 inventory 验证产物增加 / 材料减少 → /clear 恢复
 *  11. Phase 4G：3×3 工作台 —— 就地在 bot 旁边放一张临时工作台（记录原方块，最后还原）
 *      → recipe_lookup(chest, table) 拿到 recipe_id → craft(recipe_id, table) → 重读 inventory
 *      → /clear 产物与材料 → 逐槽比对恢复（支持 SMOKE_CRAFTING_TABLE_TARGET="x,y,z"）
 *  12. Phase 4H：掉落物感知 + 单实体拾取 —— 放一块 oak_log → dig 出真实掉落物 →
 *      dropped_items 看到 entity_id/物品/位置/距离 → （可选）真实 STOP（把罐头挪远制造取消窗口）
 *      → pickup → playerCollect + 实体消失 + 背包增加 → /clear 回收 → 逐槽比对恢复
 *      （支持 SMOKE_PICKUP_TARGET="entity_id" + SMOKE_PICKUP_ITEM="minecraft:oak_log"；
 *      上一次中断残留的临时方块用 SMOKE_CLEANUP_BLOCK="x,y,z" 清掉）
 *  13. disconnect
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
const { normalizeItemName, MOVE_DEFAULTS } = require(path.join(RUNTIME_DIR, 'runtime.js'))

const MOVE_RADIUS = MOVE_DEFAULTS.radius

/** 方块名比较用的小助手（runtime 的投影已经是裸名，这里只做防御性去前缀/小写）。 */
const normalizeBlock = (name) =>
  String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/^minecraft:/, '')
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
    let origin = (await status()).position
    console.log(`[smoke] 已进入世界 @ ${JSON.stringify(origin)}`)

    // 罐头的位置会**跨会话保留**（上一次 smoke 可能把它留在了洞穴/深水里），而 dig / place /
    // container / craft / pickup 的夹具都需要正常地表地形。开局先用 /spreadplayers 把罐头放到
    // 附近的地表安全点（原版指令会挑"站在最高方块上"的位置，不会有掉落伤害）；
    // 只动罐头自己，绝不改世界上的任何方块。
    {
      const spawnX = Math.round(origin.x)
      const spawnZ = Math.round(origin.z)
      await request(runtimePort, 'POST', '/minecraft/chat', {
        message: `/spreadplayers ${spawnX} ${spawnZ} 4 48 false @s`,
      })
      const relocated = await waitFor(async () => {
        const now = (await status()).position
        const moved = Math.hypot(now.x - origin.x, now.z - origin.z) > 3
        const resurfaced = now.y > origin.y + 3 || (moved && Math.abs(now.y - origin.y) <= 3)
        return moved || resurfaced ? now : null
      }, '把罐头放到地表安全点', 8000)
      if (relocated) {
        origin = (await status()).position
        console.log(`[smoke] 开局把罐头放到地表安全点 @ ${JSON.stringify(origin)}`)
      } else {
        console.log('[smoke] /spreadplayers 没生效：就地在当前坐标继续（不伪造结论）')
      }
    }

    // 开局主手卫生：Phase 4C 的 place 段用**主手物品**去放，所以主手要是"能放的方块"。
    // 罐头的位置与背包跨会话保留，上一次可能把它留在手持工具的状态（工具放不下去）。
    // 这里只调整罐头**自己**的手持（必要时 /give 一点沙当夹具），并明确打印出来。
    {
      // 正面判定"这个物品像是能放的方块"（比"不是工具"更准：glow_ink_sac 不是方块，
      // 服务器会拒绝放置）。名单只用来决定要不要先把手持换成沙，不参与任何动作校验。
      // 注意两端都要锚定：'^stone' 会把 stone_pickaxe 也当成"石头"（真机上踩过这个坑）。
      const PLACEABLE_LIKE =
        /^(dirt|grass_block|sand|red_sand|gravel|clay|snow|snow_block|cobblestone|stone|deepslate|granite|diorite|andesite|glass|torch|bricks|.+_(log|wood|planks|block|ore|wool|bricks|terracotta|slab|stairs|concrete))$/
      const TOOL_SUFFIX = /_(pickaxe|axe|shovel|hoe|sword)$|^(shears|flint_and_steel|bow|crossbow|fishing_rod|shield|bucket)$/
      const invNow = (await request(runtimePort, 'GET', '/minecraft/inventory')).body
      const hand = invNow.held_item || null
      if (hand && (!PLACEABLE_LIKE.test(normalizeItemName(hand.name)) || TOOL_SUFFIX.test(normalizeItemName(hand.name)))) {
        const candidate = (invNow.items || []).find(
          (row) =>
            PLACEABLE_LIKE.test(normalizeItemName(row.name)) &&
            !TOOL_SUFFIX.test(normalizeItemName(row.name)),
        )
        let pick = candidate ? candidate.name : ''
        if (!pick) {
          const botName = (await status()).username
          if (botName) {
            await request(runtimePort, 'POST', '/minecraft/chat', {
              message: `/give ${botName} minecraft:sand 4`,
            })
            await sleep(800)
            pick = 'minecraft:sand'
          }
        }
        if (pick) {
          await request(runtimePort, 'POST', '/minecraft/equip', { item: pick })
          await sleep(1000)
          console.log(
            `[smoke] 开局主手卫生：主手本来是 ${hand.name}（放不下去）→ 换成 ${pick}`
              + `${candidate ? '' : '（/give 出来的夹具）'}`,
          )
        } else {
          console.log(
            `[smoke] 开局主手是 ${hand.name}，且背包里没有可放的物品：place 段可能 SKIPPED`,
          )
        }
      } else {
        console.log(
          `[smoke] 开局主手：${hand ? `${hand.name}×${hand.count}` : '空手'}`
            + '（place 段会自己判断能不能放）',
        )
      }
    }

    // ---- 1. 近距离 move_to 正向验证（Phase 4H.1：completed 必须真的"在 1.5 格内"）----
    // 真机地形千奇百怪（洞穴/水/悬崖里某些方向就是**没有**非破坏性路径），所以按
    // 方向 × 距离逐个试，只要能拿到一条"真的走通"的路径就够了；失败的方向按结构化
    // 失败如实跳过（旧代码这些方向会因为 2.4.5 的空路径静默 resolve 而假装 completed）。
    const MOVE_DIRECTIONS = [
      [4, 0],
      [-4, 0],
      [0, 4],
      [0, -4],
      [3, 3],
      [-3, 3],
      [3, -3],
      [-3, -3],
      [6, 0],
      [-6, 0],
      [0, 6],
      [0, -6],
    ]
    let moved = null
    let movedDir = null
    let lastMoveFailure = null
    const tryPositiveMoves = async () => {
      for (const [dx, dz] of MOVE_DIRECTIONS) {
        if (moved) return
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
          const result = terminal.result || {}
          const settled = (await status()).position
          const movedBy = Math.hypot(settled.x - origin.x, settled.z - origin.z)
          if (movedBy < 1) {
            // "报到达但根本没挪动"不算正向证据（Phase 4H.1 之前正是这种假成功）→ 换方向
            lastMoveFailure = `completed 但只挪了 ${movedBy.toFixed(2)} 格`
            continue
          }
          moved = { ...resp.body, result }
          movedDir = [dx, dz]
          return
        }
        lastMoveFailure = terminal
          ? `${terminal.event}${terminal.code ? `/${terminal.code}` : ''}${terminal.error ? `（${terminal.error}）` : ''}`
          : '终态事件超时'
      }
    }

    // 方块交互/导航之前先把罐头挪到**干燥、开阔**的落脚点（洞穴/水里导航常常没有
    // 非破坏性路径）。这条只在正向段全军覆没时用一次，并且**只改罐头自己**（/tp），
    // 不动世界上的任何方块。
    const relocateForMoveTests = async () => {
      const local = (await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=local')).body
      const columns = (local.blocks && local.blocks.local && local.blocks.local.columns) || []
      const FLUID = /water|lava|seagrass|kelp|bubble|magma|ice/i
      const byColumn = new Map()
      for (const col of columns) {
        if (!col.pos || !col.name || typeof col.distance !== 'number') continue
        const key = `${col.pos.x},${col.pos.z}`
        const current = byColumn.get(key)
        if (!current || col.pos.y > current.pos.y) byColumn.set(key, col)
      }
      const spot = [...byColumn.values()]
        .filter((col) => !FLUID.test(col.name))
        .filter((col) => col.distance >= 3 && col.distance <= 24)
        .sort((a, b) => a.distance - b.distance)[0]
      if (!spot) {
        console.log('[smoke] 附近没有干燥落脚点：就地在当前坐标继续')
        return false
      }
      const x = spot.pos.x + 0.5
      const y = spot.pos.y + 1
      const z = spot.pos.z + 0.5
      await request(runtimePort, 'POST', '/minecraft/chat', { message: `/tp @s ${x} ${y} ${z}` })
      const ok = await waitFor(async () => {
        const now = (await status()).position
        return Math.hypot(now.x - x, now.z - z) < 3 && Math.abs(now.y - y) < 4
      }, '挪到干燥落脚点', 8000)
      console.log(
        ok
          ? `[smoke] move_to positive：已把罐头挪到 (${Math.round(x)}, ${Math.round(y)}, ${Math.round(z)})（${spot.name}）再试`
          : '[smoke] move_to positive：传送没生效，就地在当前坐标再试',
      )
      return ok
    }

    await tryPositiveMoves()
    if (!moved) {
      console.log(
        `[smoke] move_to positive 第一轮全部没有非破坏性路径（最后：${lastMoveFailure}）`
          + ' → 挪到干燥落脚点再试一轮',
      )
      await relocateForMoveTests()
      origin = (await status()).position
      await tryPositiveMoves()
    }

    check(Boolean(moved), `move_to positive → completed（${moved ? `action_id=${moved.action_id}` : `所有方向都失败，最后：${lastMoveFailure}`}）`)
    console.log(`[smoke] ✓ move_to positive → RUNNING → completed（${moved ? `${movedDir} 方向` : '未完成'}）`)
    if (moved) {
      // §五/§二十一：唯一硬门禁 —— distance_to_target 是**重新读到的实际位置**算出来的
      // （GoalNear 口径：罐头占的方块格 → 目标方块格），不是 Pathfinder 的预测值。
      check(
        typeof moved.result.distance_to_target === 'number' &&
          moved.result.distance_to_target <= MOVE_RADIUS,
        `positive final distance <= ${MOVE_RADIUS}（GoalNear 口径 ${moved.result.distance_to_target} 格，`
          + `原始浮点 ${moved.result.raw_distance_to_target} 格）`,
      )
      const positiveStatus = await status()
      const positivePathfinder = positiveStatus.pathfinder || {}
      check(
        positivePathfinder.goal === null && positivePathfinder.moving === false,
        'move_to positive → goal cleanup = PASS（成功后 goal=null / isMoving=false）',
      )
      // 独立量测（不采信动作自己报的数）：smoke 自己从 /status 读位置再算一遍
      const positiveTarget = {
        x: origin.x + movedDir[0],
        y: origin.y,
        z: origin.z + movedDir[1],
      }
      const positiveActual = Math.hypot(
        positiveStatus.position.x - positiveTarget.x,
        positiveStatus.position.y - positiveTarget.y,
        positiveStatus.position.z - positiveTarget.z,
      )
      check(
        positiveActual <= MOVE_RADIUS + 2,
        `move_to positive → 独立量测也同意到达（实际 ${positiveActual.toFixed(2)} 格）`,
      )
    }

    // ---- 2. 远距离 move_to → STOP（保持 Phase 3C 契约）----
    if (moved) {
      const baseDir = Math.hypot(movedDir[0], movedDir[1]) || 1
      const bux = movedDir[0] / baseDir
      const buz = movedDir[1] / baseDir
      const directions = [
        [bux, buz],
        [-bux, -buz],
        [-buz, bux],
        [buz, -bux],
        [(bux + buz) / Math.SQRT2, (buz - bux) / Math.SQRT2],
        [(bux - buz) / Math.SQRT2, (buz + bux) / Math.SQRT2],
        [(-bux + buz) / Math.SQRT2, (-buz - bux) / Math.SQRT2],
        [(-bux - buz) / Math.SQRT2, (-buz + bux) / Math.SQRT2],
      ]
      let stopVerified = false
      for (const [ux, uz] of directions) {
        const now = (await status()).position
        const moveResp = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: now.x + ux * 25,
          y: now.y,
          z: now.z + uz * 25,
        })
        if (
          !(moveResp.status === 200 && moveResp.body.status === 'RUNNING' && moveResp.body.action_id)
        ) {
          check(false, `远距离 move_to 启动被拒（${JSON.stringify(moveResp.body)}）`)
          break
        }
        const moveId = moveResp.body.action_id
        const moving = await waitFor(async () => {
          const snap = await status()
          return Boolean(snap.pathfinder && snap.pathfinder.moving)
        }, '导航开始', 8000)
        const finishedEarly = events.find(
          (row) => TERMINAL_EVENTS.includes(row.event) && row.action_id === moveId,
        )
        if (finishedEarly) {
          console.log(
            `[smoke] 远距离 move_to（方向 ${ux},${uz}）在被取消前就进入终态`
              + `（${finishedEarly.event}${finishedEarly.code ? `/${finishedEarly.code}` : ''}）`
              + ' —— 这个方向没有可取消窗口，换方向重试',
          )
          continue
        }
        if (!moving) {
          const terminal = await waitForActionTerminal(moveId, '远距离 move_to 终态', 20000)
          console.log(
            `[smoke] 远距离目标（方向 ${ux},${uz}）未能开始移动`
              + `（${terminal ? `${terminal.event}${terminal.code ? `/${terminal.code}` : ''}` : '终态超时'}）——换方向重试`,
          )
          continue
        }
        const stop = await request(runtimePort, 'POST', '/minecraft/stop', {})
        check(stop.body.cancelled.includes(moveId), 'STOP 取消了移动中的 move_to')
        const terminal = await waitForActionTerminal(moveId, 'move_to cancelled 事件', 10000)
        check(
          Boolean(terminal) && terminal.event === 'minecraft.action.cancelled',
          `move_to 终态 = cancelled（${terminal ? terminal.event : '超时'}）`,
        )
        const settledStop = await waitForValue(async () => {
          const snap = await status()
          return snap.pathfinder.goal === null && snap.pathfinder.moving === false ? snap : null
        }, 'STOP 后回到静止', 8000)
        check(Boolean(settledStop), 'goal == null 且 isMoving == false（STOP 后回到静止）')
        const stopped = settledStop || (await status())
        check(stopped.pathfinder.goal === null, 'goal == null')
        check(stopped.pathfinder.moving === false, 'isMoving == false')
        // 硬证据是 goal == null + isMoving == false。位置检查改成"**停稳之后**再测一段"
        // （STOP 瞬间可能还在空中收尾；水里/冰面上会有环境滑动，所以把量到的数打出来）。
        const driftA = (await status()).position
        await sleep(600)
        const driftB = (await status()).position
        const drift = distance2d(driftB, driftA)
        check(drift <= 0.6, `停止后位置不再漂移（实测 ${drift.toFixed(2)} 格 / 阈值 0.6）`)
        console.log('[smoke] ✓ move_to STOP → CANCELLED + goal=null + isMoving=false + 位置稳定')
        stopVerified = true
        break
      }
      if (!stopVerified) {
        console.log(
          '[smoke] SKIPPED move_to STOP：四个方向都没拿到可取消窗口'
            + '（真机地形：这个方向没有非破坏性路径）'
            + ' —— 取消语义由 4H 的 pickup STOP 段与 Node 单测独立验证',
        )
      }
      // §三十三：**不再用"零距离 move_to"清残留 Goal** —— 任何终态（成功/失败/取消）
      // 都由 runtime 自己收掉导航意图，这里只做验证。
      const afterStop = (await status()).pathfinder || {}
      check(
        afterStop.goal === null && afterStop.moving === false,
        'move_to goal cleanup = PASS（STOP 段之后没有残留 Goal / 没有在移动）',
      )
    }

    // ---- 2b. REAL MOVE_TO FALSE-SUCCESS GUARD（Phase 4H.1 的核心证据，§二十七-§三十二）----
    // 目标必须**真的不可达**（罐头不能挖、不能放、不能飞），而且**不改变世界**（§二十八）。
    // 旧代码在这些目标上会因为 mineflayer-pathfinder 2.4.5 的 goto() 空路径静默 resolve
    // 而报 completed（真机实测：completed 时还在 28 格外）。
    {
      const base = (await status()).position
      // 守卫的目标是"找不到路"，但我们不希望它把罐头带进洞穴/远走 —— 结束后 tp 回原位，
      // 后面的段落（dig / place / craft / pickup）地形环境不受影响。
      const guardOrigin = { x: base.x, y: base.y, z: base.z }
      const candidates = [
        { label: '脚下 20 格的实心岩层', x: base.x, y: base.y - 20, z: base.z },
        { label: '斜下方 30 格外的岩层', x: base.x + 30, y: base.y - 20, z: base.z },
        { label: '头顶 30 格的空气', x: base.x, y: base.y + 30, z: base.z },
      ]
      let guard = null
      const guardNotes = []
      for (const candidate of candidates) {
        const resp = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: candidate.x,
          y: candidate.y,
          z: candidate.z,
        })
        if (!(resp.status === 200 && resp.body.status === 'RUNNING' && resp.body.action_id)) {
          guardNotes.push(`${candidate.label}：启动被拒 ${JSON.stringify(resp.body)}`)
          continue
        }
        const terminal = await waitForActionTerminal(
          resp.body.action_id,
          `false-success guard 终态（${candidate.label}）`,
          45000,
        )
        if (!terminal) {
          guardNotes.push(`${candidate.label}：终态事件超时`)
          continue
        }
        if (terminal.event === 'minecraft.action.completed') {
          // completed 只有在"罐头其实没到"时才算假成功 —— 这里用 smoke **自己**从 /status
          // 读到的实际位置独立量测（不采信动作自己报的距离）。如果这个候选其实可达
          // （比如岩层里正好有洞穴），那就换下一个候选，不算假成功。
          const arrived = (await status()).position
          const actualDistance = Math.hypot(
            arrived.x - candidate.x,
            arrived.y - candidate.y,
            arrived.z - candidate.z,
          )
          if (actualDistance <= MOVE_RADIUS + 2) {
            guardNotes.push(
              `${candidate.label}：其实可达（completed 且实际 ${actualDistance.toFixed(2)} 格）`,
            )
            continue
          }
          check(
            false,
            `move_to false-success guard：completed 但罐头实际在 ${actualDistance.toFixed(2)} 格外`
              + `（动作自己报 ${terminal.result && terminal.result.distance_to_target} 格）`
              + ' —— 假成功回来了',
          )
          guard = { candidate, terminal, falseSuccess: true }
          break
        }
        const code = terminal.code || ''
        if (
          terminal.event === 'minecraft.action.failed' &&
          (code === 'path.not_found' || code === 'path.not_reached')
        ) {
          guard = { candidate, terminal }
          break
        }
        guardNotes.push(`${candidate.label} → ${terminal.event}${code ? `/${code}` : ''}`)
      }

      if (guard && guard.falseSuccess) {
        // 上面已经 check(false)，这里不再重复
      } else if (guard) {
        const detail = guard.terminal.detail || {}
        console.log('[smoke] ✓ move_to false-success guard')
        console.log('  Pathfinder finished/empty-path scenario（目标：不可达）')
        console.log(
          `  → NOT SUCCEEDED（${guard.terminal.event} / ${guard.terminal.code}：`
            + `${guard.terminal.error || '-'}）`,
        )
        check(true, 'move_to false-success guard：NOT SUCCEEDED（结构化失败，不是 completed）')
        check(
          guard.terminal.event === 'minecraft.action.failed' &&
            (guard.terminal.code === 'path.not_found' || guard.terminal.code === 'path.not_reached'),
          `结构化失败 = PASS（${guard.terminal.event} / ${guard.terminal.code}，目标：${guard.candidate.label}）`,
        )
        check(
          detail && typeof detail.distance_to_target === 'number' && detail.distance_to_target >= 5,
          `失败 detail 如实带距离（${detail && detail.distance_to_target} 格，半径 ${detail && detail.radius}）`,
        )
        console.log(`  → structured failure：${JSON.stringify(detail)}`)
        // 失败终态之后同样不该有残留导航意图（§三十三）。给一个**有界**的等待：
        // mineflayer 的一次 in-flight A* 结果可能在这一瞬间把 path 又填回来（几十毫秒内自愈）。
        const guardSettled = await waitForValue(async () => {
          const snap = await status()
          return snap.pathfinder.goal === null && snap.pathfinder.moving === false ? snap : null
        }, '失败终态后回到静止', 4000)
        check(
          Boolean(guardSettled),
          'move_to goal cleanup = PASS（失败终态之后没有残留 Goal / 没有在移动）',
        )
      } else {
        check(
          false,
          `move_to false-success guard：没有得到结构化失败（${guardNotes.join('；')}）`
            + ' —— §三十二：这一项不允许 SKIPPED',
        )
      }

      // 不管守卫结果如何，都把罐头放回原位（只动自己，不动世界）
      const stray = (await status()).position
      if (
        Math.hypot(stray.x - guardOrigin.x, stray.z - guardOrigin.z) > 2 ||
        Math.abs(stray.y - guardOrigin.y) > 2
      ) {
        await request(runtimePort, 'POST', '/minecraft/chat', {
          message: `/tp @s ${guardOrigin.x} ${guardOrigin.y} ${guardOrigin.z}`,
        })
        await sleep(900)
        console.log('[smoke] false-success guard：已把罐头 tp 回原位（守卫不带偏后续段落的地形）')
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
      // 停稳之后再测一段（下落/惯性收尾不算"还在走"；goal/isMoving 才是硬证据）
      await sleep(400)
      const followSettledA = (await status()).position
      await sleep(400)
      const followSettledB = (await status()).position
      check(
        distance2d(followSettledB, followSettledA) <= 0.2,
        '跟随 STOP 后停稳不再漂移',
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
    // 方块交互（dig / place / container）之前：把罐头挪到**干燥实地**。
    // 水里或水边放方块会被服务器拒绝（水会流回目标格，客户端看到的"空气"其实是水），
    // dig 也常被水干扰 → 先用 /tp（bot 有 op）挪到最近的"实心且非流体"柱子上；
    // 挪不动就照旧继续（只记日志，不伪造结论）。
    const say = (message) => request(runtimePort, 'POST', '/minecraft/chat', { message })
    const relocateToDryLand = async () => {
      const local = await snapshot('local')
      const columns = (local.blocks && local.blocks.local && local.blocks.local.columns) || []
      const FLUID = /water|lava|seagrass|kelp|bubble|magma|ice/i
      const spot = columns
        .filter((col) => col.pos && typeof col.distance === 'number' && col.name)
        .filter((col) => col.distance >= 4 && col.distance <= 24)
        .filter((col) => !FLUID.test(col.name))
        .sort((a, b) => a.distance - b.distance)[0]
      if (!spot) {
        console.log('[smoke] 附近没有干燥落脚点：就地继续（方块交互可能被水干扰）')
        return
      }
      const x = spot.pos.x + 0.5
      const y = spot.pos.y + 1
      const z = spot.pos.z + 0.5
      await say(`/tp @s ${x} ${y} ${z}`)
      const moved = await waitFor(async () => {
        const now = (await status()).position
        return Math.hypot(now.x - x, now.z - z) < 3 && Math.abs(now.y - y) < 4
      }, '挪到干燥实地', 8000)
      if (moved) {
        console.log(
          `[smoke] 已把罐头挪到干燥实地 (${Math.round(x)}, ${Math.round(y)}, ${Math.round(z)})`
            + `（${spot.name}）`,
        )
      } else {
        console.log('[smoke] 传送没生效：就地继续（不伪造结论）')
      }
    }
    await relocateToDryLand()

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
        // 参考方块必须是**实心可放的表面**：水/树叶/草/花这类"看着像方块"的东西
        // 会被服务器拒绝（真机见过 "the block is still air"）。名单只用于挑目标，
        // 不参与任何动作校验。
        const SOLID_REF =
          /^(stone|cobblestone|dirt|grass_block|sand|red_sand|gravel|clay|snow_block|deepslate|granite|diorite|andesite|sandstone|.*_log|.*_planks|.*_terracotta|.*_concrete|.*_wool|bricks|.*_ore)$/
        const candidates = columnsNow
          .filter((col) => col.pos && col.distance !== undefined)
          .filter((col) => col.distance >= 1.5 && col.distance <= 3.5) // 站得开一点，又能在 5 格内够到上方
          .filter((col) => SOLID_REF.test(normalizeBlock(col.name)))
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

    // ---- 6b. Phase 4I：工具感知 dig（optional expected_tool） ----
    // 全部**显式**：/give 夹具 → 错误工具先被拒（方块不变）→ 自己 equip → 重读主手 →
    // dig(expected_tool) → 真实世界复核 → 还原。dig 全程只"校验主手"，绝不替用户换工具。
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4I：runtime 未空闲，工具感知 dig 硬门禁不能执行')
    } else {
      const PICKAXE = (process.env.SMOKE_TOOL_ITEM || 'minecraft:stone_pickaxe').trim()
      const invNow = async () => (await request(runtimePort, 'GET', '/minecraft/inventory')).body
      const handNow = async () => (await invNow()).held_item || null
      const slotRows = async () =>
        ((await request(runtimePort, 'GET', '/minecraft/inventory/slots')).body || {}).slots || []
      const slotSignature = async () =>
        (await slotRows())
          .map((row) => `${row.slot}:${normalizeItemName(row.name)}×${row.count}`)
          .sort()
          .join('|')
      const botName = (await status()).username
      const signatureBefore4I = await slotSignature()

      // 夹具：确保背包里有一把镐（操作者也可以用 SMOKE_TOOL_ITEM 指定别的工具）
      let toolReady = (await invNow()).items?.some(
        (row) => normalizeItemName(row.name) === normalizeItemName(PICKAXE),
      )
      let gaveTool = false
      if (!toolReady && botName) {
        await say(`/give ${botName} ${PICKAXE} 1`)
        gaveTool = true
        toolReady = Boolean(
          await waitForValue(async () => {
            const inv = await invNow()
            return (inv.items || []).some(
              (row) => normalizeItemName(row.name) === normalizeItemName(PICKAXE),
            )
              ? inv
              : null
          }, `${PICKAXE} 进入背包`, 8000),
        )
      }

      if (!toolReady) {
        console.log(
          `[smoke] SKIPPED Phase 4I：拿不到测试用工具 ${PICKAXE}`
            + '（/give 不可用且背包里本来没有）—— 不伪造结论',
        )
      } else {
        // 临时石头块（记录原方块，全部结束后原样还原）：位置在罐头脚边/胸口高度的空气格
        const origin4I = (await status()).position
        const base4I = {
          x: Math.round(origin4I.x),
          y: Math.round(origin4I.y),
          z: Math.round(origin4I.z),
        }
        const offsets4I = [
          [1, 0, 0],
          [0, 0, 1],
          [-1, 0, 0],
          [0, 0, -1],
          [1, 1, 0],
          [0, 1, 1],
          [1, 0, 1],
        ]
        let spot4I = null
        let spotOrigin = 'air'
        for (const [dx, dy, dz] of offsets4I) {
          const candidate = { x: base4I.x + dx, y: base4I.y + dy, z: base4I.z + dz }
          const raw = await blockAt(candidate)
          if (raw !== null && normalizeBlock(raw) !== 'air' && normalizeBlock(raw) !== 'water') {
            continue
          }
          await say(`/setblock ${candidate.x} ${candidate.y} ${candidate.z} minecraft:stone`)
          const placed4I = await waitForValue(async () => {
            const now = normalizeBlock(await blockAt(candidate))
            return now === 'stone' ? now : null
          }, '临时石头块出现在感知里', 4000)
          if (placed4I) {
            spot4I = candidate
            spotOrigin = raw === null ? 'air' : raw
            break
          }
          await say(`/setblock ${candidate.x} ${candidate.y} ${candidate.z} ${raw || 'air'}`)
        }
        if (!spot4I) {
          console.log('[smoke] SKIPPED Phase 4I：身边放不出临时石头块（不伪造结论）')
        } else {
          console.log(
            `[smoke] 4I：在 (${spot4I.x},${spot4I.y},${spot4I.z}) 放了临时 stone`
              + `（原方块 ${spotOrigin}）→ 结束后还原；测试工具 ${PICKAXE}`,
          )
          // 注意口径：runtime 的 dig 比的是**原始 block.name**（这台服务器上是裸名 stone），
          // 所以 expected_block 要给裸名；expected_tool 那边两层都会规范化（两种写法都行）。
          const digArgs = (extra) => ({
            x: spot4I.x,
            y: spot4I.y,
            z: spot4I.z,
            expected_block: 'stone',
            ...extra,
          })

          // (a) 错误工具：手里不是它 → 必须**同步拒绝**，方块不动，也没有新的 dig Action
          const handBefore4I = await handNow()
          const wrongTarget = normalizeItemName((handBefore4I || {}).name || '') === normalizeItemName(PICKAXE)
            ? 'minecraft:netherite_pickaxe' // 极端情况下换个肯定不在手里的工具
            : PICKAXE
          const rejected = await request(runtimePort, 'POST', '/minecraft/dig', digArgs({
            expected_tool: wrongTarget,
          }))
          const rejectedCode = rejected.body && rejected.body.error && rejected.body.error.code
          // §十二 的两种分类都算"身份不对、拒绝"：手里是别的物品 → held_item_changed（409）；
          // 空手 → held.item_missing（400）。两者都**不会**开始挖、也不会替你去拿工具。
          check(
            (rejected.status === 409 && rejectedCode === 'held.item_changed') ||
              (rejected.status === 400 && rejectedCode === 'held.item_missing'),
            `expected_tool mismatch = PASS（HTTP ${rejected.status} / ${rejectedCode}）`,
          )
          const rejectedDetail = rejected.body && rejected.body.error && rejected.body.error.detail
          check(
            Boolean(rejectedDetail) &&
              normalizeItemName(rejectedDetail.expected) === normalizeItemName(wrongTarget) &&
              normalizeItemName(rejectedDetail.actual || '') ===
                normalizeItemName((handBefore4I || {}).name || ''),
            `mismatch detail 带 expected/actual（${JSON.stringify(rejectedDetail)}）`,
          )
          check(
            normalizeBlock(await blockAt(spot4I)) === 'stone',
            'expected_tool mismatch 之后方块没有变化',
          )
          const idleAfterReject = await idleState()
          check(
            idleAfterReject.idle,
            'expected_tool mismatch 没有启动任何 dig Action（runtime 仍然 IDLE）',
          )
          const handAfterReject = await handNow()
          check(
            normalizeItemName((handAfterReject || {}).name || '') ===
              normalizeItemName((handBefore4I || {}).name || ''),
            'no auto-equip = PASS（被拒之后主手一个物品都没变）',
          )

          // (b) 显式 EQUIP → completed → 重读主手（§二十九：绝不隐式调用工具动作）
          const equipResp = await request(runtimePort, 'POST', '/minecraft/equip', { item: PICKAXE })
          if (!(equipResp.status === 200 && equipResp.body.status === 'RUNNING' && equipResp.body.action_id)) {
            check(false, `equip 测试工具必须 200/RUNNING（HTTP ${equipResp.status}）`)
          } else {
            const equipTerminal = await waitForActionTerminal(
              equipResp.body.action_id,
              'equip 终态',
              30000,
            )
            check(
              Boolean(equipTerminal) && equipTerminal.event === 'minecraft.action.completed',
              `equip 自己先完成（${equipTerminal && equipTerminal.event}）`,
            )
            const handNow2 = await handNow()
            check(
              normalizeItemName((handNow2 || {}).name || '') === normalizeItemName(PICKAXE),
              `重读主手 = ${PICKAXE}（得到 ${(handNow2 || {}).name}）`,
            )

            // (c) 真正工具感知的 dig：RUNNING → completed → 世界真的变了
            const digResp = await request(runtimePort, 'POST', '/minecraft/dig', digArgs({
              expected_tool: PICKAXE,
            }))
            if (!(digResp.status === 200 && digResp.body.status === 'RUNNING' && digResp.body.action_id)) {
              console.log(`[smoke]    工具感知 dig 响应：${JSON.stringify(digResp.body)}`)
              check(false, `工具感知 dig 启动必须 200/RUNNING（HTTP ${digResp.status}）`)
            } else {
              check(true, 'real tool-aware dig → RUNNING')
              const terminal = await waitForActionTerminal(
                digResp.body.action_id,
                '工具感知 dig 终态',
                60000,
              )
              if (!terminal || terminal.event !== 'minecraft.action.completed') {
                check(
                  false,
                  `工具感知 dig 终态是 ${terminal && terminal.event}`
                    + `（${(terminal && (terminal.error || terminal.reason)) || '-'}）`,
                )
              } else {
                const result = terminal.result || {}
                check(
                  result.block_before === 'stone' && normalizeBlock(result.block_after) !== 'stone',
                  `real tool-aware dig = PASS（${result.block_before} → ${result.block_after}）`,
                )
                check(
                  normalizeItemName(result.tool_expected || '') === normalizeItemName(PICKAXE) &&
                    normalizeItemName(result.tool_actual || '') === normalizeItemName(PICKAXE),
                  `expected_tool match = PASS（tool_expected=${result.tool_expected} / `
                    + `tool_actual=${result.tool_actual}）`,
                )
                check(
                  Boolean(result.tool_actual_after) &&
                    normalizeItemName(result.tool_actual_after.name) === normalizeItemName(PICKAXE) &&
                    !('type' in result.tool_actual_after) &&
                    !('slot' in result.tool_actual_after),
                  `结果带动作后的主手快照（${JSON.stringify(result.tool_actual_after)}，无 raw item）`,
                )
                const gone = await waitForValue(async () => {
                  const now = normalizeBlock(await blockAt(spot4I))
                  // 挖掉之后读到的是 "air"（或 null）——都要当成真值，别让空字符串把等待卡满
                  return now !== 'stone' ? now || 'air' : null
                }, '真实世界里石头已经没了', 10000)
                check(Boolean(gone), `block changed = PASS（该位置现在是 ${gone || '未知'}）`)
                let stable = 0
                for (let round = 0; round < 3; round += 1) {
                  await sleep(1000)
                  // blockAt 在"这一列没有方块"时返回 null → 归一成 air 再比（挖掉之后本来就该是 air）
                  const nowName = normalizeBlock(await blockAt(spot4I)) || 'air'
                  if (nowName === gone) stable += 1
                }
                const handAfterDig = await handNow()
                check(
                  stable === 3 &&
                    normalizeItemName((handAfterDig || {}).name || '') === normalizeItemName(PICKAXE),
                  `inventory/perception = PASS（感知连续 3 次一致 ${stable}/3；主手仍是 `
                    + `${(handAfterDig || {}).name}）`,
                )
              }
            }

            // (d) 向后兼容：同一个位置再放一块 stone，用**不带 expected_tool** 的 dig 挖掉
            await say(`/setblock ${spot4I.x} ${spot4I.y} ${spot4I.z} minecraft:stone`)
            await sleep(600)
            if (normalizeBlock(await blockAt(spot4I)) === 'stone') {
              const legacy = await request(runtimePort, 'POST', '/minecraft/dig', digArgs({}))
              if (legacy.status === 200 && legacy.body.action_id) {
                const legacyTerminal = await waitForActionTerminal(
                  legacy.body.action_id,
                  'legacy dig 终态',
                  60000,
                )
                const legacyResult = (legacyTerminal && legacyTerminal.result) || {}
                check(
                  Boolean(legacyTerminal) &&
                    legacyTerminal.event === 'minecraft.action.completed' &&
                    legacyResult.tool_expected === null &&
                    legacyResult.tool_actual === null,
                  `backward-compatible dig = PASS（不带 expected_tool 照样挖：`
                    + `${legacyResult.block_before} → ${legacyResult.block_after}，`
                    + `tool_expected=${legacyResult.tool_expected}）`,
                )
              } else {
                check(false, `legacy dig 启动失败（HTTP ${legacy.status}）`)
              }
            } else {
              console.log('[smoke]    legacy dig 的临时方块没放上，跳过这一段（不伪造）')
            }
          }

          // (e) 收尾：还原临时方块 + 清掉夹具工具 + 逐槽比对
          await say(`/setblock ${spot4I.x} ${spot4I.y} ${spot4I.z} ${spotOrigin}`)
          await sleep(500)
          console.log(
            `[smoke] 4I：临时方块已还原（(${spot4I.x},${spot4I.y},${spot4I.z}) → ${spotOrigin}）`,
          )
          // 只清**这段自己 /give 出来**的夹具；本来就有的工具绝不能替操作者清掉
          if (botName && gaveTool) {
            await say(`/clear ${botName} ${PICKAXE}`)
          } else {
            console.log('[smoke] 4I：这把工具原本就在背包里 → 不清掉（只清自己造的夹具）')
          }
          // 主手还原：这段自己 equip 过工具，结束时要还原成进来时的样子
          const handWas4I =
            handBefore4I && handBefore4I.name ? normalizeItemName(handBefore4I.name) : null
          if (handWas4I && handWas4I !== normalizeItemName(PICKAXE)) {
            await request(runtimePort, 'POST', '/minecraft/equip', { item: handWas4I })
            await sleep(800)
            console.log(`[smoke] 4I：主手已还原成 ${handWas4I}`)
          } else if (!handWas4I && gaveTool) {
            console.log('[smoke] 4I：进来时是空手且工具是这段给的 → /clear 已让主手回到空手')
          } else {
            console.log('[smoke] 4I：进来时主手就是这个工具 → 保持不变')
          }
          // 背包比对：硬门禁是"**没有丢东西**"（夹具工具已清 / 原有物品一个不少）。
          // 挖 stone 会掉 cobblestone 并被罐头顺手捡起（vanilla 行走拾取）——那是真实行为，
          // 如实打印出来，不当失败，也不静默放过。
          const parseSig = (sig) =>
            new Map(
              (sig ? sig.split('|') : []).filter(Boolean).map((entry) => {
                const [slot, rest] = entry.split(':')
                const [item, count] = rest.split('×')
                return [`${slot}:${item}`, Number.parseInt(count, 10)]
              }),
            )
          const before4IMap = parseSig(signatureBefore4I)
          const after4IMap = parseSig(await slotSignature())
          const lost4I = []
          for (const [key, count] of before4IMap) {
            if ((after4IMap.get(key) || 0) < count) lost4I.push(`${key} ×${count}→${after4IMap.get(key) || 0}`)
          }
          const gained4I = []
          for (const [key, count] of after4IMap) {
            const was = before4IMap.get(key) || 0
            if (count > was) gained4I.push(`${key} ×${was}→${count}`)
          }
          check(lost4I.length === 0, `4I inventory restored = PASS（原有物品没丢；before=${signatureBefore4I || '空'}）`)
          if (lost4I.length > 0) console.log(`[smoke]    ✗ 4I 丢了/少了原有物品：${lost4I.join('、')}`)
          if (gained4I.length > 0) {
            console.log(
              `[smoke] 4I 备注：背包多出 ${gained4I.join('、')}`
                + '（挖 stone 掉的 cobblestone 被罐头顺手捡起 —— vanilla 行为，非本段夹具）',
            )
          }
          const idle4I = await idleState()
          check(idle4I.idle, 'ensureIdle = PASS（工具感知 dig 之后 runtime 回到 IDLE）')
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
                    // 第三层：WorldPerception 输入是不是**新鲜**的。
                    // 注意口径：感知快照里没有 inventory 层（只有 self.held_item + 方块层），
                    // 所以这里比对"感知的 self.held_item == 重读的真实主手"——搬运只动背包槽位、
                    // 不动方块，感知能反映的就是这个（旧写法在快照 JSON 里搜物品名，
                    // 只有"被搬的正好是手持物"时才碰巧通过）。
                    const freshPerception = await waitForValue(async () => {
                      const snap = await snapshot('local')
                      const held = ((await request(runtimePort, 'GET', '/minecraft/inventory')).body || {})
                        .held_item
                      const selfHeld =
                        snap && snap.self && snap.self.held_item
                          ? { name: snap.self.held_item }
                          : null
                      const sameName = held && selfHeld ? name(held.name) === name(selfHeld.name) : !held && !selfHeld
                      return sameName ? true : null
                    }, 'WorldPerception 的 self 与真实主手一致', 10000)
                    check(Boolean(freshPerception), 'WorldPerception 输入反映搬运结果')

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
              // 进来时是空手 → 没有"空手"这个物品可以 equip，本段上面已经如实 SKIPPED；
              // 这里不再把"主手还拿着本段自己换上去的物品"判成失败（那是本段自己的遗留）。
              check(
                name(heldFinal && heldFinal.name) === name(heldBefore && heldBefore.name) ||
                  !(heldBefore && heldBefore.name),
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

    // ---- 7. Phase 4E：container（inspect → withdraw → deposit → 恢复原状） ----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4E：runtime 未空闲，container 硬门禁不能执行')
    } else {
      const name = (value) => normalizeItemName(value || '')
      const FIXTURE_ITEM = 'minecraft:oak_planks'
      const FIXTURE_COUNT = 3
      const readSlots = async () => {
        const resp = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        return resp.status === 200 && resp.body ? resp.body.slots || [] : []
      }
      const signature = (rows) =>
        rows
          .map((row) => `${row.slot}:${name(row.name)}×${row.count}`)
          .sort((a, b) => Number.parseInt(a, 10) - Number.parseInt(b, 10))
          .join('|')
      const inspect = (pos) =>
        request(runtimePort, 'POST', '/minecraft/container_inspect', {
          x: pos.x,
          y: pos.y,
          z: pos.z,
        })
      const say = (message) =>
        request(runtimePort, 'POST', '/minecraft/chat', { message })

      const inventoryBeforeAll = await readSlots()
      const playerSignatureBefore = signature(inventoryBeforeAll)

      // (a) 找容器：优先操作者指定的；否则在 bot 旁边找一个空气格放临时箱子
      let containerPos = null
      let containerOrigin = 'air'
      let createdFixture = false
      const override = (process.env.SMOKE_CONTAINER_TARGET || '').trim()
      if (override) {
        const [x, y, z] = override.split(',').map((value) => Number.parseInt(value, 10))
        if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
          containerPos = { x, y, z }
          console.log(`[smoke] container：用操作者指定的目标 (${x},${y},${z})`)
        } else {
          console.log(`[smoke] SMOKE_CONTAINER_TARGET 格式不对（应为 "x,y,z"）：${override}`)
        }
      } else {
        const origin = (await status()).position
        const base = { x: Math.round(origin.x), y: Math.round(origin.y), z: Math.round(origin.z) }
        // 优先 bot 头部/上方（几乎总是空气），再退到脚边；只覆盖 air / water ——
        // 其它方块一律不动，绝不在操作者的世界里乱改
        const offsets = [
          [1, 1, 0],
          [0, 1, 1],
          [1, 1, 1],
          [-1, 1, 0],
          [0, 1, -1],
          [0, 2, 0],
          [0, 3, 0],
          [1, 0, 0],
          [0, 0, 1],
          [-1, 0, 0],
          [0, 0, -1],
        ]
        for (const [dx, dy, dz] of offsets) {
          const spot = { x: base.x + dx, y: base.y + dy, z: base.z + dz }
          const raw = await blockAt(spot)
          const original = !raw || raw === 'air' || raw === 'water' ? raw || 'air' : null
          if (original === null) continue
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} minecraft:chest`)
          const opened = await waitForValue(async () => {
            const probe = await inspect(spot)
            return probe.status === 200 && probe.body.status === 'SUCCEEDED' ? probe.body.result : null
          }, `临时箱子 (${spot.x},${spot.y},${spot.z}) 可以打开`, 6000)
          if (opened) {
            containerPos = spot
            containerOrigin = original
            createdFixture = true
            console.log(
              `[smoke] container：在 (${spot.x},${spot.y},${spot.z}) 放了临时箱子`
                + `（原方块是 ${original}，结束后原样还原）`,
            )
            break
          }
          // 打不开 → 立刻还原，再试下一个位置
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} ${original}`)
        }
        if (!containerPos) {
          console.log(
            '[smoke] SKIPPED container：附近放不出可以被打开的临时箱子，也没有 SMOKE_CONTAINER_TARGET'
              + '（不伪造结论）',
          )
        }
      }

      if (!containerPos) {
        console.log('[smoke] ✗ Phase 4E：没有可用容器，无法完成 withdraw / deposit 真机验证')
      } else {
        // (b) inspect：真实 open → read → close（两层证据：HTTP 快照 + 再看一次证明窗口没漏）
        const first = await inspect(containerPos)
        const snapshotOne = first.body && first.body.result
        check(
          first.status === 200 && first.body.status === 'SUCCEEDED' && Boolean(snapshotOne),
          `container_inspect 成功（HTTP ${first.status}，type=${snapshotOne && snapshotOne.container.type}）`,
        )
        check(
          Boolean(snapshotOne) &&
            name(snapshotOne.container.type) === 'chest' &&
            snapshotOne.container.size === 27,
          `容器类型/大小来自真实 window（type=${snapshotOne && snapshotOne.container.type}，`
            + `size=${snapshotOne && snapshotOne.container.size}）`,
        )
        const second = await inspect(containerPos)
        check(
          second.status === 200 && second.body.status === 'SUCCEEDED',
          '窗口没有泄漏：第二次 inspect 仍然成功（close 是硬要求）',
        )

        // (c) 往容器里放一个测试物品（/replaceitem 是 1.16 的写法；1.17+ 是 /item）
        await say(
          `/replaceitem block ${containerPos.x} ${containerPos.y} ${containerPos.z} `
            + `container.0 ${FIXTURE_ITEM} ${FIXTURE_COUNT}`,
        )
        await sleep(600)
        let filled = await waitForValue(async () => {
          const probe = await inspect(containerPos)
          const snap = probe.body && probe.body.result
          if (!snap) return null
          const row = (snap.slots || []).find((entry) => entry.slot === 0)
          return row && name(row.name) === name(FIXTURE_ITEM) ? snap : null
        }, '容器第 0 格出现测试物品', 8000)
        if (!filled) {
          // 换 1.17+ 的写法再试一次
          await say(
            `/item replace block ${containerPos.x} ${containerPos.y} ${containerPos.z} `
              + `container.0 with ${FIXTURE_ITEM} ${FIXTURE_COUNT}`,
          )
          await sleep(600)
          filled = await waitForValue(async () => {
            const probe = await inspect(containerPos)
            const snap = probe.body && probe.body.result
            if (!snap) return null
            const row = (snap.slots || []).find((entry) => entry.slot === 0)
            return row && name(row.name) === name(FIXTURE_ITEM) ? snap : null
          }, '容器第 0 格出现测试物品（/item 写法）', 8000)
        }
        if (!filled) {
          console.log(
            '[smoke] SKIPPED container 读写：这台服务器不接受 /replaceitem 或 /item 装填箱子'
              + '（无法在真机上造出"箱子里有东西"，不伪造结论）',
          )
          if (createdFixture) {
            await say(
              `/setblock ${containerPos.x} ${containerPos.y} ${containerPos.z} ${containerOrigin}`,
            )
          }
        } else {
          const slotZero = filled.slots.find((entry) => entry.slot === 0)
          check(
            slotZero && slotZero.count === FIXTURE_COUNT && name(slotZero.name) === name(FIXTURE_ITEM),
            `真实箱子内容与命令一致（第 0 格 ${slotZero && slotZero.name}×${slotZero && slotZero.count}）`,
          )
          console.log(
            `[smoke]    箱子内容（真实 read）：${JSON.stringify(filled.slots)}`,
          )

          // (d) 找一个空背包槽（避开选中的快捷栏槽 → 不动主手）
          const inventoryView = await request(runtimePort, 'GET', '/minecraft/inventory')
          const selectedHotbar = 36 + Number((inventoryView.body && inventoryView.body.selected_hotbar_slot) || 0)
          const occupied = new Set((await readSlots()).map((row) => row.slot))
          let targetSlot = null
          for (let slot = 9; slot <= 44; slot += 1) {
            if (!occupied.has(slot) && slot !== selectedHotbar) {
              targetSlot = slot
              break
            }
          }
          if (targetSlot === null) {
            console.log('[smoke] SKIPPED withdraw：背包里没有空槽（不隐式交换）')
          } else {
            console.log(`[smoke] withdraw：容器第 0 格 → 背包第 ${targetSlot} 格，取 1 个`)
            const withdraw = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
              x: containerPos.x,
              y: containerPos.y,
              z: containerPos.z,
              direction: 'withdraw',
              container_slot: 0,
              inventory_slot: targetSlot,
              item: name(FIXTURE_ITEM),
              count: 1,
            })
            const started =
              withdraw.status === 200 &&
              withdraw.body.status === 'RUNNING' &&
              Boolean(withdraw.body.action_id)
            if (!started) {
              console.log(`[smoke]    withdraw 启动失败：${JSON.stringify(withdraw)}`)
              check(false, `withdraw 启动必须 200/RUNNING 且带 action_id（HTTP ${withdraw.status}）`)
            } else {
              check(true, `withdraw 启动 → RUNNING（action_id=${withdraw.body.action_id}）`)
              const terminal = await waitForActionTerminal(
                withdraw.body.action_id,
                'withdraw 终态事件',
                30000,
              )
              if (!terminal || terminal.event !== 'minecraft.action.completed') {
                check(
                  false,
                  `withdraw 终态是 ${terminal && terminal.event}`
                    + `（${(terminal && (terminal.error || terminal.reason)) || '-'}）`,
                )
              } else {
                const result = terminal.result || {}
                check(
                  result.direction === 'withdraw' &&
                    result.container_slot === 0 &&
                    result.inventory_slot === targetSlot,
                  `withdraw 结果带明确方向与槽位（${JSON.stringify({
                    direction: result.direction,
                    container_slot: result.container_slot,
                    inventory_slot: result.inventory_slot,
                  })}）`,
                )
                check(
                  result.container_before && result.container_after &&
                    result.container_before.count === FIXTURE_COUNT &&
                    result.container_after.count === FIXTURE_COUNT - 1,
                  `container_before/after 如实上报（${JSON.stringify(result.container_before)} → `
                    + `${JSON.stringify(result.container_after)}）`,
                )
                check(
                  result.inventory_before === null &&
                    result.inventory_after &&
                    result.inventory_after.count === 1,
                  `inventory_before/after 如实上报（${JSON.stringify(result.inventory_before)} → `
                    + `${JSON.stringify(result.inventory_after)}）`,
                )
                check(
                  result.moved_out >= 1 && result.gained_in >= 1,
                  `真实变化量：移出 ${result.moved_out} / 目标增加 ${result.gained_in}`,
                )
                // 第二层：重新读容器 + 背包（不抄事件里的结论）
                const afterWithdraw = await waitForValue(async () => {
                  const probe = await inspect(containerPos)
                  const snap = probe.body && probe.body.result
                  const rows = await readSlots()
                  if (!snap) return null
                  const row = (snap.slots || []).find((entry) => entry.slot === 0)
                  const target = rows.find((entry) => entry.slot === targetSlot)
                  const containerOk = row && row.count === FIXTURE_COUNT - 1
                  const inventoryOk = target && name(target.name) === name(FIXTURE_ITEM)
                  return containerOk && inventoryOk ? { snap, rows } : null
                }, '重新读取容器与背包（withdraw 之后）', 15000)
                check(
                  Boolean(afterWithdraw),
                  '重新 inspect 与重读槽位表都反映这次搬运（不硬编码 ±1）',
                )
                if (afterWithdraw) {
                  console.log(
                    `[smoke]    重读：容器 ${JSON.stringify(afterWithdraw.snap.slots)}；`
                      + `背包 ${JSON.stringify(afterWithdraw.rows.filter((r) => r.slot === targetSlot))}`,
                  )
                }

                // (e) deposit 放回原槽 → 容器与背包都必须恢复
                console.log(`[smoke] deposit：背包第 ${targetSlot} 格 → 容器第 0 格，放回 1 个`)
                const deposit = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
                  x: containerPos.x,
                  y: containerPos.y,
                  z: containerPos.z,
                  direction: 'deposit',
                  container_slot: 0,
                  inventory_slot: targetSlot,
                  item: name(FIXTURE_ITEM),
                  count: 1,
                })
                const depositStarted =
                  deposit.status === 200 &&
                  deposit.body.status === 'RUNNING' &&
                  Boolean(deposit.body.action_id)
                if (!depositStarted) {
                  console.log(`[smoke]    deposit 启动失败：${JSON.stringify(deposit)}`)
                  check(false, `deposit 启动必须 200/RUNNING（HTTP ${deposit.status}）`)
                } else {
                  check(true, `deposit 启动 → RUNNING（action_id=${deposit.body.action_id}）`)
                  const depositTerminal = await waitForActionTerminal(
                    deposit.body.action_id,
                    'deposit 终态事件',
                    30000,
                  )
                  if (!depositTerminal || depositTerminal.event !== 'minecraft.action.completed') {
                    check(
                      false,
                      `deposit 终态是 ${depositTerminal && depositTerminal.event}`
                        + `（${(depositTerminal && (depositTerminal.error || depositTerminal.reason)) || '-'}）`,
                    )
                  } else {
                    const depositResult = depositTerminal.result || {}
                    check(
                      depositResult.direction === 'deposit' &&
                        depositResult.container_after &&
                        depositResult.container_after.count === FIXTURE_COUNT,
                      `deposit 把东西放回了原槽（container_after=${JSON.stringify(
                        depositResult.container_after,
                      )}）`,
                    )
                    check(
                      depositResult.inventory_after === null,
                      `背包格已空（inventory_after=${JSON.stringify(depositResult.inventory_after)}）`,
                    )
                    // 最终状态：容器内容 + 背包布局都回到测试前
                    const restored = await waitForValue(async () => {
                      const probe = await inspect(containerPos)
                      const snap = probe.body && probe.body.result
                      const rows = await readSlots()
                      if (!snap) return null
                      const row = (snap.slots || []).find((entry) => entry.slot === 0)
                      const containerOk =
                        row && row.count === FIXTURE_COUNT && name(row.name) === name(FIXTURE_ITEM)
                      return containerOk ? { snap, rows } : null
                    }, '容器内容恢复（deposit 之后）', 15000)
                    check(Boolean(restored), '容器状态已恢复（第 0 格回到测试前的数量）')
                    if (restored) {
                      check(
                        signature(restored.rows) === playerSignatureBefore,
                        `背包布局已恢复（before=${playerSignatureBefore || '空'} / `
                          + `after=${signature(restored.rows) || '空'}）`,
                      )
                      console.log(
                        `[smoke]    Phase 4E 结束状态：容器 ${JSON.stringify(restored.snap.slots)}；`
                          + `背包 ${signature(restored.rows) || '空'}`,
                      )
                    }
                  }
                }
              }
            }
          }

          // (f) 清夹具：临时箱子自己 setblock air 还原（操作者指定的容器不动）
          if (createdFixture) {
            await say(
              `/setblock ${containerPos.x} ${containerPos.y} ${containerPos.z} ${containerOrigin}`,
            )
            await sleep(500)
            const gone = await waitForValue(async () => {
              const probe = await inspect(containerPos)
              return probe.status !== 200 ? true : null
            }, '临时箱子已清掉', 8000)
            check(
              Boolean(gone),
              `临时箱子已还原成 ${containerOrigin}`
                + `（(${containerPos.x},${containerPos.y},${containerPos.z}) 不再是容器）`,
            )
          } else {
            console.log('[smoke] 用的是操作者自己的容器：只把物品放回原槽，不动方块')
          }
        }
      }

      // container 动作是毫秒级的，真实服务器上没有"挖到一半"那种可取消窗口
      console.log(
        '[smoke] SKIPPED container STOP：inspect/transfer 毫秒级完成，真实服务器上没有可取消窗口',
      )
    }

    // ---- 8. Phase 4F：crafting（玩家自身 2×2）真实合成 ----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4F：runtime 未空闲，crafting 硬门禁不能执行')
    } else {
      const name = (value) => normalizeItemName(value || '')
      const FIXTURE_PLANKS = 'minecraft:oak_planks'
      const FIXTURE_PLANKS_COUNT = 2
      const readSlots = async () => {
        const resp = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        return resp.status === 200 && resp.body ? resp.body.slots || [] : []
      }
      const signature = (rows) =>
        rows
          .map((row) => `${row.slot}:${name(row.name)}×${row.count}`)
          .sort((a, b) => Number.parseInt(a, 10) - Number.parseInt(b, 10))
          .join('|')
      const inventorySlice = async () => (await request(runtimePort, 'GET', '/minecraft/inventory')).body
      const say = (message) => request(runtimePort, 'POST', '/minecraft/chat', { message })
      const totalOf = (body, itemName) => {
        const hit = ((body && body.items) || []).find((row) => name(row.name) === name(itemName))
        return hit ? hit.count : 0
      }

      const signatureBefore = signature(await readSlots())
      const sliceBefore = await inventorySlice()
      const sticksBefore = totalOf(sliceBefore, 'stick')
      const planksBefore = totalOf(sliceBefore, FIXTURE_PLANKS)
      console.log(
        `[smoke] crafting 前：stick=${sticksBefore}，${name(FIXTURE_PLANKS)}=${planksBefore}`
          + `（背包 ${signatureBefore || '空'}）`,
      )

      // 夹具：用服务器命令给罐头两块木板（§三十六；用不了就 SKIPPED，绝不伪造）
      const botName = (await status()).username
      let fixtureReady = false
      if (botName) {
        await say(`/give ${botName} ${FIXTURE_PLANKS} ${FIXTURE_PLANKS_COUNT}`)
        const granted = await waitForValue(async () => {
          const body = await inventorySlice()
          return totalOf(body, FIXTURE_PLANKS) >= FIXTURE_PLANKS_COUNT ? body : null
        }, '夹具木板进入背包', 10000)
        fixtureReady = Boolean(granted)
        if (!fixtureReady) {
          console.log('[smoke] SKIPPED crafting：/give 不可用（拿不到木板夹具，不伪造结论）')
        }
      } else {
        console.log('[smoke] SKIPPED crafting：拿不到 bot 用户名，无法 /give 夹具')
      }

      if (fixtureReady) {
        // (a) recipe lookup：找到"材料正是 oak_planks ×2"的 2×2 配方（绝不在脚本里写死 recipe_id）
        const lookupOnce = async () =>
          (await request(runtimePort, 'POST', '/minecraft/recipe_lookup', { item: 'stick' })).body
        const lookup = await lookupOnce()
        const payload = lookup && lookup.result
        check(
          Boolean(payload) && payload.status === 'available',
          `recipe lookup = PASS（status=${payload && payload.status}，共 ${payload && payload.total} 个 2×2 配方）`,
        )
        const target = ((payload && payload.recipes) || []).find(
          (row) =>
            row.available &&
            row.requires_table === false &&
            (row.ingredients || []).length === 1 &&
            name(row.ingredients[0].name) === name(FIXTURE_PLANKS),
        )
        check(
          Boolean(target),
          `找到用 ${name(FIXTURE_PLANKS)} 的 2×2 配方`
            + `（${target ? target.recipe_id : '没有'}；产物 ${target && target.result.count_per_craft} 个 ${target && target.result.name}）`,
        )
        if (!target) {
          console.log('[smoke] ✗ Phase 4F：这个版本里没有"木板→木棍"的 2×2 配方，无法继续')
        } else {
          const again = await lookupOnce()
          const sameId = ((again.result || {}).recipes || []).some(
            (row) => row.recipe_id === target.recipe_id,
          )
          check(sameId, `recipe_id stable = PASS（${target.recipe_id}）`)

          // (b) craft：RUNNING → completed
          const craft = await request(runtimePort, 'POST', '/minecraft/craft', {
            recipe_id: target.recipe_id,
          })
          const started =
            craft.status === 200 && craft.body.status === 'RUNNING' && Boolean(craft.body.action_id)
          check(started, `craft → RUNNING + action_id（HTTP ${craft.status}）`)
          if (!started) {
            console.log(`[smoke]    craft 启动失败：${JSON.stringify(craft)}`)
          } else {
            const terminal = await waitForActionTerminal(
              craft.body.action_id,
              'craft 终态事件',
              30000,
            )
            if (!terminal || terminal.event !== 'minecraft.action.completed') {
              check(
                false,
                `craft 终态是 ${terminal && terminal.event}`
                  + `（${(terminal && (terminal.error || terminal.reason)) || '-'}）`,
              )
            } else {
              const result = terminal.result || {}
              check(
                name(result.item) === 'stick' &&
                  Boolean(result.result) &&
                  result.result.crafted_count >= target.result.count_per_craft,
                `real craft completed（产物 count=${result.result && result.result.crafted_count}`
                  + `，每刀 ${result.result && result.result.count_per_craft}）`,
              )
              check(
                result.before &&
                  result.after &&
                  result.after.result_count - result.before.result_count ===
                    (result.result && result.result.crafted_count),
                `产物 before/after 如实上报（${result.before && result.before.result_count} → `
                  + `${result.after && result.after.result_count}）`,
              )
              const consumed = (result.ingredients || [])[0]
              check(
                Boolean(consumed) &&
                  name(consumed.name) === name(FIXTURE_PLANKS) &&
                  consumed.consumed >= FIXTURE_PLANKS_COUNT,
                `材料消耗如实上报（${consumed && consumed.name} -${consumed && consumed.consumed}）`,
              )

              // (c) 重新读真实 inventory：产物增加 + 材料减少（不抄事件里的数字）
              const craftedCount = (result.result && result.result.crafted_count) || 0
              const reread = await waitForValue(async () => {
                const body = await inventorySlice()
                const sticks = totalOf(body, 'stick')
                const planks = totalOf(body, FIXTURE_PLANKS)
                return sticks >= sticksBefore + craftedCount &&
                  planks <= planksBefore + FIXTURE_PLANKS_COUNT - FIXTURE_PLANKS_COUNT
                  ? { body, sticks, planks }
                  : null
              }, '重读 inventory（产物增加 + 材料减少）', 15000)
              check(
                Boolean(reread),
                'inventory reread agrees = PASS（重读到产物增加与材料减少'
                  + `：stick ${sticksBefore} → ${reread && reread.sticks}，`
                  + `${name(FIXTURE_PLANKS)} ${planksBefore} → ${reread && reread.planks}）`,
              )

              // (d) 清夹具：把测试给的东西清掉，逐槽比对回测试前
              await say(`/clear ${botName} minecraft:stick`)
              await say(`/clear ${botName} ${FIXTURE_PLANKS}`)
              const restored = await waitForValue(async () => {
                const rows = await readSlots()
                return signature(rows) === signatureBefore ? rows : null
              }, '夹具已清、背包回到测试前', 15000)
              check(Boolean(restored), `fixture restored = PASS（${signatureBefore || '空'}）`)
              if (!restored) {
                console.log(
                  `[smoke]    ✗ 背包没回到测试前：before=${signatureBefore || '空'}`
                    + ` after=${signature(await readSlots()) || '空'}（请手动清理 stick / oak_planks）`,
                )
              }
            }
          }
        }
      }

      console.log(
        '[smoke] SKIPPED craft STOP：合成毫秒级完成，真实服务器上没有可取消窗口'
          + '（Node 单测覆盖 cancel/timeout/race/cleanup 恰好一次）',
      )
    }

    // ---- 9. Phase 4G：3×3 工作台（8 木板 → 1 箱子）----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4G：runtime 未空闲，3×3 craft 不能执行')
    } else {
      const name = (value) => normalizeItemName(value || '')
      const PLANKS = 'minecraft:oak_planks'
      const PLANKS_COUNT = 8
      const readSlots = async () => {
        const resp = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        return resp.status === 200 && resp.body ? resp.body.slots || [] : []
      }
      const signature = (rows) =>
        rows
          .map((row) => `${row.slot}:${name(row.name)}×${row.count}`)
          .sort((a, b) => Number.parseInt(a, 10) - Number.parseInt(b, 10))
          .join('|')
      const inventorySlice = async () => (await request(runtimePort, 'GET', '/minecraft/inventory')).body
      const say = (message) => request(runtimePort, 'POST', '/minecraft/chat', { message })
      const totalOf = (body, itemName) => {
        const hit = ((body && body.items) || []).find((row) => name(row.name) === name(itemName))
        return hit ? hit.count : 0
      }

      const signatureBefore = signature(await readSlots())
      const sliceBefore = await inventorySlice()
      const chestsBefore = totalOf(sliceBefore, 'chest')
      console.log(
        `[smoke] 3×3 前：chest=${chestsBefore}（背包 ${signatureBefore || '空'}）`,
      )

      // (a) 找一张工作台：优先 SMOKE_CRAFTING_TABLE_TARGET；否则就地放一张临时工作台
      let tablePos = null
      let tableOrigin = 'air'
      let createdTable = false
      const override = (process.env.SMOKE_CRAFTING_TABLE_TARGET || '').trim()
      if (override) {
        const [x, y, z] = override.split(',').map((value) => Number.parseInt(value, 10))
        if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(z)) {
          tablePos = { x, y, z }
          console.log(`[smoke] 3×3：用操作者指定的工作台 (${x},${y},${z})`)
        } else {
          console.log(`[smoke] SMOKE_CRAFTING_TABLE_TARGET 格式不对（应为 "x,y,z"）：${override}`)
        }
      } else {
        const origin = (await status()).position
        const base = { x: Math.round(origin.x), y: Math.round(origin.y), z: Math.round(origin.z) }
        const offsets = [
          [1, 1, 0],
          [0, 1, 1],
          [1, 1, 1],
          [-1, 1, 0],
          [0, 1, -1],
          [0, 2, 0],
          [0, 3, 0],
          [1, 0, 0],
          [0, 0, 1],
          [-1, 0, 0],
          [0, 0, -1],
        ]
        for (const [dx, dy, dz] of offsets) {
          const spot = { x: base.x + dx, y: base.y + dy, z: base.z + dz }
          const raw = await blockAt(spot)
          const original = !raw || raw === 'air' || raw === 'water' ? raw || 'air' : null
          if (original === null) continue
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} minecraft:crafting_table`)
          const usable = await waitForValue(async () => {
            const probe = await request(runtimePort, 'POST', '/minecraft/recipe_lookup', {
              item: 'chest',
              crafting_table: spot,
            })
            return probe.status === 200 ? probe.body.result : null
          }, `临时工作台 (${spot.x},${spot.y},${spot.z}) 可用`, 6000)
          if (usable) {
            tablePos = spot
            tableOrigin = original
            createdTable = true
            console.log(
              `[smoke] 3×3：在 (${spot.x},${spot.y},${spot.z}) 放了临时工作台`
                + `（原方块 ${original}，结束后原样还原）`,
            )
            break
          }
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} ${original}`)
        }
        if (!tablePos) {
          console.log(
            '[smoke] SKIPPED 3×3：附近放不出可用的临时工作台，也没有 SMOKE_CRAFTING_TABLE_TARGET'
              + '（不伪造结论）',
          )
        }
      }

      // (b) 材料夹具：/give 8 块木板
      const botName = (await status()).username
      let materialsReady = false
      if (tablePos && botName) {
        await say(`/give ${botName} ${PLANKS} ${PLANKS_COUNT}`)
        const granted = await waitForValue(async () => {
          const body = await inventorySlice()
          return totalOf(body, PLANKS) >= PLANKS_COUNT ? body : null
        }, '夹具木板进入背包', 10000)
        materialsReady = Boolean(granted)
        if (!materialsReady) {
          console.log('[smoke] SKIPPED 3×3：/give 不可用（凑不出 8 块木板，不伪造结论）')
        }
      } else if (tablePos) {
        console.log('[smoke] SKIPPED 3×3：拿不到 bot 用户名，无法 /give 材料')
      }

      if (tablePos && materialsReady) {
        // (c) table lookup：动态找"需要工作台"的 chest 配方（绝不硬编码 recipe_id）
        const lookup = await request(runtimePort, 'POST', '/minecraft/recipe_lookup', {
          item: 'chest',
          crafting_table: tablePos,
        })
        const payload = lookup.body && lookup.body.result
        check(
          lookup.status === 200 && lookup.body.status === 'SUCCEEDED' && Boolean(payload),
          `table lookup = PASS（HTTP ${lookup.status}）`,
        )
        check(
          JSON.stringify(payload && payload.crafting_table) === JSON.stringify(tablePos),
          `table 坐标进语义结果（${JSON.stringify(payload && payload.crafting_table)}）`,
        )
        const target = ((payload && payload.recipes) || []).find(
          (row) => row.available && row.requires_table === true,
        )
        check(
          Boolean(target),
          `recipe requires table = true（${target ? target.recipe_id : '没找到需要工作台的可用配方'}）`,
        )
        check(
          Boolean(target) && target.available === true,
          `recipe available = true（每刀 ${target && target.result.count_per_craft} 个 ${target && target.result.name}）`,
        )
        console.log(
          '[smoke] table type = minecraft:crafting_table / table distance = PASS'
            + `（lookup 成功即通过距离与类型门；坐标 ${tablePos.x},${tablePos.y},${tablePos.z}）`,
        )

        if (!target) {
          console.log('[smoke] ✗ Phase 4G：这个版本里没有"需要工作台"的箱子配方，无法继续')
        } else {
          // (d) craft：RUNNING → completed
          const craft = await request(runtimePort, 'POST', '/minecraft/craft', {
            recipe_id: target.recipe_id,
            crafting_table: tablePos,
          })
          const started =
            craft.status === 200 && craft.body.status === 'RUNNING' && Boolean(craft.body.action_id)
          check(started, `craft RUNNING + action_id（HTTP ${craft.status}）`)
          if (!started) {
            console.log(`[smoke]    craft 启动失败：${JSON.stringify(craft)}`)
          } else {
            const terminal = await waitForActionTerminal(
              craft.body.action_id,
              'craft 终态事件',
              30000,
            )
            if (!terminal || terminal.event !== 'minecraft.action.completed') {
              check(
                false,
                `craft 终态是 ${terminal && terminal.event}`
                  + `（${(terminal && (terminal.error || terminal.reason)) || '-'}）`,
              )
            } else {
              const result = terminal.result || {}
              check(
                JSON.stringify(result.crafting_table) === JSON.stringify(tablePos),
                `craft completed 带回工作台坐标（${JSON.stringify(result.crafting_table)}）`,
              )
              const crafted = (result.result && result.result.crafted_count) || 0
              check(crafted >= 1, `output increased（产物 count=${crafted}）`)
              const consumed = (result.ingredients || [])[0]
              check(
                Boolean(consumed) && consumed.consumed >= PLANKS_COUNT,
                `ingredients decreased（${consumed && consumed.name} -${consumed && consumed.consumed}）`,
              )
              // (e) 重读 inventory：箱子 +N、木板 −8（不抄事件里的数字）
              const reread = await waitForValue(async () => {
                const body = await inventorySlice()
                const chests = totalOf(body, 'chest')
                const planks = totalOf(body, PLANKS)
                return chests >= chestsBefore + crafted && planks <= 0
                  ? { chests, planks }
                  : null
              }, '重读 inventory（箱子增加 + 木板消耗）', 15000)
              check(
                Boolean(reread),
                'inventory reread agrees = PASS'
                  + `（chest ${chestsBefore} → ${reread && reread.chests}，`
                  + `${name(PLANKS)} → ${reread && reread.planks}）`,
              )
            }
          }
        }
      }

      // (f) 清理夹具：产物 + 材料 + 临时工作台；最后逐槽比对
      if (tablePos) {
        if (botName) {
          await say(`/clear ${botName} minecraft:chest`)
          await say(`/clear ${botName} ${PLANKS}`)
        }
        if (createdTable) {
          await say(`/setblock ${tablePos.x} ${tablePos.y} ${tablePos.z} ${tableOrigin}`)
        }
        const restored = await waitForValue(async () => {
          const rows = await readSlots()
          return signature(rows) === signatureBefore ? rows : null
        }, '夹具已清、背包回到测试前', 15000)
        check(Boolean(restored), `inventory restored / fixture restored = PASS（${signatureBefore || '空'}）`)
        if (createdTable) {
          const gone = await waitForValue(async () => {
            const probe = await request(runtimePort, 'POST', '/minecraft/recipe_lookup', {
              item: 'chest',
              crafting_table: tablePos,
            })
            return probe.status !== 200 ? true : null
          }, '临时工作台已还原', 8000)
          check(Boolean(gone), `table restored = PASS（还原成 ${tableOrigin}）`)
        }
      }

      console.log(
        '[smoke] SKIPPED 3×3 craft STOP：合成毫秒级完成，真实服务器上没有可取消窗口',
      )
    }

    // ---- 10. Phase 4H：掉落物感知 + 单实体拾取（dig → drop → dropped_items → pickup）----
    if (!digReady) {
      console.log('[smoke] ✗ Phase 4H：runtime 未空闲，掉落物/拾取硬门禁不能执行')
    } else {
      const name = (value) => normalizeItemName(value || '')
      const TEST_LOG = 'minecraft:oak_log'
      const readSlots = async () => {
        const resp = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        return resp.status === 200 && resp.body ? resp.body.slots || [] : []
      }
      const signature = (rows) =>
        rows
          .map((row) => `${row.slot}:${name(row.name)}×${row.count}`)
          .sort((a, b) => Number.parseInt(a, 10) - Number.parseInt(b, 10))
          .join('|')
      const inventorySlice = async () => (await request(runtimePort, 'GET', '/minecraft/inventory')).body
      const say = (message) => request(runtimePort, 'POST', '/minecraft/chat', { message })
      const totalOf = (body, itemName) => {
        const hit = ((body && body.items) || []).find((row) => name(row.name) === name(itemName))
        return hit ? hit.count : 0
      }
      const listDropped = async () => {
        const resp = await request(runtimePort, 'POST', '/minecraft/dropped_items', {})
        return resp.status === 200 && resp.body ? resp.body.result : null
      }
      // 把罐头挪到**离掉落物 minDist~maxDist 格的一块干燥落脚点**上（只动自己，不动世界）。
      // 4H 的 STOP 段需要"要走一段"的窗口；直接往 -7 格 tp 有可能掉下悬崖/落水，
      // 那样 pickup 会以 target_too_far / invalid 起不来，就等于自己把这一段做成 SKIPPED。
      const tpToDrySpotNear = async (drop, minDist, maxDist, label) => {
        const local = (await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=local')).body
        const columns = (local.blocks && local.blocks.local && local.blocks.local.columns) || []
        const FLUID = /water|lava|seagrass|kelp|bubble|magma|ice/i
        const byColumn = new Map()
        for (const col of columns) {
          if (!col.pos || !col.name) continue
          const key = `${col.pos.x},${col.pos.z}`
          const current = byColumn.get(key)
          if (!current || col.pos.y > current.pos.y) byColumn.set(key, col)
        }
        const middle = (minDist + maxDist) / 2
        const found = [...byColumn.values()]
          .filter((col) => !FLUID.test(col.name))
          .map((col) => ({
            col,
            gap: Math.hypot(
              col.pos.x + 0.5 - drop.position.x,
              col.pos.z + 0.5 - drop.position.z,
            ),
          }))
          .filter((row) => row.gap >= minDist && row.gap <= maxDist)
          .sort((a, b) => Math.abs(a.gap - middle) - Math.abs(b.gap - middle))[0]
        if (!found) {
          console.log(`[smoke] ${label}：附近没有 ${minDist}~${maxDist} 格的干燥落脚点（就地继续）`)
          return null
        }
        const x = found.col.pos.x + 0.5
        const y = found.col.pos.y + 1
        const z = found.col.pos.z + 0.5
        await say(`/tp @s ${x} ${y} ${z}`)
        await sleep(1200)
        console.log(
          `[smoke] ${label}：把罐头挪到 (${Math.round(x)},${Math.round(y)},${Math.round(z)})`
            + `（离掉落物约 ${found.gap.toFixed(1)} 格，${found.col.name}）`,
        )
        return { x, y, z }
      }

      const waitForDrop = async (predicate, label, timeoutMs = 12000) => {
        const deadline = Date.now() + timeoutMs
        while (Date.now() < deadline) {
          const view = await listDropped()
          if (view) {
            const hit = predicate(view)
            if (hit) return hit
          }
          await sleep(300)
        }
        console.log(`[smoke] 等待超时：${label}`)
        return null
      }

      // 夹具前置（本段自洽）：上一次中断的运行可能留下测试物品或临时方块 —— 先清干净，
      // 后面的逐槽比对才有意义。
      const fixtureBotName = (await status()).username
      if (fixtureBotName) {
        await say(`/clear ${fixtureBotName} ${TEST_LOG}`)
        await sleep(400)
      }
      const leftoverBlock = (process.env.SMOKE_CLEANUP_BLOCK || '').trim()
      if (/^-?\d+,-?\d+,-?\d+$/.test(leftoverBlock)) {
        const [lx, ly, lz] = leftoverBlock.split(',')
        await say(`/setblock ${lx} ${ly} ${lz} air`)
        await sleep(400)
        console.log(`[smoke] 已清理上一次残留的临时方块 (${lx},${ly},${lz})`)
      }

      const signatureBefore = signature(await readSlots())
      const sliceBefore = await inventorySlice()
      const logsBefore = totalOf(sliceBefore, TEST_LOG)
      console.log(`[smoke] 4H 前：${name(TEST_LOG)}=${logsBefore}（背包 ${signatureBefore || '空'}）`)

      // (a) 指定目标（SMOKE_PICKUP_TARGET）优先；否则自己造一个真实掉落物
      const overrideTarget = (process.env.SMOKE_PICKUP_TARGET || '').trim()
      const overrideItem = (process.env.SMOKE_PICKUP_ITEM || '').trim() || TEST_LOG
      let targetDrop = null
      let createdBlock = null
      let blockOrigin = 'air'

      if (overrideTarget) {
        const wanted = Number.parseInt(overrideTarget, 10)
        targetDrop = await waitForDrop(
          (view) => view.items.find((row) => row.entity_id === wanted) || null,
          `找到操作者指定的实体 #${wanted}`,
          8000,
        )
        console.log(
          targetDrop
            ? `[smoke] 4H：用操作者指定的实体 #${wanted}（${targetDrop.item.name} ×${targetDrop.item.count}）`
            : `[smoke] ✗ 4H：找不到操作者指定的实体 #${wanted}`,
        )
      } else {
        // 就地放一块 oak_log（只覆盖 air/water，记录原方块）→ dig 出真实掉落物
        const origin = (await status()).position
        const base = { x: Math.round(origin.x), y: Math.round(origin.y), z: Math.round(origin.z) }
        const offsets = [
          [1, 1, 0],
          [0, 1, 1],
          [1, 1, 1],
          [-1, 1, 0],
          [0, 1, -1],
          [0, 2, 0],
          [1, 0, 0],
          [0, 0, 1],
        ]
        for (const [dx, dy, dz] of offsets) {
          const spot = { x: base.x + dx, y: base.y + dy, z: base.z + dz }
          const raw = await blockAt(spot)
          const original = !raw || raw === 'air' || raw === 'water' ? raw || 'air' : null
          if (original === null) continue
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} minecraft:oak_log`)
          await sleep(500)
          if ((await blockAt(spot)) === 'oak_log') {
            createdBlock = spot
            blockOrigin = original
            break
          }
          await say(`/setblock ${spot.x} ${spot.y} ${spot.z} ${original}`)
        }
        if (!createdBlock) {
          console.log('[smoke] SKIPPED 4H：附近放不出测试用的 oak_log（不伪造结论）')
        } else {
          console.log(
            `[smoke] 4H：在 (${createdBlock.x},${createdBlock.y},${createdBlock.z}) 放了 oak_log`
              + `（原方块 ${blockOrigin}，结束后还原）→ 挖掉它制造真实掉落物`,
          )
          const dig = await request(runtimePort, 'POST', '/minecraft/dig', {
            x: createdBlock.x,
            y: createdBlock.y,
            z: createdBlock.z,
            expected_block: 'oak_log',
          })
          if (!(dig.status === 200 && dig.body.action_id)) {
            console.log(`[smoke] ✗ 4H：dig 启动失败：${JSON.stringify(dig)}`)
          } else {
            const digTerminal = await waitForActionTerminal(dig.body.action_id, 'dig 终态', 30000)
            check(
              Boolean(digTerminal) && digTerminal.event === 'minecraft.action.completed',
              `制造掉落物的 dig 完成（${digTerminal && digTerminal.event}）`,
            )
          }
          targetDrop = await waitForDrop(
            (view) =>
              view.items.find(
                (row) =>
                  name(row.item.name) === name(TEST_LOG) &&
                  Math.hypot(
                    row.position.x - createdBlock.x,
                    row.position.y - createdBlock.y,
                    row.position.z - createdBlock.z,
                  ) <= 3,
              ) || null,
            '真实掉落物出现在感知里',
            12000,
          )
        }
      }

      if (!targetDrop) {
        // 挖不出来掉落物（服务器可能禁了 drop）→ 再用 /summon 造一个（两种 NBT 写法都试）
        console.log('[smoke] 4H：没等到挖掘掉落物，尝试 /summon 一个测试 Item Entity……')
        const origin = (await status()).position
        const spot = { x: Math.round(origin.x) + 2, y: Math.round(origin.y) + 1, z: Math.round(origin.z) }
        for (const nbt of [
          `{Item:{id:"${TEST_LOG}",count:3}}`, // 1.20.5+
          `{Item:{id:"${TEST_LOG}",Count:3b}}`, // 1.20.4 及更早
        ]) {
          await say(`/summon minecraft:item ${spot.x} ${spot.y} ${spot.z} ${nbt}`)
          targetDrop = await waitForDrop(
            (view) =>
              view.items.find(
                (row) =>
                  name(row.item.name) === name(TEST_LOG) &&
                  Math.hypot(row.position.x - spot.x, row.position.z - spot.z) <= 4,
              ) || null,
            'summon 出来的掉落物出现在感知里',
            6000,
          )
          if (targetDrop) {
            console.log(`[smoke] 4H：/summon（${nbt.slice(0, 24)}…）成功造出真实 Item Entity`)
            break
          }
        }
      }

      if (!targetDrop) {
        console.log(
          '[smoke] SKIPPED 4H pickup：真实服务器上造不出 Item Entity'
            + '（既没有挖掘掉落，/summon 也不可用）——不伪造 PASS',
        )
        check(false, 'dropped item perception / pickup = BLOCKED（没有真实 Item Entity 可捡）')
      } else {
        // (b) 掉落物感知 = PASS（entity_id / 物品 / 位置 / 距离）
        check(
          Number.isFinite(targetDrop.entity_id) &&
            Boolean(targetDrop.item) &&
            name(targetDrop.item.name) === name(overrideItem) &&
            Number.isFinite(targetDrop.distance) &&
            Boolean(targetDrop.position),
          `dropped item perception = PASS（#${targetDrop.entity_id} `
            + `${targetDrop.item.name} ×${targetDrop.item.count}，${targetDrop.distance} 格）`,
        )
        check(
          Object.keys(targetDrop).sort().join(',') === 'distance,entity_id,item,position',
          `target identity = PASS（只有约定字段：${Object.keys(targetDrop).sort().join(',')}）`,
        )
        const entityId = targetDrop.entity_id

        // (c) 真实 STOP：把罐头挪远制造"要走一段"的窗口（绝不瞬移 Item 本身 —— §三十八）。
        //     **先 STOP 再拾取**：STOP 段必须把罐头 tp 走，而 tp 回来的落点如果正好贴着掉落物，
        //     服务器会按 vanilla 规则立刻把它自动收进背包 —— 那样真实拾取就没有目标可测了。
        let stopDone = false
        //: STOP 段把罐头挪走之前先记住原位——采完证据要 tp 回来再造第二个掉落物做真实拾取。
        let stopOrigin = null
        if (targetDrop.distance < 6) {
          stopOrigin = (await status()).position
          await tpToDrySpotNear(targetDrop, 6, 12, '4H STOP 段')
        }
        let beforeStop = await waitForDrop(
          (view) => view.items.find((row) => row.entity_id === entityId) || null,
          '停止测试前目标仍在',
          6000,
        )
        if (beforeStop && beforeStop.distance > 12) {
          // 第一次挪得太远（或掉到下层）→ 换个近一点的干燥落脚点，保证 pickup 能启动
          await tpToDrySpotNear(targetDrop, 4, 8, '4H STOP 段（拉近一点）')
          beforeStop = await waitForDrop(
            (view) => view.items.find((row) => row.entity_id === entityId) || null,
            '拉近之后目标仍在',
            6000,
          )
        }
        if (!beforeStop) {
          console.log('[smoke] SKIPPED 4H STOP：目标实体已经不在了（不能伪造取消）')
        } else {
          const stopStart = await request(runtimePort, 'POST', '/minecraft/pickup_item', {
            entity_id: entityId,
            expected_item: name(beforeStop.item.name),
          })
          if (!(stopStart.status === 200 && stopStart.body.status === 'RUNNING')) {
            check(false, `STOP 段 pickup 启动失败（HTTP ${stopStart.status}）`)
          } else {
            await sleep(700) // 让它真的走起来
            await request(runtimePort, 'POST', '/minecraft/stop', {})
            const stopTerminal = await waitForActionTerminal(
              stopStart.body.action_id,
              'STOP 段 pickup 终态',
              15000,
            )
            if (!stopTerminal) {
              check(false, 'STOP 段 pickup 未进入终态')
            } else if (stopTerminal.event === 'minecraft.action.completed') {
              console.log(
                '[smoke] SKIPPED 4H STOP：动作在被叫停前就已经完成（不伪造 CANCELLED）',
              )
            } else if (stopTerminal.event !== 'minecraft.action.cancelled') {
              check(false, `STOP 段终态应为 cancelled（得到 ${stopTerminal.event}）`)
            } else {
              const settled = await waitForValue(async () => {
                const snap = await status()
                const idle =
                  snap.pathfinder && snap.pathfinder.goal === null && snap.pathfinder.moving === false
                return idle ? snap : null
              }, 'STOP 后回到 IDLE', 8000)
              check(Boolean(settled), 'STOP → CANCELLED + goal null + isMoving false')
              const posA = (await status()).position
              await sleep(500)
              const posB = (await status()).position
              check(
                Math.hypot(posB.x - posA.x, posB.z - posA.z) <= 0.3,
                'STOP 后位置稳定（不再继续走）',
              )
              const stillThere = await listDropped()
              check(
                Boolean(
                  (stillThere.items || []).find((row) => row.entity_id === entityId),
                ),
                'STOP 后目标掉落物仍在（没有被误捡）',
              )
              const invAfterStop = await inventorySlice()
              check(
                totalOf(invAfterStop, beforeStop.item.name) === logsBefore,
                `STOP 后背包没有增加（${beforeStop.item.name} 仍是 ${logsBefore}）`,
              )
              stopDone = true
            }
          }
        }

        // (d) 真实拾取（本阶段硬门禁）：回到原来的站姿，**新造一个**掉落物就在脚边，
        //     从自然站姿走过去 —— 服务器真的把 Item Entity 收进 bot inventory 才算过。
        if (stopOrigin) {
          await say(`/tp @s ${stopOrigin.x} ${stopOrigin.y} ${stopOrigin.z}`)
          await sleep(1200)
        }
        if (!stopDone) {
          console.log('[smoke] SKIPPED 4H STOP 段结论（见上），后续 pickup 仍按真实结果判定')
        }
        const nearFixtureDrop = (view) =>
          view.items.find(
            (row) =>
              name(row.item.name) === name(TEST_LOG) &&
              Math.hypot(
                row.position.x - createdBlock.x,
                row.position.y - createdBlock.y,
                row.position.z - createdBlock.z,
              ) <= 3,
          ) || null
        let pickupDrop = null
        if (createdBlock) {
          await say(`/setblock ${createdBlock.x} ${createdBlock.y} ${createdBlock.z} ${TEST_LOG}`)
          await sleep(500)
          const dig2 = await request(runtimePort, 'POST', '/minecraft/dig', {
            x: createdBlock.x,
            y: createdBlock.y,
            z: createdBlock.z,
            expected_block: 'oak_log',
          })
          let dig2Done = false
          if (dig2.status === 200 && dig2.body.action_id) {
            const dig2Terminal = await waitForActionTerminal(
              dig2.body.action_id,
              '拾取用 dig 终态',
              30000,
            )
            dig2Done =
              Boolean(dig2Terminal) && dig2Terminal.event === 'minecraft.action.completed'
            check(dig2Done, `拾取用的第二次 dig 完成（${dig2Terminal && dig2Terminal.event}）`)
          } else {
            check(false, `拾取用的第二次 dig 启动失败（HTTP ${dig2.status}）`)
          }
          if (dig2Done) {
            pickupDrop = await waitForDrop(nearFixtureDrop, '拾取用掉落物出现在感知里', 12000)
          }
        } else {
          pickupDrop = await waitForDrop(
            (view) => view.items.find((row) => row.entity_id === entityId) || null,
            '拾取目标仍在',
            8000,
          )
        }

        if (!pickupDrop) {
          console.log('[smoke] SKIPPED 4H pickup：真实服务器上造不出 Item Entity —— 不伪造 PASS')
          check(false, 'real pickup = BLOCKED（没有可拾取的真实 Item Entity）')
        } else {
          // 走过去的时候，地上的**别的**掉落物会被服务器自动捡起（vanilla 行走拾取，
          // 不是 pickup_item 干的）—— 先如实记下来，收尾比对才有解释。
          const nearbyOthers = (((await listDropped()) || {}).items || []).filter(
            (row) => row.entity_id !== pickupDrop.entity_id && row.distance <= 8,
          )
          if (nearbyOthers.length > 0) {
            console.log(
              `[smoke] 4H 备注：拾取前 8 格内还有别的掉落物 ${nearbyOthers
                .map((row) => `#${row.entity_id} ${row.item.name}×${row.item.count}(${row.distance} 格)`)
                .join('、')}`
                + ' —— 走过去时可能被服务器自动捡起（vanilla 行为）',
            )
          }
          const pickupItemName = name(pickupDrop.item.name)
          const pickupStart = await request(runtimePort, 'POST', '/minecraft/pickup_item', {
            entity_id: pickupDrop.entity_id,
            expected_item: pickupItemName,
          })
          if (!(pickupStart.status === 200 && pickupStart.body.status === 'RUNNING')) {
            check(false, `pickup 启动必须 200/RUNNING（HTTP ${pickupStart.status}）`)
          } else {
            check(true, `pickup RUNNING = PASS（action_id=${pickupStart.body.action_id}）`)
            const terminal = await waitForActionTerminal(
              pickupStart.body.action_id,
              'pickup 终态事件',
              40000,
            )
            if (!terminal || terminal.event !== 'minecraft.action.completed') {
              check(
                false,
                `real pickup 终态是 ${terminal && terminal.event}`
                  + `（${(terminal && (terminal.error || terminal.reason)) || '-'}）`,
              )
            } else {
              const result = terminal.result || {}
              check(
                result.collected === true && result.entity_id === pickupDrop.entity_id,
                `real playerCollect = PASS（result=${JSON.stringify(result).slice(0, 180)}）`,
              )
              const goneView = await waitForValue(async () => {
                const view = await listDropped()
                return (view.items || []).some((row) => row.entity_id === pickupDrop.entity_id)
                  ? null
                  : view
              }, '目标实体从感知里消失', 8000)
              check(Boolean(goneView), 'target entity gone = PASS（dropped_items 里不再有它）')
              const invAfter = await inventorySlice()
              const logsAfter = totalOf(invAfter, pickupItemName)
              check(
                logsAfter > logsBefore,
                `inventory increased = PASS（${pickupItemName} ${logsBefore} → ${logsAfter}）`,
              )
            }
          }
        }
      }
      // (e) 恢复：清掉测试物品 + 还原临时方块 + 逐槽比对
      const botName = (await status()).username
      if (botName) {
        await say(`/clear ${botName} ${TEST_LOG}`)
      }
      if (createdBlock) {
        await say(`/setblock ${createdBlock.x} ${createdBlock.y} ${createdBlock.z} ${blockOrigin}`)
        await sleep(400)
        // 还没被捡走的夹具掉落物也清掉：以临时方块为中心 4 格（只动这一段测试自己造的东西）
        await say(
          `/kill @e[type=item,x=${createdBlock.x},y=${createdBlock.y},z=${createdBlock.z},distance=..4]`,
        )
        await sleep(300)
      }
      const clearedFixture = await waitForValue(async () => {
        const body = await inventorySlice()
        return totalOf(body, TEST_LOG) === logsBefore ? body : null
      }, `夹具已清（${TEST_LOG} 回到测试前数量）`, 15000)
      check(
        Boolean(clearedFixture),
        `fixture cleared = PASS（${TEST_LOG} 回到测试前数量 ${logsBefore}）`,
      )
      const parseSignature = (sig) =>
        new Map(
          (sig ? sig.split('|') : [])
            .filter(Boolean)
            .map((entry) => {
              const [slot, rest] = entry.split(':')
              const [itemName, count] = rest.split('×')
              return [`${slot}:${itemName}`, Number.parseInt(count, 10)]
            }),
        )
      const beforeSlots = parseSignature(signatureBefore)
      const afterSlots = parseSignature(signature(await readSlots()))
      const lostSlots = []
      for (const [key, count] of beforeSlots) {
        const now = afterSlots.get(key) || 0
        if (now < count) lostSlots.push(`${key} ×${count} → ${now}`)
      }
      check(
        lostSlots.length === 0,
        `inventory restored = PASS（原有物品一个没丢；before=${signatureBefore || '空'}）`,
      )
      if (lostSlots.length > 0) {
        console.log(`[smoke]    ✗ 有原有物品丢了或变少了：${lostSlots.join('、')}`)
      }
      // 多出来的东西：地上散落物被 vanilla 行走拾取是真实服务器上的正常行为，
      // 不能算这一段的失败，但必须**如实打印**出来，绝不静默放过。
      const gainedSlots = []
      for (const [key, count] of afterSlots) {
        const was = beforeSlots.get(key) || 0
        if (count > was) gainedSlots.push(`${key} ×${was}→${count}`)
      }
      if (gainedSlots.length > 0) {
        console.log(
          `[smoke] 4H 备注：背包多出 ${gainedSlots.join('、')}`
            + '（走过去的路上被服务器自动捡起的地上散落物 —— vanilla 行为，非 pickup_item 所为）',
        )
      } else if (lostSlots.length === 0) {
        console.log('[smoke] ✓ 背包逐槽签名与测试前完全一致')
      }
      await sleep(300)
    }

    await ensureIdle('smoke 收尾')

    await request(runtimePort, 'POST', '/minecraft/disconnect', {})
    await waitFor(async () => (await status()).status === 'DISCONNECTED', '主动离开', 15000)
    check(true, '主动离开完成')
    console.log(failed ? '[smoke] REAL SERVER: FAIL' : '[smoke] REAL SERVER: PASS')
    process.exitCode = failed ? 1 : 0
  } catch (error) {
    console.log(`[smoke] REAL SERVER: FAIL（${error.message}）`)
    console.log(`[smoke] 出错位置：${String(error.stack || '').split(String.fromCharCode(10)).slice(1, 3).join(' | ')}`)
    console.log(runtimeLog.join('').split('\n').slice(-10).join('\n'))
    process.exitCode = 1
  } finally {
    child.kill('SIGKILL')
    receiver.close()
  }
}

main()
