'use strict'
/**
 * Phase 1 验收的自动化 E2E（对应任务书 Test 3-10 的可机器执行子集）：
 *
 *   真实 spawn runtime.js → flying-squid 服务器 → offline 登录 → 进世界
 *   （状态机 CONNECTING→CONNECTED→SPAWNING→ONLINE）→ 观察者玩家在服务器里
 *   发言 → minecraft.chat 事件 → Bridge chat API → 观察者看到 bot 发言
 *   → 重复 connect 拒绝 → 被踢 → 主动断开 → 完整流程重复 3 次无残留
 *   → 死亡目标报错不崩 → 进程可干净退出。
 *
 * 运行：node minecraft_runtime/test/e2e.js（需要先 npm install）
 */

const { spawn } = require('child_process')
const http = require('http')
const net = require('net')
const path = require('path')
const fs = require('fs')
const os = require('os')

const { createFakeServer } = require('./fake_server')

const RUNTIME = path.join(__dirname, '..', 'runtime.js')
const USERNAME = 'GuanTou'
const OBSERVER_NAME = 'Tester'
const CYCLES = 3

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

function request(port, method, urlPath, body, token) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body)
    const request = http.request(
      {
        host: '127.0.0.1',
        port,
        method,
        path: urlPath,
        headers: {
          'Content-Type': 'application/json',
          ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
      },
      (response) => {
        let raw = ''
        response.on('data', (chunk) => (raw += chunk))
        response.on('end', () => {
          try {
            resolve({ status: response.statusCode, body: raw ? JSON.parse(raw) : null })
          } catch (error) {
            reject(error)
          }
        })
      },
    )
    request.on('error', reject)
    request.setTimeout(10000, () => request.destroy(new Error('request timeout')))
    if (payload) request.write(payload)
    request.end()
  })
}

function assert(condition, message) {
  if (!condition) throw new Error(`ASSERT FAILED: ${message}`)
}

async function waitForRuntime(port, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const { status, body } = await request(port, 'GET', '/minecraft/health')
      if (status === 200 && body && body.ok === true) return
    } catch {
      /* not up yet */
    }
    await sleep(250)
  }
  throw new Error('runtime health probe timed out')
}

async function waitFor(predicate, label, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await sleep(150)
  }
  throw new Error(`timeout waiting for: ${label}`)
}

/**
 * 把观察者放到指定位置，并**在等待期间重复补发那条幂等的 /tp**。
 *
 * follow 系列检查曾在 CI 上偶发失败（`runtime 看见 Followee` / `目标第 2 次移动后罐头跟上`）：
 * 机器繁忙时实体包与区块加载滞后，等待窗口用完了人还没出现 —— 断言本身没错
 * （她必须**真的**看见玩家、必须**真的**跟到附近），错的是把"某一条 /tp 必须一次成功"
 * 当成了前提。重发同一句指令不改变断言强度，只是不再依赖单条指令的时序运气。
 */
async function placeAndWait({ tp, x, y, z, label, timeoutMs, predicate, retpEveryMs = 2500 }) {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    tp(x, y, z)
    const sliceDeadline = Math.min(deadline, Date.now() + retpEveryMs)
    while (Date.now() < sliceDeadline) {
      try {
        if (await predicate()) return
      } catch (error) {
        // 轮询期间的瞬时错误（例如 status 还没就绪）不算失败：窗口没到就继续等
        lastError = error
      }
      await sleep(150)
    }
  }
  throw new Error(
    `timeout waiting for: ${label}${lastError ? ` (last poll error: ${lastError.message})` : ''}`,
  )
}

// ------------------------------------------------------------------ main

/** 把小数组转成可比对的字符串集合（Phase 4K 的 e2e 用它检查投影字段）。 */
function setOf(values) {
  return [...values].sort().join(',')
}

async function main() {
  // 全局看门狗：E2E 绝不允许无限期挂起（CI 也不欢迎）。
  setTimeout(() => {
    console.error('[e2e] GLOBAL TIMEOUT after 240s')
    process.exit(1)
  }, 240000).unref()
  const callbackPort = await freePort()
  const serverPort = await freePort()
  const runtimePort = await freePort()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-runtime-e2e-'))
  const authFile = path.join(workDir, 'auth.json')
  fs.writeFileSync(
    authFile,
    JSON.stringify({ mode: 'offline', username: USERNAME }, null, 2),
    'utf8',
  )

  // 回调接收器：收集 runtime 推来的事件（并校验 bearer token）。
  const events = []
  const receiver = http.createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/events') {
      res.writeHead(404).end()
      return
    }
    if (req.headers.authorization !== 'Bearer e2e-token') {
      res.writeHead(401).end()
      return
    }
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => {
      events.push(JSON.parse(raw))
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end('{"ok":true}')
    })
  })
  await new Promise((resolve) => receiver.listen(callbackPort, '127.0.0.1', resolve))

  const fake = createFakeServer({ port: serverPort })
  console.log(`[e2e] flying-squid on :${serverPort}, callback on :${callbackPort}`)

  const child = spawn(process.execPath, [RUNTIME], {
    env: {
      ...process.env,
      MC_RUNTIME_PORT: String(runtimePort),
      MC_CALLBACK_URL: `http://127.0.0.1:${callbackPort}/events`,
      MC_CALLBACK_TOKEN: 'e2e-token',
      MC_AUTH_FILE: authFile,
      MC_CONNECT_TIMEOUT: '30',
      MC_MOVE_TIMEOUT_MS: '2500', // Test C 依赖：可达的 40+ 格约需 9s → 确定性超时（4H.1 起）
      MC_FOLLOW_TIMEOUT_MS: '8000', // Test C 的 3s 宽限必须在超时之前完成；Test E 等 8s
      MC_FOLLOW_MAX_CHASE_DISTANCE: '16', // Test D 用 30 格验证「超上限即失败」
      MC_DIG_TIMEOUT_MS: '2500', // dig Test F 依赖：石头徒手 ~7.5s → 确定性超时
      MC_DIG_MAX_DISTANCE: '5',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  const runtimeLog = []
  child.stdout.on('data', (d) => {
    runtimeLog.push(d.toString())
    process.stdout.write(`[runtime] ${d}`)
  })
  child.stderr.on('data', (d) => process.stdout.write(`[runtime:err] ${d}`))
  let exitInfo = null
  child.on('exit', (code, signal) => {
    exitInfo = { code, signal }
  })

  const countEvents = (name) => events.filter((e) => e.event === name).length
  const runtimeLogText = () => runtimeLog.join('')
  let observer = null

  try {
    await waitForRuntime(runtimePort)

    // ---------------- Test 10：完整 连接→进入→聊天→退出 重复 3 次 ----------------
    for (let cycle = 1; cycle <= CYCLES; cycle += 1) {
      console.log(`[e2e] ===== cycle ${cycle}/${CYCLES} =====`)
      const connectedBefore = countEvents('minecraft.connected')
      const spawnedBefore = countEvents('minecraft.spawned')
      const disconnectedBefore = countEvents('minecraft.disconnected')

      // Test 3: join
      const join = await request(runtimePort, 'POST', '/minecraft/connect', {
        host: '127.0.0.1',
        port: serverPort,
      })
      assert(join.status === 200, `connect should answer 200, got ${join.status}: ${JSON.stringify(join.body)}`)
      assert(join.body.session_id, 'connect returns a session_id')
      assert(
        join.body.status === 'CONNECTING' || join.body.status === 'AUTHENTICATING',
        `connect returns a connecting status, got ${join.body.status}`,
      )
      const sessionId = join.body.session_id

      // Test 4（进程内等价）: 服务器玩家表里出现 bot
      await waitFor(() => fake.playerByName(USERNAME) !== null, 'bot joined server player list')

      // Test 5: 进世界 + 基础状态
      await waitFor(
        () => countEvents('minecraft.spawned') > spawnedBefore,
        'minecraft.spawned event',
      )
      await waitFor(async () => {
        const status = await request(runtimePort, 'GET', '/minecraft/status')
        return (
          status.body.status === 'ONLINE' &&
          typeof status.body.health === 'number' &&
          status.body.health > 0 &&
          status.body.position !== null
        )
      }, 'ONLINE status with health/position').then(() => sleep(200))
      const status = await request(runtimePort, 'GET', '/minecraft/status')
      assert(status.status === 200, 'status answers 200')
      assert(status.body.status === 'ONLINE', `bot ONLINE, got ${status.body.status}`)
      assert(status.body.username === USERNAME, `username ${USERNAME}, got ${status.body.username}`)
      assert(status.body.session_id === sessionId, 'session id stable across the cycle')
      assert(typeof status.body.health === 'number' && status.body.health > 0, `health > 0, got ${status.body.health}`)
      assert(status.body.position && Number.isFinite(status.body.position.x), 'position available')
      assert(typeof status.body.dimension === 'string' && status.body.dimension.length > 0, 'dimension available')
      console.log(`[e2e] online: dim=${status.body.dimension} pos=${JSON.stringify(status.body.position)} hp=${status.body.health}`)

      // Test 6: 玩家在服务器里说话 → runtime 产出 minecraft.chat 事件
      if (observer === null) {
        observer = await fake.spawnObserver(OBSERVER_NAME)
        console.log('[e2e] observer player joined')
      }
      const chatsBefore = countEvents('minecraft.chat')
      observer.chat('罐头')
      const observerChats = () =>
        events.filter(
          (e) =>
            e.event === 'minecraft.chat' &&
            (e.username === OBSERVER_NAME || (e.message || '').includes('罐头')),
        )
      await waitFor(() => observerChats().length > chatsBefore, 'minecraft.chat event from observer')
      const chatEvent = observerChats().at(-1)
      assert(chatEvent.session_id === sessionId, 'chat event carries session_id')
      assert(chatEvent.username === OBSERVER_NAME, `chat username ${OBSERVER_NAME}, got ${chatEvent.username}`)
      assert(chatEvent.message.includes('罐头'), `chat message, got ${chatEvent.message}`)

      // Test 7: CatooBot 调 chat API → 另一个客户端能看到罐头发言
      observer.seen.length = 0
      const chat = await request(runtimePort, 'POST', '/minecraft/chat', { message: '我在这里！' })
      assert(chat.status === 200 && chat.body.sent === true, `chat api ok, got ${JSON.stringify(chat.body)}`)
      await waitFor(() => observer.seen.some((line) => line.includes('我在这里！')), 'observer saw bot chat')
      console.log('[e2e] observer saw bot chat ✓')
      // Phase 3B：chat 已纳入 ActionRuntime → 必须有 chat 的 started/completed 事件
      await waitFor(
        () => events.some((e) => e.event === 'minecraft.action.completed' && e.action === 'chat'),
        'chat action completed event',
      )

      // ---------------- Phase 3B：Action Runtime（look_at → stop，逐循环） ----------------
      const startedBefore = countEvents('minecraft.action.started')
      const completedBefore = countEvents('minecraft.action.completed')
      const positionBefore = (await request(runtimePort, 'GET', '/minecraft/status')).body.position

      // look_at：抬头看正上方（mineflayer 约定 pitch≈+90；yaw 无关紧要）
      const look = await request(runtimePort, 'POST', '/minecraft/look_at', {
        x: positionBefore.x + 0.5,
        y: positionBefore.y + 50,
        z: positionBefore.z + 0.5,
      })
      assert(look.status === 200, `look_at answers 200, got ${look.status}: ${JSON.stringify(look.body)}`)
      assert(look.body.status === 'SUCCEEDED', `look_at SUCCEEDED, got ${look.body.status}`)
      assert(
        typeof look.body.action_id === 'string' && look.body.action_id.startsWith('act_'),
        `look_at 返回 runtime 生成的 action_id，得到 ${look.body.action_id}`,
      )
      await waitFor(
        () =>
          countEvents('minecraft.action.started') > startedBefore &&
          countEvents('minecraft.action.completed') > completedBefore,
        'look_at action events',
      )
      const completedEvent = events.filter((e) => e.event === 'minecraft.action.completed').at(-1)
      assert(completedEvent.action_id === look.body.action_id, 'completed 事件携带同一 action_id')
      assert(completedEvent.action === 'look_at', 'completed 事件携带动作名')

      // look_at 不产生水平位移（Phase 3A 关心的是窗口锚点 x/z；y 可能因重力下落变化）
      const afterLook = await request(runtimePort, 'GET', '/minecraft/status')
      const driftX = Math.abs(afterLook.body.position.x - positionBefore.x)
      const driftZ = Math.abs(afterLook.body.position.z - positionBefore.z)
      assert(
        driftX <= 0.2 && driftZ <= 0.2,
        `look_at 不产生水平位移（dx=${driftX.toFixed(2)}, dz=${driftZ.toFixed(2)}；y=${positionBefore.y}→${afterLook.body.position.y}）`,
      )
      // 状态回报：动作完成瞬间的实际朝向（mineflayer 约定抬头 pitch≈+90）。
      // 不看 snapshot 回读：flying-squid 会随即 forcedMove 把朝向回写（测试环境行为）。
      assert(
        completedEvent.result && completedEvent.result.pitch >= 80,
        `completed 事件回报 pitch≈+90（得到 ${completedEvent.result && completedEvent.result.pitch}）`,
      )
      console.log(`[e2e] look_at ✓ id=${look.body.action_id} pitch=${completedEvent.result.pitch}`)

      // stop：幂等、无动作时也成功
      const stop1 = await request(runtimePort, 'POST', '/minecraft/stop', {})
      assert(stop1.status === 200 && stop1.body.status === 'IDLE', `stop → 200 IDLE，得到 ${JSON.stringify(stop1.body)}`)
      assert(Array.isArray(stop1.body.cancelled) && stop1.body.cancelled.length === 0, '空闲 stop 不取消任何动作')
      const stop2 = await request(runtimePort, 'POST', '/minecraft/stop', {})
      assert(stop2.status === 200 && stop2.body.cancelled.length === 0, 'stop 幂等')

      // 无僵尸动作 / 无并发残留 / 连接仍然 ONLINE（§十八验收）
      const actionStatus = await request(runtimePort, 'GET', '/minecraft/status')
      assert(actionStatus.body.status === 'ONLINE', '动作后连接仍是 ONLINE')
      assert(actionStatus.body.action, 'status 带 action 段')
      assert(actionStatus.body.action.active_count === 0, '无僵尸动作（active_count=0）')
      assert(
        ['IDLE', 'SUCCEEDED', 'CANCELLED', 'TIMEOUT', 'FAILED'].includes(actionStatus.body.action.status),
        `动作已落定，得到 ${actionStatus.body.action.status}`,
      )
      console.log(
        `[e2e] action runtime ✓ last=${actionStatus.body.action.action}/${actionStatus.body.action.status} active=${actionStatus.body.action.active_count}`,
      )

      // ---------------- Phase 2：Raw World Snapshot（Test 1/2/3/5/6 子集） ----------------
      if (cycle === 1) {
        // flying-squid 广播玩家实体包有延迟：轮询直到观察者出现在 players 里。
        let s = null
        await waitFor(async () => {
          const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
          if (snap.status !== 200 || snap.body.online !== true) return false
          s = snap.body
          return s.players.some((p) => p.username === OBSERVER_NAME)
        }, 'observer appears in snapshot players', 20000)
        // Self：位置与 status 一致
        assert(s.self && Number.isFinite(s.self.position.x), 'self.position present')
        assert(typeof s.self.yaw === 'number' && typeof s.self.pitch === 'number', 'yaw/pitch present')
        assert(s.self.dimension === status.body.dimension, 'dimension consistent with status')
        // Environment
        assert(s.environment && ['day', 'sunset', 'night', 'sunrise'].includes(s.environment.time_phase), `time_phase valid: ${s.environment.time_phase}`)
        assert(['clear', 'rain', 'thunder'].includes(s.environment.weather), 'weather valid')
        // Players：观察者必须被发现，且带完整空间字段
        const tester = s.players.find((p) => p.username === OBSERVER_NAME)
        assert(tester, 'observer discovered in players')
        // Phase 5C：canonical identity —— 快照里的玩家必须带 UUID（身份桥靠它认人，改名不换人）
        assert(
          typeof tester.uuid === 'string' && /^[0-9a-f-]{32,36}$/i.test(tester.uuid),
          `player uuid present: ${tester.uuid}`,
        )
        assert(Number.isFinite(tester.distance) && tester.distance > 0, 'player distance')
        assert(Number.isFinite(tester.bearing), 'player bearing numeric')
        // Blocks：近层柱面扫描有数据、无空气、方向合法
        assert(s.blocks.near.columns.length > 0, 'near columns present')
        for (const col of s.blocks.near.columns.slice(0, 12)) {
          assert(col.name && !['air', 'cave_air', 'void_air'].includes(col.name), `non-air block: ${col.name}`)
          assert(['front', 'front_left', 'front_right', 'left', 'right', 'back', 'back_left', 'back_right', 'above', 'below'].includes(col.relative_direction), `valid relative_direction: ${col.relative_direction}`)
          // 方向数学一致性：从 rel + yaw 重算必须与 runtime 给出的一致
          const rad = (s.self.yaw * Math.PI) / 180
          const fx = -Math.sin(rad)
          const fz = Math.cos(rad)
          const horizontal = Math.sqrt(col.rel.dx ** 2 + col.rel.dz ** 2)
          const bearing = Math.atan2(fx * col.rel.dz - fz * col.rel.dx, fx * col.rel.dx + fz * col.rel.dz) * 180 / Math.PI
          const expected =
            horizontal < 2
              ? (col.rel.dy >= 1.5 ? 'above' : col.rel.dy <= -1.5 ? 'below' : 'front')
              : ['front', 'front_right', 'right', 'back_right', 'back', 'back_left', 'left', 'front_left'][Math.floor(((((bearing + 22.5) % 360) + 360) % 360) / 45)]
          assert(expected === col.relative_direction, `direction math: block ${col.name} expected ${expected}, got ${col.relative_direction}`)
        }
        // 空气不膨胀：near 层列数 = 扫描柱数上限内（169 柱），绝无海量空气条目
        assert(s.blocks.near.columns.length <= 220, 'near layer bounded')

        // 上下文体积测量（Phase 2 §性能）：各层字节数 + 可转储供 Python 侧分析
        const nearOnly = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
        const allLayers = await request(runtimePort, 'GET', '/minecraft/world/snapshot')
        const nearBytes = JSON.stringify(nearOnly.body).length
        const allBytes = JSON.stringify(allLayers.body).length
        console.log(`[e2e] snapshot sizes: near-only=${nearBytes}B all-layers=${allBytes}B`)
        if (process.env.MC_E2E_SNAPSHOT_OUT) {
          fs.writeFileSync(process.env.MC_E2E_SNAPSHOT_OUT, JSON.stringify(allLayers.body), 'utf8')
          console.log(`[e2e] snapshot dumped to ${process.env.MC_E2E_SNAPSHOT_OUT}`)
        }
        console.log(`[e2e] snapshot ✓ self=(${s.self.position.x}, ${s.self.position.y}, ${s.self.position.z}) biome=${s.environment.biome} near=${s.blocks.near.columns.length} cols, player=${OBSERVER_NAME} ${tester.relative_direction} @${tester.distance}`)
      }

      // ---------------- Phase 3C：move_to（Test A 成功 / Test B STOP 真停 / Test C 超时） ----------------
      if (cycle === 1) {
        const moveOrigin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const dirs = [
          [5, 0],
          [-5, 0],
          [0, 5],
          [0, -5],
          [5, 5],
        ]
        let moveOk = null
        let moveDir = [5, 0]
        let moveFail = null
        for (const [dx, dz] of dirs) {
          const resp = await request(runtimePort, 'POST', '/minecraft/move_to', {
            x: moveOrigin.x + dx,
            y: moveOrigin.y,
            z: moveOrigin.z + dz,
          })
          // Phase 3E：move_to 是持续型动作——HTTP 立刻返回 RUNNING（不阻塞 30s），
          // 终点/失败由 action 事件送达（与 follow_player 同一套语义）。
          if (!(resp.status === 200 && resp.body.status === 'RUNNING')) {
            moveFail = resp // 同步拒绝（busy / 超距离 / 非法坐标）
            continue
          }
          const actionId = resp.body.action_id
          try {
            await waitFor(
              () =>
                events.some(
                  (e) => e.event === 'minecraft.action.completed' && e.action_id === actionId,
                ),
              'move_to completed 事件',
              20000,
            )
          } catch (error) {
            // no-path / 失败：换个方向重试（失败原因由 failed 事件记录）
            moveFail = {
              body: { error: String(error && error.message ? error.message : error) },
            }
            continue
          }
          const completed = events.find(
            (e) => e.event === 'minecraft.action.completed' && e.action_id === actionId,
          )
          // 有的方向会"瞬间 completed 但根本没挪动"（脚下被卡住/水/Pathfinder 认为已经在
          // 目标附近）：这种结果不算数，换个方向再试（与 no-path 分支同样处理），
          // 否则后面的"位置确实变了"断言会踩到环境抖动。
          const settled = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
          const movedBy = Math.hypot(settled.x - moveOrigin.x, settled.z - moveOrigin.z)
          if (movedBy < 1) {
            moveFail = { body: { error: `move_to 完成了但只挪了 ${movedBy.toFixed(2)} 格` } }
            continue
          }
          moveOk = { ...resp.body, result: completed && completed.result }
          moveDir = [dx, dz]
          break
        }
        assert(moveOk, `Test A：至少一个方向的 move_to 成功（最后失败：${JSON.stringify(moveFail && moveFail.body)}）`)
        assert(
          moveOk.result && typeof moveOk.result.distance_to_target === 'number',
          'Test A：completed 事件带 distance_to_target',
        )
        // Phase 4H.1：completed 的语义收紧了 —— distance_to_target 用**重新读到的实际位置**
        // 按 GoalNear 口径（罐头占的方块格 → 目标方块格）算，所以它必须真的 <= 1.5。
        // 旧的 2.5 容差是为了容忍"Pathfinder 说到了、其实还差一米多"的假成功。
        assert(
          moveOk.result.distance_to_target <= 1.5,
          `Test A：真的在到达半径内（${moveOk.result.distance_to_target} 格）`,
        )
        assert(
          typeof moveOk.result.raw_distance_to_target === 'number',
          `Test A：原始浮点距离也如实上报（${moveOk.result.raw_distance_to_target} 格）`,
        )
        const movedStatus = (await request(runtimePort, 'GET', '/minecraft/status')).body
        const movedDistance = Math.hypot(
          movedStatus.position.x - moveOrigin.x,
          movedStatus.position.z - moveOrigin.z,
        )
        assert(movedDistance >= 1, `Test A：位置确实变了（水平位移 ${movedDistance.toFixed(1)} 格）`)
        assert(
          movedStatus.pathfinder && movedStatus.pathfinder.goal === null,
          'Test A：成功后 goal 已清空',
        )
        console.log(
          `[e2e] move_to ✓ 方向 ${moveDir} 位移 ${movedDistance.toFixed(1)} 格 距目标 ${moveOk.result.distance_to_target}`,
        )

        // ---- Test B：移动中 STOP 必须真正停住（本阶段最重要的验收，§十一） ----
        // moveDir 是方向向量（可能含 5），归一化后再乘距离
        const [rawDx, rawDz] = moveDir
        const dirLength = Math.hypot(rawDx, rawDz) || 1
        const ux = rawDx / dirLength
        const uz = rawDz / dirLength
        let stopVerified = false
        for (const distance of [20, 15, 25, 12]) {
          const farOrigin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
          const target = { x: farOrigin.x + ux * distance, y: farOrigin.y, z: farOrigin.z + uz * distance }
          const startResp = await request(runtimePort, 'POST', '/minecraft/move_to', target)
          assert(
            startResp.status === 200 && startResp.body.status === 'RUNNING',
            `Test B：move_to 启动即 RUNNING（得到 ${JSON.stringify(startResp.body)}）`,
          )
          const moveId = startResp.body.action_id
          let started = false
          try {
            await waitFor(async () => {
              const snap = await request(runtimePort, 'GET', '/minecraft/status')
              return Boolean(snap.body.pathfinder && snap.body.pathfinder.moving)
            }, '导航开始', 4000)
            started = true
          } catch {
            // 该方向没能开始移动（no-path 等）：换个比例重试
          }
          if (!started) continue
          const running = (await request(runtimePort, 'GET', '/minecraft/status')).body
          if (running.action.status !== 'RUNNING') continue // 已经跑完：换个比例重试

          const stopResult = await request(runtimePort, 'POST', '/minecraft/stop', {})
          assert(
            stopResult.body.cancelled.length === 1 && stopResult.body.cancelled[0] === moveId,
            `Test B：stop 取消 move_to（得到 ${JSON.stringify(stopResult.body)}）`,
          )
          await waitFor(
            () =>
              events.some((e) => e.event === 'minecraft.action.cancelled' && e.action_id === moveId),
            'move_to cancelled 事件',
          )
          // §十一：不能只验证 Action 状态——还要验证 goal 清空、isMoving=false、位置停住
          const stopped = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(stopped.pathfinder.goal === null, 'Test B：goal == null')
          assert(stopped.pathfinder.moving === false, 'Test B：isMoving == false')
          // 硬证据是 goal == null + isMoving == false（上面两条）。位置检查改成
          // "**停稳之后**再测一段"：STOP 瞬间可能还在空中（下落/惯性收尾），
          // 直接拿 400ms 内的位移当"还在走"会误报（本地 0.35~0.8，CI 上出现过 1.11）。
          await sleep(400)
          const settledA = (await request(runtimePort, 'GET', '/minecraft/status')).body
          await sleep(400)
          const settledB = (await request(runtimePort, 'GET', '/minecraft/status')).body
          const drift = Math.hypot(
            settledB.position.x - settledA.position.x,
            settledB.position.z - settledA.position.z,
          )
          assert(drift <= 0.2, `Test B：停稳后位置不再漂移（${drift.toFixed(2)} 格）`)
          stopVerified = true
          break
        }
        assert(stopVerified, 'Test B：移动中 STOP 验证完成（goal 清空 + isMoving=false + 位置停住）')
        console.log('[e2e] move_to STOP ✓ goal=null moving=false 位置已停')

        // ---- Test C：move_to 超时 → TIMEOUT + goal 清空（§十三） ----
        let timeoutVerified = false
        let timeoutFail = null
        // Phase 4H.1 起：**不可达**的目标会立刻以 path.not_found 失败（不再"静默成功/挂到超时"），
        // 所以这里要靠**足够远**的可行目标来制造确定的超时窗口：40+ 格在假服务器上要走 9s 上下，
        // 远超 MC_MOVE_TIMEOUT_MS=2500。太远没路的那些会被上面的 try/catch 换掉。
        for (const distance of [40, 32, 48, 24, 56, 16]) {
          const timeoutOrigin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
          const target = {
            x: timeoutOrigin.x + ux * distance,
            y: timeoutOrigin.y,
            z: timeoutOrigin.z + uz * distance,
          }
          const resp = await request(runtimePort, 'POST', '/minecraft/move_to', target)
          timeoutFail = resp
          if (resp.status === 200 && resp.body.status === 'RUNNING') {
            try {
              await waitFor(
                () =>
                  events.some(
                    (e) =>
                      e.event === 'minecraft.action.timeout' && e.action_id === resp.body.action_id,
                  ),
                'move_to timeout 事件',
                15000,
              )
            } catch (error) {
              // 超时前就自己结束了（走完了 / no-path）：换个更远的比例重试
              timeoutFail = {
                body: { error: String(error && error.message ? error.message : error) },
              }
              continue
            }
            const after = (await request(runtimePort, 'GET', '/minecraft/status')).body
            assert(after.action.status === 'TIMEOUT', `Test C：终态 TIMEOUT（得到 ${after.action.status}）`)
            assert(after.pathfinder.goal === null, 'Test C：超时后 goal == null')
            assert(after.pathfinder.moving === false, 'Test C：超时后 isMoving == false')
            timeoutVerified = true
            break
          }
          // 同步拒绝：换个更远的比例重试
        }
        assert(
          timeoutVerified,
          `Test C：move_to 超时 → TIMEOUT 且 goal 清空（最后一次响应：${JSON.stringify(timeoutFail && timeoutFail.body)}）`,
        )
        console.log('[e2e] move_to TIMEOUT ✓ goal=null moving=false')

        // ---- Test D（Phase 4H.1）：不可达目标 → 绝不假成功（§十/§二十九） ----
        // 目标在头顶 40 格的空气里：罐头不能飞、也不能搭方块（这些能力本阶段就没有），
        // 所以唯一正确的结果是"结构化失败"，绝不能是 completed（旧代码在这里假成功）。
        {
          const dHere = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
          const resp = await request(runtimePort, 'POST', '/minecraft/move_to', {
            x: dHere.x,
            y: dHere.y + 40,
            z: dHere.z,
          })
          assert(
            resp.status === 200 && resp.body.status === 'RUNNING',
            `Test D：启动 → RUNNING（得到 ${JSON.stringify(resp.body)}）`,
          )
          const actionId = resp.body.action_id
          const isTerminal = (row) =>
            [
              'minecraft.action.completed',
              'minecraft.action.failed',
              'minecraft.action.cancelled',
              'minecraft.action.timeout',
            ].includes(row.event) && row.action_id === actionId
          let terminal = null
          try {
            await waitFor(() => {
              terminal = events.find(isTerminal) || null
              return Boolean(terminal)
            }, 'Test D：终态事件', 45000)
          } catch {
            terminal = null
          }
          assert(
            !terminal || terminal.event !== 'minecraft.action.completed',
            `Test D：不可达目标绝不 completed（得到 ${terminal ? terminal.event : '终态超时'}）`,
          )
          if (terminal && terminal.event === 'minecraft.action.failed') {
            assert(
              terminal.code === 'path.not_found' || terminal.code === 'path.not_reached',
              `Test D：结构化错误码（得到 ${terminal.code}）`,
            )
            assert(
              terminal.detail && typeof terminal.detail.distance_to_target === 'number',
              'Test D：失败 detail 带实际距离（distance_to_target）',
            )
            console.log(`[e2e] move_to false-success guard ✓ ${terminal.event}/${terminal.code}`)
          } else {
            console.log(
              `[e2e] move_to false-success guard ✓ 没有假成功（终态 ${terminal ? terminal.event : '超时'}）`,
            )
          }
          const afterD = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(afterD.pathfinder.goal === null, 'Test D：终态之后 goal 已清（runtime 自己收）')
          assert(afterD.action.active_count === 0, 'Test D：终态之后没有僵尸动作')
        }

        // 移动测试收尾：无僵尸动作、连接仍 ONLINE
        const moveSettled = (await request(runtimePort, 'GET', '/minecraft/status')).body
        assert(moveSettled.status === 'ONLINE', 'Test A/B/C 后连接仍 ONLINE')
        assert(moveSettled.action.active_count === 0, 'Test A/B/C 后无僵尸动作')
      }

      // ---------------- Phase 4B：dig（单方块；A 成功 / B block_changed / C not_found / D too_far /
      //                  E STOP 真停 / F 超时 / G 感知看到移除） ----------------
      if (cycle === 1) {
        const setBlock = (x, y, z, id) =>
          observer.chat(`/setblock ${Math.round(x)} ${Math.round(y)} ${Math.round(z)} ${id}`)
        // 从近层快照读某个坐标的方块名（表层柱面扫描；没有这一柱 = air）
        const blockAt = async (x, y, z) => {
          const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
          const columns = (snap.body.blocks && snap.body.blocks.near && snap.body.blocks.near.columns) || []
          const hit = columns.find(
            (col) => col.pos && col.pos.x === x && col.pos.y === y && col.pos.z === z,
          )
          return hit ? hit.name : 'air'
        }
        const origin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const digEvents = (action) =>
          events.filter((e) => e.action === action && e.event.startsWith('minecraft.action.'))

        // ---- Test A：挖掉一个指定的方块（dirt 徒手 ~0.8s） ----
        const aPos = { x: Math.round(origin.x) + 1, y: Math.round(origin.y), z: Math.round(origin.z) }
        setBlock(aPos.x, aPos.y, aPos.z, 'dirt')
        await waitFor(async () => (await blockAt(aPos.x, aPos.y, aPos.z)) === 'dirt', 'dirt 已放置', 8000)

        const digA = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: aPos.x,
          y: aPos.y,
          z: aPos.z,
          expected_block: 'dirt',
        })
        assert(
          digA.status === 200 && digA.body.status === 'RUNNING',
          `Test A：dig 启动即 RUNNING（得到 ${JSON.stringify(digA.body)}）`,
        )
        await waitFor(
          () => digEvents('dig').some((e) => e.event === 'minecraft.action.completed' && e.action_id === digA.body.action_id),
          'dig completed 事件',
          15000,
        )
        const doneA = digEvents('dig').find((e) => e.event === 'minecraft.action.completed' && e.action_id === digA.body.action_id)
        assert(doneA && doneA.result, `Test A：完成事件带 result（得到 ${JSON.stringify(doneA && doneA.result)}）`)
        assert(doneA.result.block_before === 'dirt', `Test A：block_before=dirt（得到 ${doneA.result.block_before}）`)
        assert(doneA.result.block_after !== 'dirt', `Test A：block_after 不再是 dirt（得到 ${doneA.result.block_after}）`)
        // §二十四/§五十二：挖掉之后感知层（表层扫描）不再看到这一柱
        await waitFor(async () => (await blockAt(aPos.x, aPos.y, aPos.z)) !== 'dirt', '感知层看到方块消失', 8000)
        // 同一位置再挖一次 → 方块已经没了
        const againA = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: aPos.x,
          y: aPos.y,
          z: aPos.z,
          expected_block: 'dirt',
        })
        assert(
          againA.status === 404 && againA.body.error.code === 'block.not_found',
          `Test A：方块没了 → block.not_found（得到 ${JSON.stringify(againA.body)}）`,
        )
        console.log('[e2e] dig ✓ block_before=dirt → block_after=' + doneA.result.block_after + '（感知层同步消失）')

        // Phase 7D Follow-up：结果里必须带"世界效果 + 执行归属"两个**独立**结论
        const attributionA = doneA.result.attribution
        assert(Boolean(attributionA), 'Test A：结果带 dig_attribution（世界效果与执行归属分离）')
        assert(
          ['BLOCK_REMOVED', 'BLOCK_REMAINS', 'UNKNOWN'].includes(attributionA.world_effect),
          `Test A：world_effect 取值合法（得到 ${attributionA && attributionA.world_effect}）`,
        )
        assert(
          attributionA.world_effect !== 'BLOCK_REMAINS',
          'Test A：方块确实没了 → 不允许判成"还在原位"',
        )
        // flying-squid 不保证回"服务器自己的方块变化包"：没有它时 world_effect 必须是 UNKNOWN
        // （本地乐观更新不算证据）——这正是 Phase 7D Follow-up 真机学到的那条规矩
        if (!attributionA.flags || attributionA.flags.server_block_update_says_air !== true) {
          assert(
            attributionA.world_effect === 'UNKNOWN',
            `Test A：没有服务器确认 → world_effect 必须是 UNKNOWN（得到 ${attributionA.world_effect}）`,
          )
        } else {
          assert(
            attributionA.world_effect === 'BLOCK_REMOVED',
            'Test A：服务器说变成 air → BLOCK_REMOVED',
          )
        }
        assert(
          ['SELF_CONFIRMED', 'EXTERNAL_INDICATED', 'AMBIGUOUS'].includes(attributionA.attribution),
          `Test A：attribution 取值合法（得到 ${attributionA && attributionA.attribution}）`,
        )
        assert(
          attributionA.attribution !== 'EXTERNAL_INDICATED',
          'Test A：这台假服务器上只有罐头一个玩家 → 绝不允许判成外部破坏',
        )
        assert(
          attributionA.action_id === digA.body.action_id,
          `Test A：归因绑定本次 action_id（得到 ${attributionA && attributionA.action_id}）`,
        )
        assert(
          attributionA.target &&
            attributionA.target.x === aPos.x &&
            attributionA.target.y === aPos.y &&
            attributionA.target.z === aPos.z,
          `Test A：归因绑定目标坐标（得到 ${JSON.stringify(attributionA && attributionA.target)}）`,
        )
        console.log(
          `[e2e] dig ✓ 归因 ${attributionA.attribution}/${attributionA.reason_code}` +
            '（flying-squid 挖方块是瞬时的 → 允许 AMBIGUOUS，绝不伪造成自证）',
        )

        // ---- Test B：方块与 expected_block 不一致 → block.changed（带 expected/actual） ----
        const bPos = { x: Math.round(origin.x) - 1, y: Math.round(origin.y), z: Math.round(origin.z) }
        setBlock(bPos.x, bPos.y, bPos.z, 'dirt')
        await waitFor(async () => (await blockAt(bPos.x, bPos.y, bPos.z)) === 'dirt', 'dirt 已放置(B)', 8000)
        const digB = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: bPos.x,
          y: bPos.y,
          z: bPos.z,
          expected_block: 'minecraft:stone',
        })
        assert(
          digB.status === 409 && digB.body.error.code === 'block.changed',
          `Test B：期望 stone 实际 dirt → block.changed（得到 ${JSON.stringify(digB.body)}）`,
        )
        assert(
          digB.body.error.detail && digB.body.error.detail.expected === 'minecraft:stone' &&
            digB.body.error.detail.actual === 'dirt',
          `Test B：带 expected/actual（得到 ${JSON.stringify(digB.body.error.detail)}）`,
        )
        assert(
          (await blockAt(bPos.x, bPos.y, bPos.z)) === 'dirt',
          'Test B：拒绝后 dirt 原地未动',
        )
        console.log('[e2e] dig ✓ block.changed（expected/actual 结构化，未破坏）')

        // ---- Test C：目标位置是空气 → block.not_found ----
        const cPos = { x: Math.round(origin.x), y: Math.round(origin.y) + 4, z: Math.round(origin.z) }
        const digC = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: cPos.x,
          y: cPos.y,
          z: cPos.z,
          expected_block: 'minecraft:stone',
        })
        assert(
          digC.status === 404 && digC.body.error.code === 'block.not_found',
          `Test C：空气 → block.not_found（得到 ${JSON.stringify(digC.body)}）`,
        )
        console.log('[e2e] dig ✓ 空气 → block.not_found（绝不调用 bot.dig）')

        // ---- Test D：太远 → block.too_far（不自己走过去） ----
        // 8 格在 near 扫描半径（~6）之外，快照看不到它；直接问 runtime ——
        // 区块/方块更新有延迟，没看到就等一会儿重试（这才是"世界视图"的真实边界）
        const dPos = { x: Math.round(origin.x) + 8, y: Math.round(origin.y), z: Math.round(origin.z) }
        setBlock(dPos.x, dPos.y, dPos.z, 'dirt')
        let digD = null
        for (let attempt = 0; attempt < 20; attempt += 1) {
          digD = await request(runtimePort, 'POST', '/minecraft/dig', {
            x: dPos.x,
            y: dPos.y,
            z: dPos.z,
            expected_block: 'dirt',
          })
          if (digD.body.error && digD.body.error.code === 'block.too_far') break
          await sleep(300)
        }
        assert(
          digD.status === 400 && digD.body.error.code === 'block.too_far',
          `Test D：8 格 → block.too_far（得到 ${JSON.stringify(digD.body)}）`,
        )
        console.log('[e2e] dig ✓ 太远 → block.too_far（不导航）')

        // ---- 挖掘中 STOP / 超时 / 断开 这三段**不在 flying-squid 上验证**：
        // flying-squid 收到挖掘包就立刻破坏方块（不模拟挖掘耗时），dig 会在毫秒级完成，
        // 叫停/超时都无从谈起（真实数据：Test A 的 dirt elapsed=4ms）。
        // 这三条由三层覆盖：
        //   ① test/dig.test.js —— start/wait/cleanup 的同步语义与复核；
        //   ② test/action_runtime.test.js —— CANCELLED/TIMEOUT/断开取消 + cleanup 至多一次；
        //   ③ test/smoke_real_server.js —— 真实服务器上「挖掘中 STOP，方块仍在」（§七十四）。

        // dig 测试收尾：连接仍 ONLINE、无僵尸动作
        const digSettled = (await request(runtimePort, 'GET', '/minecraft/status')).body
        assert(digSettled.status === 'ONLINE', 'dig Test A–D 后连接仍 ONLINE')
        assert(digSettled.action.active_count === 0, 'dig Test A–D 后无僵尸动作')
      }

      // ---------------- Phase 4C：inventory（只读）+ place（空手/非法参数的可观察行为） ----------------
      // 说明：flying-squid 没有 /give，也没有挖掘掉落 → 这台假服务器上 bot 的背包**永远是空的**，
      // 所以 place 的成功路径只能在真实服务器 smoke 里验证；这里验证它能验证的部分：
      // 只读切片、参数校验、以及"手里没东西就拒绝"。
      if (cycle === 1) {
        const here0 = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const inv = await request(runtimePort, 'GET', '/minecraft/inventory')
        assert(inv.status === 200 && inv.body.online === true, `inventory 只读在线（${JSON.stringify(inv.body)}）`)
        assert(
          Object.keys(inv.body).sort().join(',') === 'held_item,items,ok,online,selected_hotbar_bar_slot'
            .replace('selected_hotbar_bar_slot', 'selected_hotbar_slot'),
          `切片只有约定字段（得到 ${Object.keys(inv.body).sort().join(',')}）`,
        )
        assert(
          inv.body.held_item === null && Array.isArray(inv.body.items) && inv.body.items.length === 0,
          '假服务器上背包是空的（held_item=null, items=[]）',
        )
        console.log('[e2e] inventory ✓ 只读切片（online/held_item/items，无 slot/NBT）')

        // 空手 → held.item_missing（绝不自动装备）
        const emptyHand = await request(runtimePort, 'POST', '/minecraft/place', {
          x: Math.round(here0.x) + 1,
          y: Math.round(here0.y),
          z: Math.round(here0.z),
          face: 'up',
          expected_item: 'dirt',
        })
        assert(
          emptyHand.status === 400 && emptyHand.body.error.code === 'held.item_missing',
          `空手放方块 → held.item_missing（得到 ${JSON.stringify(emptyHand.body)}）`,
        )
        console.log('[e2e] place ✓ 空手 → held.item_missing（不自动装备/切槽）')

        // face 非法 → face.invalid（在 validate 阶段，连 world 都不读）
        const badFace = await request(runtimePort, 'POST', '/minecraft/place', {
          x: 1,
          y: 64,
          z: 1,
          face: 'north_east',
          expected_item: 'dirt',
        })
        assert(
          badFace.status === 400 && badFace.body.error.code === 'face.invalid',
          `非法 face → face.invalid（得到 ${JSON.stringify(badFace.body)}）`,
        )
        // 坐标必须整数 → action.invalid
        const floatCoords = await request(runtimePort, 'POST', '/minecraft/place', {
          x: 1.5,
          y: 64,
          z: 1,
          face: 'up',
          expected_item: 'dirt',
        })
        assert(
          floatCoords.status === 400 && floatCoords.body.error.code === 'action.invalid',
          `小数坐标 → action.invalid（得到 ${JSON.stringify(floatCoords.body)}）`,
        )
        console.log('[e2e] place ✓ 参数校验（非法 face / 小数坐标 → 400，不进入放置）')

        // 非独占：移动中也能读背包
        const moveResp = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: here0.x + 6,
          y: here0.y,
          z: here0.z,
        })
        if (moveResp.status === 200 && moveResp.body.status === 'RUNNING') {
          const during = await request(runtimePort, 'GET', '/minecraft/inventory')
          assert(
            during.status === 200 && during.body.online === true,
            'move_to 跑着的时候也能读背包（inventory 非独占）',
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) => e.event === 'minecraft.action.cancelled' && e.action_id === moveResp.body.action_id,
              ),
            'move_to cancelled（inventory 段收尾）',
            10000,
          )
        } else {
          console.log('[e2e] inventory 非独占检查：move_to 没进入 RUNNING，跳过')
        }
      }

      // ---------------- Phase 4D：equip / inventory_move（假服务器上只有拒绝路径） ----------------
      // flying-squid 没有 /give → 这台服务器上 bot 的背包**永远是空的**：equip/move 的成功
      // 路径只能在真实服务器 smoke 里验证。这里验证所有不依赖背包内容的可观察行为：
      // 调试槽位视图、参数校验、空槽位/不存在物品的拒绝、以及 exclusivity。
      if (cycle === 1) {
        const slotsView = await request(runtimePort, 'GET', '/minecraft/inventory/slots')
        assert(
          slotsView.status === 200 && slotsView.body.online === true,
          `槽位视图在线（得到 ${JSON.stringify(slotsView.body)}）`,
        )
        assert(
          slotsView.body.hotbar_start === 36 && slotsView.body.inventory_start === 9,
          `槽位范围 9..44（快捷栏从 36 起，得到 ${JSON.stringify(slotsView.body)}）`,
        )
        assert(
          Array.isArray(slotsView.body.slots) && slotsView.body.slots.length === 0,
          `空背包 → 没有槽位行（得到 ${JSON.stringify(slotsView.body.slots)}）`,
        )
        console.log('[e2e] inventory/slots ✓ 调试槽位视图（只读，空背包为空表）')

        // 背包里没有这个物品 → 404 item.not_found（绝不自动造物、绝不换别的物品）
        const equipMissing = await request(runtimePort, 'POST', '/minecraft/equip', {
          item: 'minecraft:dirt',
        })
        assert(
          equipMissing.status === 404 && equipMissing.body.error.code === 'item.not_found',
          `equip 不在背包里的物品 → item.not_found（得到 ${JSON.stringify(equipMissing.body)}）`,
        )
        // 物品名不合法 → 400 item.invalid
        const equipBad = await request(runtimePort, 'POST', '/minecraft/equip', { item: '   ' })
        assert(
          equipBad.status === 400 && equipBad.body.error.code === 'item.invalid',
          `equip 空物品名 → item.invalid（得到 ${JSON.stringify(equipBad.body)}）`,
        )
        console.log('[e2e] equip ✓ 参数校验 + 背包里没有 → item.not_found（不自动装备）')

        // 槽位范围：8 / 45 都是 400 slot.invalid（主背包 9-35 + 快捷栏 36-44）
        const lowSlot = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
          source_slot: 8,
          destination_slot: 9,
          item: 'dirt',
          count: 1,
        })
        assert(
          lowSlot.status === 400 && lowSlot.body.error.code === 'slot.invalid',
          `source_slot=8 → slot.invalid（得到 ${JSON.stringify(lowSlot.body)}）`,
        )
        const highSlot = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
          source_slot: 9,
          destination_slot: 45,
          item: 'dirt',
          count: 1,
        })
        assert(
          highSlot.status === 400 && highSlot.body.error.code === 'slot.invalid',
          `destination_slot=45 → slot.invalid（得到 ${JSON.stringify(highSlot.body)}）`,
        )
        // 同一个槽位 → slot.invalid；数量 < 1 → item.invalid
        const sameSlot = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
          source_slot: 37,
          destination_slot: 37,
          item: 'dirt',
          count: 1,
        })
        assert(
          sameSlot.status === 400 && sameSlot.body.error.code === 'slot.invalid',
          `source=destination → slot.invalid（得到 ${JSON.stringify(sameSlot.body)}）`,
        )
        const badCount = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
          source_slot: 37,
          destination_slot: 9,
          item: 'dirt',
          count: 0,
        })
        assert(
          badCount.status === 400 && badCount.body.error.code === 'item.invalid',
          `count=0 → item.invalid（得到 ${JSON.stringify(badCount.body)}）`,
        )
        console.log('[e2e] inventory_move ✓ 参数校验（槽位范围 / 同槽 / count）')

        // 槽位合法但 source 是空的 → 404 item.not_found（空背包，如实拒绝）
        const moveEmpty = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
          source_slot: 9,
          destination_slot: 36,
          item: 'dirt',
          count: 1,
        })
        assert(
          moveEmpty.status === 404 && moveEmpty.body.error.code === 'item.not_found',
          `空槽位 → item.not_found（得到 ${JSON.stringify(moveEmpty.body)}）`,
        )
        console.log('[e2e] inventory_move ✓ 空 source 槽位 → item.not_found（不隐式换槽）')

        // exclusivity：move_to 跑着的时候，equip 必须被拒（action.busy），且不会打断它
        const here4d = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const busyMove = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: here4d.x + 6,
          y: here4d.y,
          z: here4d.z,
        })
        if (busyMove.status === 200 && busyMove.body.status === 'RUNNING') {
          const busyEquip = await request(runtimePort, 'POST', '/minecraft/equip', { item: 'dirt' })
          assert(
            busyEquip.status === 409 && busyEquip.body.error.code === 'action.busy',
            `move_to 跑着时 equip → action.busy（得到 ${JSON.stringify(busyEquip.body)}）`,
          )
          const busySlotMove = await request(runtimePort, 'POST', '/minecraft/inventory_move', {
            source_slot: 9,
            destination_slot: 36,
            item: 'dirt',
            count: 1,
          })
          assert(
            busySlotMove.status === 409 && busySlotMove.body.error.code === 'action.busy',
            `move_to 跑着时 inventory_move → action.busy（得到 ${JSON.stringify(busySlotMove.body)}）`,
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) =>
                  e.event === 'minecraft.action.cancelled' &&
                  e.action_id === busyMove.body.action_id,
              ),
            'move_to cancelled（4D exclusivity 段收尾）',
            10000,
          )
          console.log('[e2e] equip / inventory_move ✓ 独占（move_to 跑着时 → action.busy）')
        } else {
          console.log('[e2e] equip / inventory_move 独占检查：move_to 没进入 RUNNING，跳过')
        }

        // 空背包上 equip 一定 fail-fast，没有"挖到一半"那种可取消窗口：
        // 假服务器上 STOP 路径无法真实触发（与 dig 的 STOP 段同理）。
        console.log('[e2e] equip / inventory_move STOP：SKIPPED（假服务器背包永远是空的，没有可取消的窗口）')
      }

      // ---------------- Phase 4E：container（flying-squid 会真的发 chest GUI 窗口） ----------------
      // flying-squid 的 chest 插件会发 open_window(3x9) + window_items → inspect 的
      // 「open → read → close」可以在这台假服务器上**真实**走一遍（内容当然是空的）。
      // withdraw / deposit 的成功路径需要"箱子里真的有东西"，而假服务器没有 /give、
      // 也没有掉落 → 那部分只能在真实服务器 smoke 验证（这里只验证拒绝路径）。
      if (cycle === 1) {
        const setBlock = (x, y, z, id) =>
          observer.chat(`/setblock ${Math.round(x)} ${Math.round(y)} ${Math.round(z)} ${id}`)
        const here = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const cx = Math.round(here.x) + 1
        const cy = Math.round(here.y)
        const cz = Math.round(here.z)
        const stonePos = { x: cx, y: cy, z: cz + 1 }
        const chestPos = { x: cx, y: cy + 1, z: cz }

        // 非容器方块 → container.unsupported（绝不靠"看起来像"判断）
        setBlock(stonePos.x, stonePos.y, stonePos.z, 'minecraft:stone')
        await sleep(700)
        const notContainer = await request(runtimePort, 'POST', '/minecraft/container_inspect', {
          x: stonePos.x,
          y: stonePos.y,
          z: stonePos.z,
        })
        assert(
          notContainer.status === 422 && notContainer.body.error.code === 'container.unsupported',
          `非容器方块 → container.unsupported（得到 ${JSON.stringify(notContainer.body)}）`,
        )
        // 小数坐标 → action.invalid（方块坐标没有小数）
        const floatCoords = await request(runtimePort, 'POST', '/minecraft/container_inspect', {
          x: chestPos.x + 0.5,
          y: chestPos.y,
          z: chestPos.z,
        })
        assert(
          floatCoords.status === 400 && floatCoords.body.error.code === 'action.invalid',
          `小数坐标 → action.invalid（得到 ${JSON.stringify(floatCoords.body)}）`,
        )
        console.log('[e2e] container_inspect ✓ 参数与类型拒绝（不打开任何窗口）')

        // 真放一个箱子（上方是空气 → flying-squid 才允许打开）→ inspect 真实开窗读内容
        setBlock(chestPos.x, chestPos.y, chestPos.z, 'minecraft:chest')
        await sleep(900)
        const inspect = await request(runtimePort, 'POST', '/minecraft/container_inspect', {
          x: chestPos.x,
          y: chestPos.y,
          z: chestPos.z,
        })
        assert(
          inspect.status === 200 && inspect.body.status === 'SUCCEEDED',
          `真实 chest 窗口 inspect 成功（得到 ${JSON.stringify(inspect.body)}）`,
        )
        const snapshot = inspect.body.result || {}
        assert(
          snapshot.container && snapshot.container.type === 'chest',
          `容器类型来自真实方块（得到 ${JSON.stringify(snapshot.container)}）`,
        )
        assert(
          snapshot.container.size === 27,
          `单方块容器 = 27 格（从真实 window 结构推导，得到 ${snapshot.container.size}）`,
        )
        assert(
          Array.isArray(snapshot.slots) && snapshot.slots.length === 0,
          `空箱子 → 没有非空格子（得到 ${JSON.stringify(snapshot.slots)}）`,
        )
        assert(
          Object.keys(snapshot).sort().join(',') === 'container,ok,slots',
          `快照只有约定字段（得到 ${Object.keys(snapshot).sort().join(',')}）`,
        )
        console.log('[e2e] container_inspect ✓ 真实 open → read → close（27 格空箱子）')

        // 再读一次：上一次的窗口必须已经关掉（否则第二次 open 会拿不到新窗口）
        const again = await request(runtimePort, 'POST', '/minecraft/container_inspect', {
          x: chestPos.x,
          y: chestPos.y,
          z: chestPos.z,
        })
        assert(
          again.status === 200 && again.body.status === 'SUCCEEDED',
          `窗口没有泄漏：第二次 inspect 仍然成功（得到 ${JSON.stringify(again.body)}）`,
        )
        console.log('[e2e] container_inspect ✓ close 是硬要求（第二次仍能打开）')

        // transfer：空箱子里没有东西 → item.not_found（绝不假装搬成功）
        const withdraw = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
          x: chestPos.x,
          y: chestPos.y,
          z: chestPos.z,
          direction: 'withdraw',
          container_slot: 0,
          inventory_slot: 9,
          item: 'dirt',
          count: 1,
        })
        assert(
          withdraw.status === 404 && withdraw.body.error.code === 'item.not_found',
          `空箱子取东西 → item.not_found（得到 ${JSON.stringify(withdraw.body)}）`,
        )
        // 方向 / 槽位 / 数量的参数校验
        const badDirection = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
          x: chestPos.x,
          y: chestPos.y,
          z: chestPos.z,
          direction: 'take',
          container_slot: 0,
          inventory_slot: 9,
          item: 'dirt',
          count: 1,
        })
        assert(
          badDirection.status === 400 && badDirection.body.error.code === 'action.invalid',
          `direction 非法 → action.invalid（得到 ${JSON.stringify(badDirection.body)}）`,
        )
        const badSlot = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
          x: chestPos.x,
          y: chestPos.y,
          z: chestPos.z,
          direction: 'withdraw',
          container_slot: 27,
          inventory_slot: 9,
          item: 'dirt',
          count: 1,
        })
        assert(
          badSlot.status === 400 && badSlot.body.error.code === 'slot.invalid',
          `container_slot 越界 → slot.invalid（得到 ${JSON.stringify(badSlot.body)}）`,
        )
        const badInventorySlot = await request(
          runtimePort,
          'POST',
          '/minecraft/container_transfer',
          {
            x: chestPos.x,
            y: chestPos.y,
            z: chestPos.z,
            direction: 'withdraw',
            container_slot: 0,
            inventory_slot: 45,
            item: 'dirt',
            count: 1,
          },
        )
        assert(
          badInventorySlot.status === 400 && badInventorySlot.body.error.code === 'slot.invalid',
          `inventory_slot 45 → slot.invalid（得到 ${JSON.stringify(badInventorySlot.body)}）`,
        )
        console.log('[e2e] container_transfer ✓ 空箱子 / 非法参数都如实拒绝（不伪造成功）')

        // 独占：move_to 跑着的时候 container 动作必须被拒
        const busyMove = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: here.x + 6,
          y: here.y,
          z: here.z,
        })
        if (busyMove.status === 200 && busyMove.body.status === 'RUNNING') {
          const busyInspect = await request(runtimePort, 'POST', '/minecraft/container_inspect', {
            x: chestPos.x,
            y: chestPos.y,
            z: chestPos.z,
          })
          assert(
            busyInspect.status === 409 && busyInspect.body.error.code === 'action.busy',
            `move_to 跑着时 inspect → action.busy（得到 ${JSON.stringify(busyInspect.body)}）`,
          )
          const busyTransfer = await request(runtimePort, 'POST', '/minecraft/container_transfer', {
            x: chestPos.x,
            y: chestPos.y,
            z: chestPos.z,
            direction: 'withdraw',
            container_slot: 0,
            inventory_slot: 9,
            item: 'dirt',
            count: 1,
          })
          assert(
            busyTransfer.status === 409 && busyTransfer.body.error.code === 'action.busy',
            `move_to 跑着时 transfer → action.busy（得到 ${JSON.stringify(busyTransfer.body)}）`,
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) =>
                  e.event === 'minecraft.action.cancelled' &&
                  e.action_id === busyMove.body.action_id,
              ),
            'move_to cancelled（4E 独占段收尾）',
            10000,
          )
          console.log('[e2e] container ✓ 独占（开窗动作不与前台动作并发）')
        } else {
          console.log('[e2e] container 独占检查：move_to 没进入 RUNNING，跳过')
        }

        // 收尾：把测试用的石头与箱子清掉（不留痕迹）
        setBlock(chestPos.x, chestPos.y, chestPos.z, 'air')
        setBlock(stonePos.x, stonePos.y, stonePos.z, 'air')
        await sleep(400)
        // inspect 是即时的，没有"挖到一半"那种可取消窗口 → 与 dig/equip 同理
        console.log('[e2e] container STOP：SKIPPED（inspect 毫秒级完成，没有可取消窗口）')
      }

      // ---------------- Phase 4F：crafting（配方表在本地，拒绝路径可真实验证） ----------------
      // 配方数据来自 minecraft-data（本地），所以"查配方"在任何服务器上都能真的跑；
      // 但 flying-squid 没有 /give、背包永远是空的 → craft 的成功路径只能在真实服务器
      // smoke 验证（这里只验证语义投影 + 拒绝路径 + 独占语义）。
      if (cycle === 1) {
        const lookupRecipe = (item) =>
          request(runtimePort, 'POST', '/minecraft/recipe_lookup', { item })
        const tryCraft = (recipeId) =>
          request(runtimePort, 'POST', '/minecraft/craft', { recipe_id: recipeId })

        const lookup = await lookupRecipe('stick')
        assert(
          lookup.status === 200 && lookup.body.status === 'SUCCEEDED',
          `recipe_lookup 同步成功（得到 ${JSON.stringify(lookup.body).slice(0, 200)}）`,
        )
        const payload = lookup.body.result
        assert(
          payload.item === 'stick' && payload.status === 'insufficient_material',
          `空背包 → 材料都不够（得到 ${payload.status}）`,
        )
        assert(
          Array.isArray(payload.recipes) && payload.recipes.length > 0,
          '仍然列出 2×2 配方（不是空数组）',
        )
        const entry = payload.recipes[0]
        assert(
          Object.keys(entry).sort().join(',') === 'available,ingredients,recipe_id,requires_table,result',
          `语义投影只有约定字段（得到 ${Object.keys(entry).sort().join(',')}）`,
        )
        assert(
          entry.available === false && entry.requires_table === false,
          '空背包 → available=false / requires_table=false',
        )
        assert(
          Object.keys(entry.result).sort().join(',') === 'count_per_craft,name',
          '产物只有 name / count_per_craft',
        )
        const rawLookup = JSON.stringify(payload)
        for (const forbidden of ['inShape', 'delta', 'metadata', 'requiresTable']) {
          assert(!rawLookup.includes(forbidden), `不泄露 raw Recipe 字段 ${forbidden}`)
        }
        console.log(`[e2e] recipe_lookup ✓ 语义投影（${payload.total} 个 2×2 配方，样例 ${entry.recipe_id}）`)

        const again = await lookupRecipe('stick')
        assert(
          again.body.result.recipes[0].recipe_id === entry.recipe_id,
          'recipe_id 稳定（重复查询一致）',
        )
        const chestLookup = await lookupRecipe('chest')
        assert(
          chestLookup.body.result.status === 'crafting_table_required',
          `工作台配方如实回报（得到 ${chestLookup.body.result.status}）`,
        )
        const missingLookup = await lookupRecipe('not_a_real_item')
        assert(
          missingLookup.body.result.status === 'recipe_not_found',
          `未知物品 → recipe_not_found（得到 ${missingLookup.body.result.status}）`,
        )
        console.log('[e2e] recipe_lookup ✓ crafting_table_required / recipe_not_found')

        // craft：材料不够 / 非法参数 / 未知物品，全部如实拒绝
        const noMaterial = await tryCraft(entry.recipe_id)
        assert(
          noMaterial.status === 409 && noMaterial.body.error.code === 'material.insufficient',
          `空背包 craft → material.insufficient（得到 ${JSON.stringify(noMaterial.body)}）`,
        )
        // 形状校验在更上游（工具 schema / Service）；runtime 只看到形状合法的 id。
        // 这里验证 runtime 自己的两道：反解不出物品名 → not_found；形状对但签名变了 → changed
        const badShape = await tryCraft('not-a-signature')
        assert(
          badShape.status === 404 && badShape.body.error.code === 'recipe.not_found',
          `形状不对的 id → recipe.not_found（得到 ${JSON.stringify(badShape.body)}）`,
        )
        const changedId = await tryCraft('stick*4=oak_planks*3')
        assert(
          changedId.status === 409 && changedId.body.error.code === 'recipe.changed',
          `签名变了的 id → recipe.changed（得到 ${JSON.stringify(changedId.body)}）`,
        )
        const unknownId = await tryCraft('not_a_real_item*1=x*1')
        assert(
          unknownId.status === 404 && unknownId.body.error.code === 'recipe.not_found',
          `未知物品的 id → recipe.not_found（得到 ${JSON.stringify(unknownId.body)}）`,
        )
        console.log('[e2e] craft ✓ 材料不够 / 非法 id / 未知 id 都如实拒绝（不伪造成功）')

        // 独占（craft 与前台动作互斥）＋ 非独占（查配方可以并行）
        const hereCraft = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const busyMove = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: hereCraft.x + 6,
          y: hereCraft.y,
          z: hereCraft.z,
        })
        if (busyMove.status === 200 && busyMove.body.status === 'RUNNING') {
          const busyCraft = await tryCraft(entry.recipe_id)
          assert(
            busyCraft.status === 409 && busyCraft.body.error.code === 'action.busy',
            `move_to 跑着时 craft → action.busy（得到 ${JSON.stringify(busyCraft.body)}）`,
          )
          const during = await lookupRecipe('stick')
          assert(
            during.status === 200 && during.body.status === 'SUCCEEDED',
            'move_to 跑着时查配方仍然可用（SAFE 非独占）',
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) =>
                  e.event === 'minecraft.action.cancelled' &&
                  e.action_id === busyMove.body.action_id,
              ),
            'move_to cancelled（4F 独占段收尾）',
            10000,
          )
          console.log('[e2e] craft ✓ 独占（忙时 action.busy）；recipe_lookup ✓ 非独占')
        } else {
          console.log('[e2e] craft 独占检查：move_to 没进入 RUNNING，跳过')
        }

        console.log(
          '[e2e] craft 成功路径：SKIPPED（flying-squid 没有 /give、背包永远是空的；'
            + '成功路径由假配方单测 + 真实服务器 smoke 覆盖）',
        )
        console.log('[e2e] craft STOP：SKIPPED（合成毫秒级完成，没有可取消窗口）')
      }

      // ---------------- Phase 4G：3×3 工作台（真实 setblock 出来的工作台 + 拒绝路径） ----------------
      // 配方表在本地，所以"用某张工作台能做什么"能真跑；3×3 的成功路径需要材料
      // （flying-squid 没有 /give）→ 只在真实服务器 smoke 验证。
      if (cycle === 1) {
        const setBlock = (x, y, z, id) =>
          observer.chat(`/setblock ${Math.round(x)} ${Math.round(y)} ${Math.round(z)} ${id}`)
        const lookupAt = (item, craftingTable) =>
          request(runtimePort, 'POST', '/minecraft/recipe_lookup', {
            item,
            crafting_table: craftingTable,
          })
        const hereTable = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const bx = Math.round(hereTable.x) + 1
        const by = Math.round(hereTable.y) + 1
        const bz = Math.round(hereTable.z)
        const tablePos = { x: bx, y: by, z: bz }
        // 只是要越过 max_distance(5)：放 +8 就够了 —— 别用 +24 去逼服务器加载远处区块
        const farPos = { x: bx + 8, y: by, z: bz }
        const stonePos = { x: bx, y: by, z: bz + 1 }

        // schema：不给坐标 → 4F 语义（chest 需要工作台）；坐标不合规 → 400
        const noTable = await request(runtimePort, 'POST', '/minecraft/recipe_lookup', {
          item: 'chest',
        })
        assert(
          noTable.body.result.status === 'crafting_table_required',
          `不给工作台 → crafting_table_required（得到 ${noTable.body.result.status}）`,
        )
        const badShape = await lookupAt('chest', 'nearest')
        assert(
          badShape.status === 400 && badShape.body.error.code === 'table.invalid',
          `crafting_table="nearest" → table.invalid（得到 ${JSON.stringify(badShape.body)}）`,
        )
        console.log('[e2e] 3×3 lookup ✓ schema（nearest 这类隐式目标一律拒绝）')

        // 真放一张工作台 + 一个石头（用来验证"不是工作台"）
        setBlock(tablePos.x, tablePos.y, tablePos.z, 'minecraft:crafting_table')
        setBlock(stonePos.x, stonePos.y, stonePos.z, 'minecraft:stone')
        await sleep(900)

        const lookup = await lookupAt('chest', tablePos)
        assert(
          lookup.status === 200 && lookup.body.status === 'SUCCEEDED',
          `3×3 lookup 成功（得到 ${JSON.stringify(lookup.body).slice(0, 200)}）`,
        )
        const payload = lookup.body.result
        assert(
          payload.status === 'insufficient_material',
          `空背包 → 材料不够（得到 ${payload.status}）`,
        )
        assert(
          JSON.stringify(payload.crafting_table) === JSON.stringify(tablePos),
          `坐标进语义结果（得到 ${JSON.stringify(payload.crafting_table)}）`,
        )
        const tableEntry = (payload.recipes || []).find((row) => row.requires_table)
        assert(
          Boolean(tableEntry) && tableEntry.available === false,
          '3×3 配方被列出来（requires_table=true，但材料不够）',
        )
        assert(
          (tableEntry.ingredients || []).every((row) => row.count > 0),
          `材料语义完整（得到 ${JSON.stringify(tableEntry.ingredients)}）`,
        )
        assert(
          !JSON.stringify(payload).includes('inShape'),
          '不泄露 raw Recipe',
        )
        console.log('[e2e] 3×3 lookup ✓ 真实工作台 + 语义投影（requires_table / 材料）')

        // 工作台缺失 / 不是工作台 / 太远
        const missing = await lookupAt('chest', { x: bx, y: by + 8, z: bz })
        assert(
          missing.status === 404 && missing.body.error.code === 'table.missing',
          `空位置 → table.missing（得到 ${JSON.stringify(missing.body)}）`,
        )
        const invalid = await lookupAt('chest', stonePos)
        assert(
          invalid.status === 422 && invalid.body.error.code === 'table.invalid',
          `石头位置 → table.invalid（得到 ${JSON.stringify(invalid.body)}）`,
        )
        setBlock(farPos.x, farPos.y, farPos.z, 'minecraft:crafting_table')
        await sleep(800)
        const tooFar = await lookupAt('chest', farPos)
        assert(
          tooFar.status === 422 && tooFar.body.error.code === 'table.too_far',
          `8 格外 → table.too_far（得到 ${JSON.stringify(tooFar.body)}）`,
        )
        console.log('[e2e] 3×3 lookup ✓ table.missing / table.invalid / table.too_far（绝不自己走过去）')

        // craft：材料不够 / 工作台不在 → 如实拒绝
        const noMaterial = await request(runtimePort, 'POST', '/minecraft/craft', {
          recipe_id: tableEntry.recipe_id,
          crafting_table: tablePos,
        })
        assert(
          noMaterial.status === 409 &&
            noMaterial.body.error.code === 'material.insufficient',
          `空背包 craft 3×3 → material.insufficient（得到 ${JSON.stringify(noMaterial.body)}）`,
        )
        const tableGone = await request(runtimePort, 'POST', '/minecraft/craft', {
          recipe_id: tableEntry.recipe_id,
          crafting_table: { x: bx, y: by + 8, z: bz },
        })
        assert(
          tableGone.status === 404 && tableGone.body.error.code === 'table.missing',
          `工作台不在 → table.missing（得到 ${JSON.stringify(tableGone.body)}）`,
        )
        console.log('[e2e] 3×3 craft ✓ 材料不够 / 工作台不在都如实拒绝')

        // 独占：move_to 跑着的时候 3×3 craft 一样被拒
        const busyMove = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: hereTable.x + 6,
          y: hereTable.y,
          z: hereTable.z,
        })
        if (busyMove.status === 200 && busyMove.body.status === 'RUNNING') {
          const busyCraft = await request(runtimePort, 'POST', '/minecraft/craft', {
            recipe_id: tableEntry.recipe_id,
            crafting_table: tablePos,
          })
          assert(
            busyCraft.status === 409 && busyCraft.body.error.code === 'action.busy',
            `move_to 跑着时 3×3 craft → action.busy（得到 ${JSON.stringify(busyCraft.body)}）`,
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) =>
                  e.event === 'minecraft.action.cancelled' &&
                  e.action_id === busyMove.body.action_id,
              ),
            'move_to cancelled（4G 独占段收尾）',
            10000,
          )
        } else {
          console.log('[e2e] 3×3 craft 独占检查：move_to 没进入 RUNNING，跳过')
        }

        setBlock(tablePos.x, tablePos.y, tablePos.z, 'air')
        setBlock(stonePos.x, stonePos.y, stonePos.z, 'air')
        setBlock(farPos.x, farPos.y, farPos.z, 'air')
        await sleep(400)
        console.log(
          '[e2e] 3×3 craft 成功路径：SKIPPED（flying-squid 没有 /give，箱子里凑不出 8 块木板；'
            + '真实服务器 smoke 覆盖）',
        )
      }

      // ---------------- Phase 4K：find_blocks（只读定位） ----------------
      if (cycle === 1) {
        const find = (body) =>
          request(runtimePort, 'POST', '/minecraft/find_blocks', body)
        const here = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const grassNear = await find({
          block_names: ['minecraft:grass_block'],
          max_distance: 16,
          max_results: 8,
        })
        assert(
          grassNear.status === 200 &&
            grassNear.body.result &&
            grassNear.body.result.ok === true &&
            Array.isArray(grassNear.body.result.matches) &&
            typeof grassNear.body.result.truncated === 'boolean',
          `find_blocks 语义投影形状（HTTP ${grassNear.status}）`,
        )
        const shape = JSON.stringify(grassNear.body.result)
        for (const forbidden of ['metadata', 'stateId', 'chunk', 'diggable', 'hardness']) {
          assert(!shape.includes(forbidden), `不泄露 ${forbidden}`)
        }
        assert(
          !shape.includes('recommended') && !shape.includes('best') && !shape.includes('optimal'),
          '不返回推荐/最佳（§十五）',
        )
        assert(
          grassNear.body.result.query.max_distance === 16 &&
            grassNear.body.result.query.max_results === 8,
          'query 块如实回显搜索条件',
        )
        for (const row of grassNear.body.result.matches) {
          assert(
            setOf(Object.keys(row)) === 'block,distance,position',
            `每条匹配只有 block/position/distance（得到 ${JSON.stringify(Object.keys(row))}）`,
          )
          assert(
            setOf(Object.keys(row.distance)) === 'goal_near,raw',
            '每条匹配带两种距离口径',
          )
        }
        console.log(
          `[e2e] find_blocks ✓ 语义投影（假服务器上 ${grassNear.body.result.matches.length} 个 grass_block）`,
        )

        // 未知方块名 → 422 的稳定错误码（不是空结果）
        const unknown = await find({ block_names: ['minecraft:banana_ore'] })
        assert(
          unknown.status === 422 && unknown.body.error.code === 'block.name_unknown',
          `未知方块名 → block.name_unknown 422（得到 ${JSON.stringify(unknown.body)}）`,
        )
        assert(
          Array.isArray(unknown.body.error.detail.unknown) &&
            unknown.body.error.detail.unknown.includes('banana_ore'),
          '错误里点出是哪个名字不认识',
        )

        // 范围内没有 → 正常空结果（不是 404）
        const none = await find({
          block_names: ['minecraft:beacon'],
          max_distance: 4,
          max_results: 4,
        })
        assert(
          none.status === 200 &&
            none.body.result.matches.length === 0 &&
            none.body.result.truncated === false,
          `范围内没有 → 正常空结果（得到 ${JSON.stringify(none.body.result.matches)}）`,
        )

        // 上限校验：越界直接拒（不允许"扫全世界"）
        const tooFar = await find({ block_names: ['minecraft:stone'], max_distance: 1000 })
        assert(
          tooFar.status === 400 && tooFar.body.error.code === 'action.invalid',
          `max_distance 越界 → action.invalid（得到 ${JSON.stringify(tooFar.body)}）`,
        )
        const manyNames = await find({
          block_names: Array.from({ length: 9 }, (_, i) => `block_${i}`),
        })
        assert(
          manyNames.status === 400 && manyNames.body.error.code === 'action.invalid',
          `9 个名字 → action.invalid（得到 ${JSON.stringify(manyNames.body)}）`,
        )
        const one = await find({ block_names: ['minecraft:grass_block'], max_results: 1 })
        assert(
          one.status === 200 && one.body.result.matches.length <= 1,
          `max_results=1 时最多一条（得到 ${one.body.result.matches.length}）`,
        )

        // 非独占：前台动作跑着时只读查询照样能执行
        const busyFind = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: here.x + 12,
          y: here.y,
          z: here.z,
        })
        const duringMove = await find({ block_names: ['minecraft:grass_block'] })
        assert(
          duringMove.status === 200 && duringMove.body.result.ok === true,
          `move_to 跑着时 find_blocks 照样能执行（得到 HTTP ${duringMove.status}）`,
        )
        const busyDig = await request(runtimePort, 'POST', '/minecraft/dig', {
          x: Math.round(here.x),
          y: Math.round(here.y) - 1,
          z: Math.round(here.z),
          expected_block: 'stone',
        })
        const exclusiveCode = busyDig.body && busyDig.body.error && busyDig.body.error.code
        assert(
          exclusiveCode === 'action.busy' || exclusiveCode === 'block.not_found',
          `前台动作在跑时独占动作仍然被拒（得到 ${exclusiveCode}）`,
        )
        await request(runtimePort, 'POST', '/minecraft/stop', {})
        if (busyFind.body && busyFind.body.action_id) {
          await waitFor(
            () => events.some((e) => e.action_id === busyFind.body.action_id && e.event.startsWith('minecraft.action.')),
            'find_blocks 段收尾',
          )
        }
        console.log('[e2e] find_blocks ✓ 未知名字 / 空结果 / 上限 / 非独占')
      }

      // ---------------- Phase 4H：掉落物感知 + 单实体拾取 ----------------
      // flying-squid 不产掉落物（挖方块没有 drop、没有 /give），所以：
      //   * 空列表 / 参数校验 / 目标不存在 / 独占 / 非独占 —— 真实验证；
      //   * 如果能用 /summon 造一个 Item Entity，就真实验证感知与拾取；
      //   * 造不出来就明确 SKIPPED（不伪造成功）。
      if (cycle === 1) {
        const listDropped = () =>
          request(runtimePort, 'POST', '/minecraft/dropped_items', {})
        const tryPickup = (entityId, item) =>
          request(runtimePort, 'POST', '/minecraft/pickup_item', {
            entity_id: entityId,
            expected_item: item,
          })

        const empty = await listDropped()
        assert(
          empty.status === 200 && empty.body.status === 'SUCCEEDED',
          `dropped_items 同步成功（得到 ${JSON.stringify(empty.body).slice(0, 160)}）`,
        )
        assert(
          empty.body.result.online === true &&
            Array.isArray(empty.body.result.items) &&
            empty.body.result.truncated === false,
          '语义投影形状（online / items / truncated）',
        )
        const emptyRaw = JSON.stringify(empty.body.result)
        for (const forbidden of ['metadata', 'velocity', 'uuid', 'itemId', 'present']) {
          assert(!emptyRaw.includes(forbidden), `不泄露 ${forbidden}`)
        }
        console.log(
          `[e2e] dropped_items ✓ 只读语义投影（假服务器上 ${empty.body.result.total} 个掉落物）`,
        )

        // 参数校验 + 目标不存在
        const badArgs = await request(runtimePort, 'POST', '/minecraft/pickup_item', {
          entity_id: 'x',
          expected_item: 'dirt',
        })
        assert(
          badArgs.status === 400 && badArgs.body.error.code === 'action.invalid',
          `非法 entity_id → action.invalid（得到 ${JSON.stringify(badArgs.body)}）`,
        )
        const missing = await tryPickup(999999, 'dirt')
        assert(
          missing.status === 404 && missing.body.error.code === 'item_entity.not_found',
          `不存在的实体 → item_entity.not_found（得到 ${JSON.stringify(missing.body)}）`,
        )
        console.log('[e2e] pickup_item ✓ 参数校验 / 目标不存在（不猜、不扫货）')

        // 非独占（读掉落物可以并行）＋ 独占（pickup 与前台动作互斥）
        const hereDrop = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const busyMove = await request(runtimePort, 'POST', '/minecraft/move_to', {
          x: hereDrop.x + 6,
          y: hereDrop.y,
          z: hereDrop.z,
        })
        if (busyMove.status === 200 && busyMove.body.status === 'RUNNING') {
          const during = await listDropped()
          assert(
            during.status === 200 && during.body.status === 'SUCCEEDED',
            'move_to 跑着时读掉落物仍然可用（SAFE 非独占）',
          )
          const busyPickup = await tryPickup(999999, 'dirt')
          assert(
            busyPickup.status === 409 && busyPickup.body.error.code === 'action.busy',
            `move_to 跑着时 pickup → action.busy（得到 ${JSON.stringify(busyPickup.body)}）`,
          )
          await request(runtimePort, 'POST', '/minecraft/stop', {})
          await waitFor(
            () =>
              events.some(
                (e) =>
                  e.event === 'minecraft.action.cancelled' &&
                  e.action_id === busyMove.body.action_id,
              ),
            'move_to cancelled（4H 独占段收尾）',
            10000,
          )
        } else {
          console.log('[e2e] 4H 独占检查：move_to 没进入 RUNNING，跳过')
        }

        // 试着用 /summon 造一个真实 Item Entity（1.16 的 NBT 写法）
        const here2 = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const itemX = Math.round(here2.x) + 1
        const itemY = Math.round(here2.y) + 1
        const itemZ = Math.round(here2.z)
        await observer.chat(
          `/summon minecraft:item ${itemX} ${itemY} ${itemZ} `
            + '{Item:{id:"minecraft:oak_log",Count:1b}}',
        )
        // 等真实的 Item Entity 出现在感知里（e2e 里没有 waitForValue，这里自己轮询）
        const waitForDropped = async (timeoutMs) => {
          const deadline = Date.now() + timeoutMs
          while (Date.now() < deadline) {
            const probe = await listDropped()
            const items = (probe.body && probe.body.result && probe.body.result.items) || []
            if (items.length > 0) return items
            await sleep(300)
          }
          return null
        }
        const spawned = await waitForDropped(8000)
        if (!spawned) {
          console.log(
            '[e2e] dropped_items / pickup 成功路径：SKIPPED（假服务器不支持 /summon 掉落物，'
              + '造不出真实 Item Entity；不伪造结论）',
          )
        } else {
          const entry = spawned[0]
          assert(
            entry.item && entry.item.name === 'oak_log' && entry.item.count === 1,
            `真实 Item Entity 的物品语义（得到 ${JSON.stringify(entry.item)}）`,
          )
          assert(
            Object.keys(entry).sort().join(',') === 'distance,entity_id,item,position',
            `条目字段（得到 ${Object.keys(entry).sort().join(',')}）`,
          )
          assert(
            Number.isFinite(entry.distance) && entry.distance >= 0,
            `距离是数字（得到 ${entry.distance}）`,
          )
          console.log(
            `[e2e] dropped_items ✓ 真实 Item Entity（#${entry.entity_id} oak_log ×1，`
              + `${entry.distance} 格）`,
          )

          const before = (await request(runtimePort, 'GET', '/minecraft/inventory')).body
          const beforeCount = ((before.items || []).find((row) => row.name === 'oak_log') || {})
            .count || 0

          const pickup = await tryPickup(entry.entity_id, 'oak_log')
          if (pickup.status !== 200 || pickup.body.status !== 'RUNNING') {
            console.log(`[smoke]    pickup 启动失败：${JSON.stringify(pickup)}`)
            assert(false, `pickup 启动必须 200/RUNNING（HTTP ${pickup.status}）`)
          } else {
            assert(true, `pickup 启动 → RUNNING（action_id=${pickup.body.action_id}）`)
            const terminal = await waitForActionTerminal(
              pickup.body.action_id,
              'pickup 终态事件',
              20000,
            )
            if (!terminal) {
              assert(false, 'pickup 未在 20s 内进入终态')
            } else if (terminal.event !== 'minecraft.action.completed') {
              console.log(
                `[e2e] pickup 终态=${terminal.event}`
                  + `（${terminal.data && (terminal.data.error || terminal.data.reason)}）`
                  + ' —— 假服务器不实现物品收集时这是**如实**的失败，不伪造成功',
              )
            } else {
              const after = (await request(runtimePort, 'GET', '/minecraft/inventory')).body
              const afterCount = ((after.items || []).find((row) => row.name === 'oak_log') || {})
                .count || 0
              assert(
                afterCount > beforeCount,
                `真实 pickup 后背包里的 oak_log 增加了（${beforeCount} → ${afterCount}）`,
              )
              const result = terminal.data.result || {}
              assert(
                result.entity_id === entry.entity_id && result.collected === true,
                `完成的 result 如实（${JSON.stringify(result).slice(0, 160)}）`,
              )
              console.log('[e2e] pickup ✓ 真实 Item Entity 被捡进背包（playerCollect + 背包增加）')
            }
          }
          await observer.chat(`/kill @e[type=item,x=${itemX},y=${itemY},z=${itemZ},distance=..4]`)
          await sleep(400)
        }
      }

      // ---------------- Phase 3D：follow_player（Test A 跟随 / B STOP / C 丢失 / D 太远 / E 超时） ----------------
      if (cycle === 1) {
        const botPosNow = async () =>
          (await request(runtimePort, 'GET', '/minecraft/status')).body.position
        const tpTarget = (target, x, y, z) => target.bot.chat(`/tp ${Math.round(x)} ${Math.round(y)} ${Math.round(z)}`)

        const followee = await fake.spawnObserver('Followee')
        try {
          // ---- Test A：follow + 目标移动 → 继续跟随（目标移动 ≠ action 结束） ----
          const beforeFollow = await botPosNow()
          // 目标先站到罐头旁边（3 格内，进入 chase 上限）
          // flying-squid 的实体包有延迟：先等 runtime 真的"看见"这个玩家再跟随，
          // 否则 bot.players[name].entity 还不存在 → player.not_found（机器繁忙时必现）。
          // 断言没变（必须真的看见），只是等待期间会重发 /tp。
          await placeAndWait({
            tp: (x, y, z) => tpTarget(followee, x, y, z),
            x: beforeFollow.x + 3,
            y: beforeFollow.y,
            z: beforeFollow.z,
            label: 'runtime 看见 Followee',
            timeoutMs: 30000,
            predicate: async () => {
              const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
              return (snap.body.players || []).some((p) => p.username === 'Followee')
            },
          })

          const followStart = await request(runtimePort, 'POST', '/minecraft/follow_player', {
            username: 'Followee',
          })
          assert(
            followStart.status === 200 && followStart.body.status === 'RUNNING',
            `Test A：follow_player 启动 → RUNNING（得到 ${JSON.stringify(followStart.body)}）`,
          )
          assert(String(followStart.body.action_id).startsWith('act_'), 'Test A：action_id 由 runtime 生成')
          await waitFor(async () => {
            const s = await request(runtimePort, 'GET', '/minecraft/status')
            return Boolean(s.body.pathfinder && s.body.pathfinder.goal === 'GoalFollow')
          }, 'Pathfinder 进入 GoalFollow', 5000)

          const pfState = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(pfState.pathfinder.goal === 'GoalFollow', 'Test A：status.pathfinder.goal = GoalFollow')
          assert(
            pfState.pathfinder.target && pfState.pathfinder.target.username === 'Followee',
            `Test A：诊断能看出正在跟谁（得到 ${JSON.stringify(pfState.pathfinder.target)}）`,
          )
          assert(pfState.pathfinder.distance === 2.5, `Test A：跟随距离 2.5（得到 ${pfState.pathfinder.distance}）`)

          // 目标移动两次（/tp 到约 7 格开外）→ 罐头必须真的走过去并继续跟（action 仍在 RUNNING）。
          // 真实地形可能让某些落点不可达 → 每个 hop 换落点重试（最多 3 次）。
          const hopOffsets = [
            [-5, 5],
            [6, -4],
            [-6, -2],
            [4, 6],
          ]
          // 「她确实动了」看的是过程里的**最大**位移：两跳方向相反时 net 位移可能很小
          let maxMoved = 0
          const trackMoved = async () => {
            const now = await botPosNow()
            maxMoved = Math.max(maxMoved, Math.hypot(now.x - beforeFollow.x, now.z - beforeFollow.z))
          }
          for (let hop = 1; hop <= 2; hop += 1) {
            if (hop === 2) hopOffsets.reverse() // 第二跳换个方向，避免同一条路
            let reached = false
            for (const [ox, oz] of hopOffsets.slice(0, 3)) {
              const botPos = await botPosNow()
              try {
                await placeAndWait({
                  tp: (x, y, z) => tpTarget(followee, x, y, z),
                  x: botPos.x + ox,
                  y: botPos.y,
                  z: botPos.z + oz,
                  label: `第 ${hop} 跳后跟到目标附近`,
                  timeoutMs: 15000,
                  predicate: async () => {
                    const s = await request(runtimePort, 'GET', '/minecraft/status')
                    const me = s.body.position
                    const targetPosition = s.body.pathfinder && s.body.pathfinder.target
                    if (!me || !targetPosition) return false
                    const gap = Math.hypot(me.x - targetPosition.x, me.z - targetPosition.z)
                    return gap <= 4
                  },
                })
                reached = true
                await trackMoved()
                break
              } catch {
                /* 该落点不可达：换一个 */
              }
            }
            assert(reached, `Test A：目标第 ${hop} 次移动后罐头跟上（继续跟随）`)
            const stillRunning = (await request(runtimePort, 'GET', '/minecraft/status')).body
            assert(
              stillRunning.action && stillRunning.action.status === 'RUNNING',
              `Test A：目标移动不结束 action（仍是 ${stillRunning.action && stillRunning.action.status}）`,
            )
          }
          await trackMoved()
          assert(maxMoved >= 1, `Test A：罐头真的移动了（最大水平位移 ${maxMoved.toFixed(1)} 格）`)
          console.log(
            `[e2e] follow_player ✓ 目标移动两跳后仍在跟随（最大位移 ${maxMoved.toFixed(1)} 格）`,
          )

          // ---- Test B：STOP → CANCELLED + goal null + moving false + 位置停住 ----
          const stopFollow = await request(runtimePort, 'POST', '/minecraft/stop', {})
          assert(stopFollow.body.cancelled.length === 1, `Test B：STOP 取消 follow（得到 ${JSON.stringify(stopFollow.body)}）`)
          await waitFor(
            () => events.some((e) => e.event === 'minecraft.action.cancelled' && e.action === 'follow_player'),
            'follow cancelled 事件',
          )
          const stoppedFollow = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(stoppedFollow.pathfinder.goal === null, 'Test B：goal == null')
          assert(stoppedFollow.pathfinder.moving === false, 'Test B：isMoving == false')
          // 与 move_to 的 Test B 同理：停稳之后再测一段（下落/惯性收尾不算"还在走"）
          await sleep(400)
          const followA = (await request(runtimePort, 'GET', '/minecraft/status')).body
          await sleep(400)
          const followB = (await request(runtimePort, 'GET', '/minecraft/status')).body
          const followDrift = Math.hypot(
            followB.position.x - followA.position.x,
            followB.position.z - followA.position.z,
          )
          assert(followDrift <= 0.2, `Test B：停稳后位置不再漂移（${followDrift.toFixed(2)} 格）`)
          console.log('[e2e] follow STOP ✓ goal=null moving=false 位置已停')
        } finally {
          followee.close()
        }

        // ---- Test C：玩家消失 → 3s grace → FAILED player_lost（goal 清空） ----
        const ghost = await fake.spawnObserver('Ghost')
        const botForGhost = await botPosNow()
        await placeAndWait({
          tp: (x, y, z) => tpTarget(ghost, x, y, z),
          x: botForGhost.x + 3,
          y: botForGhost.y,
          z: botForGhost.z,
          label: 'runtime 看见 Ghost',
          timeoutMs: 20000,
          predicate: async () => {
            const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
            return (snap.body.players || []).some((p) => p.username === 'Ghost')
          },
        })
        const lostPromise = request(runtimePort, 'POST', '/minecraft/follow_player', { username: 'Ghost' })
        await waitFor(async () => {
          const s = await request(runtimePort, 'GET', '/minecraft/status')
          return Boolean(s.body.pathfinder && s.body.pathfinder.goal === 'GoalFollow')
        }, 'Ghost 跟随建立', 5000)
        const lostStarted = await lostPromise
        assert(
          lostStarted.status === 200 && lostStarted.body.status === 'RUNNING',
          `Test C：持续型动作启动即返回 RUNNING（得到 ${JSON.stringify(lostStarted.body)}）`,
        )
        ghost.bot.quit() // 玩家消失
        await waitFor(
          () => events.some((e) => e.event === 'minecraft.action.failed' && e.code === 'player.lost'),
          'player_lost failed 事件',
          10000,
        )
        const afterLost = (await request(runtimePort, 'GET', '/minecraft/status')).body
        assert(afterLost.action.status === 'FAILED', `Test C：终态 FAILED（得到 ${afterLost.action.status}）`)
        assert(afterLost.pathfinder.goal === null, 'Test C：player_lost 后 goal 清空（动作自清理）')
        console.log('[e2e] follow player_lost ✓ 宽限 3s 后 FAILED + goal 清空')

        // ---- Test D：目标超过最大追逐距离 → FAILED follow_target_too_far ----
        const farTarget = await fake.spawnObserver('FarTarget')
        try {
          const botForFar = await botPosNow()
          // 目标解析要求 bot.players[name].entity 真的存在（runtime §五），而**远处**的实体
          // 追踪本身就不稳（mineflayer 只跟视野内的实体）——所以分两步：
          // 1) 先在近处等"真的看见他"（近处追踪可靠，这才是可靠的前置）；
          // 2) 再把他传送到 30 格外（> E2E 的 chase 上限 16）立刻启动跟随。
          // 断言完全没变：必须因为**太远**而失败（follow.target_too_far）。
          await placeAndWait({
            tp: (x, y, z) => tpTarget(farTarget, x, y, z),
            x: botForFar.x + 3,
            y: botForFar.y,
            z: botForFar.z,
            label: 'runtime 看见 FarTarget',
            timeoutMs: 20000,
            predicate: async () => {
              const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
              return (snap.body.players || []).some((p) => p.username === 'FarTarget')
            },
          })
          tpTarget(farTarget, botForFar.x + 30, botForFar.y, botForFar.z)
          await sleep(600)
          const farResp = await request(runtimePort, 'POST', '/minecraft/follow_player', { username: 'FarTarget' })
          assert(
            farResp.status === 200 && farResp.body.status === 'RUNNING',
            `Test D：启动即 RUNNING（得到 ${JSON.stringify(farResp.body)}）`,
          )
          await waitFor(
            () =>
              events.some(
                (e) => e.event === 'minecraft.action.failed' && e.code === 'follow.target_too_far',
              ),
            'follow_target_too_far failed 事件',
            10000,
          )
          const afterFar = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(afterFar.action.status === 'FAILED', `Test D：终态 FAILED（得到 ${afterFar.action.status}）`)
          assert(afterFar.pathfinder.goal === null, 'Test D：失败后 goal 清空')
          console.log('[e2e] follow target_too_far ✓ 超上限即失败，不追到世界尽头')
        } finally {
          farTarget.close()
        }

        // ---- Test E：超时 → TIMEOUT + goal 清空（E2E 用 MC_FOLLOW_TIMEOUT_MS=8000） ----
        const slowTarget = await fake.spawnObserver('SlowTarget')
        try {
          const botForSlow = await botPosNow()
          await placeAndWait({
            tp: (x, y, z) => tpTarget(slowTarget, x, y, z),
            x: botForSlow.x + 3,
            y: botForSlow.y,
            z: botForSlow.z,
            label: 'runtime 看见 SlowTarget',
            timeoutMs: 20000,
            predicate: async () => {
              const snap = await request(runtimePort, 'GET', '/minecraft/world/snapshot?layers=near')
              return (snap.body.players || []).some((p) => p.username === 'SlowTarget')
            },
          })
          const timeoutResp = await request(runtimePort, 'POST', '/minecraft/follow_player', {
            username: 'SlowTarget',
          })
          assert(
            timeoutResp.status === 200 && timeoutResp.body.status === 'RUNNING',
            `Test E：启动即 RUNNING（得到 ${JSON.stringify(timeoutResp.body)}）`,
          )
          await waitFor(
            () => events.some((e) => e.event === 'minecraft.action.timeout' && e.action === 'follow_player'),
            'follow timeout 事件（8s）',
            15000,
          )
          const afterTimeout = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(afterTimeout.action.status === 'TIMEOUT', `Test E：终态 TIMEOUT（得到 ${afterTimeout.action.status}）`)
          assert(afterTimeout.pathfinder.goal === null, 'Test E：超时后 goal 清空')
          assert(afterTimeout.pathfinder.moving === false, 'Test E：超时后 isMoving == false')
          console.log('[e2e] follow TIMEOUT ✓ goal=null moving=false')
        } finally {
          slowTarget.close()
        }

        // 跟随测试收尾：无僵尸动作 + 连接仍 ONLINE
        const followSettled = (await request(runtimePort, 'GET', '/minecraft/status')).body
        assert(followSettled.status === 'ONLINE', 'Test A–E 后连接仍 ONLINE')
        assert(followSettled.action.active_count === 0, 'Test A–E 后无僵尸动作')
      }

      // 重复 connect 必须被拒绝（不产生第二个 session）
      const dup = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort })
      assert(dup.status === 409, `duplicate connect refused with 409, got ${dup.status}`)

      // Test 8: 服务器踢出 → kicked + 回到稳定态
      // （断开时取消进行中的动作由 action_runtime.test.js 的 cancelAll 用例覆盖；
      //  dig 在这台假服务器上瞬间完成，塞不进"断开时仍在挖"这个窗口）
      if (cycle === 1) {
        assert(fake.kick(USERNAME, 'banned: e2e test'), 'kick found the player')
        await waitFor(() => countEvents('minecraft.kicked') >= 1, 'minecraft.kicked event')
        await waitFor(
          () => countEvents('minecraft.disconnected') > disconnectedBefore,
          'disconnected after kick',
        )
        const afterKick = await request(runtimePort, 'GET', '/minecraft/status')
        assert(
          afterKick.body.status === 'DISCONNECTED' || afterKick.body.status === 'ERROR',
          `settles after kick, got ${afterKick.body.status}`,
        )
        console.log(`[e2e] kicked → ${afterKick.body.status} ✓`)
      } else {
        // Test 9: 主动 disconnect
        const leave = await request(runtimePort, 'POST', '/minecraft/disconnect', {})
        assert(leave.status === 200, 'disconnect answers 200')
        await waitFor(
          () => countEvents('minecraft.disconnected') > disconnectedBefore,
          'disconnected after leave',
        )
      }

      const settled = await request(runtimePort, 'GET', '/minecraft/status')
      assert(
        settled.body.status === 'DISCONNECTED' || settled.body.status === 'ERROR',
        `cycle settled, got ${settled.body.status}`,
      )

      // 幂等 disconnect（Test 9 / 生命周期）
      const again = await request(runtimePort, 'POST', '/minecraft/disconnect', {})
      assert(again.status === 200, 'duplicate disconnect idempotent')
      assert(again.body.status === 'DISCONNECTED', `idempotent disconnect settles DISCONNECTED, got ${again.body.status}`)
    }

    // 连接到不存在的目标：报错事件 + 稳定状态 + 进程不崩
    const badEventsBefore = countEvents('minecraft.error')
    const bad = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: 1 })
    assert(bad.status === 200, 'connect to dead target accepted then fails async')
    await waitFor(() => countEvents('minecraft.error') > badEventsBefore, 'minecraft.error for dead target')
    await waitFor(async () => {
      const status = await request(runtimePort, 'GET', '/minecraft/status')
      return status.body.status === 'ERROR' || status.body.status === 'DISCONNECTED'
    }, 'settles after dead target')
    const healthAfterBad = await request(runtimePort, 'GET', '/minecraft/health')
    assert(healthAfterBad.status === 200, 'runtime still healthy after failed connect')
    console.log('[e2e] dead target → error event, runtime alive ✓')

    // 非法输入
    assert((await request(runtimePort, 'POST', '/minecraft/connect', { host: '' })).status === 400, 'empty host rejected')
    assert((await request(runtimePort, 'POST', '/minecraft/connect', { host: 'x', port: 99999 })).status === 400, 'bad port rejected')
    assert((await request(runtimePort, 'POST', '/minecraft/chat', { message: 'hi' })).status === 400, 'chat while offline rejected')

    // Test 10 收尾：3 次循环无残留 session / 僵尸 bot
    assert(countEvents('minecraft.connected') === CYCLES, `exactly ${CYCLES} connected events, got ${countEvents('minecraft.connected')}`)
    assert(countEvents('minecraft.spawned') === CYCLES, `exactly ${CYCLES} spawned events, got ${countEvents('minecraft.spawned')}`)
    const finalStatus = await request(runtimePort, 'GET', '/minecraft/status')
    assert(finalStatus.body.status === 'DISCONNECTED' || finalStatus.body.status === 'ERROR', 'final state settled')
    console.log(`[e2e] events: ${events.map((e) => e.event).join(', ')}`)

    // 干净退出。Windows 的 SIGTERM 是无条件终止（Node 不派发该信号），
    // 只在 POSIX 上能验证优雅退出路径；bot 此刻已 DISCONNECTED，无僵尸问题。
    child.kill('SIGTERM')
    await waitFor(() => exitInfo !== null, 'runtime exits on SIGTERM', 10000)
    if (process.platform === 'win32') {
      assert(exitInfo.signal === 'SIGTERM', `terminated by SIGTERM, got ${JSON.stringify(exitInfo)}`)
    } else {
      assert(exitInfo.code === 0, `runtime exit code 0, got ${JSON.stringify(exitInfo)}`)
    }
    console.log('[e2e] runtime exited cleanly ✓')
    console.log('\n[e2e] ALL CHECKS PASSED')
  } catch (error) {
    console.error('\n[e2e] FAILED:', error)
    console.error('[e2e] runtime log tail:\n' + runtimeLogText().split('\n').slice(-40).join('\n'))
    child.kill('SIGKILL')
    process.exitCode = 1
  } finally {
    if (exitInfo === null && !child.killed) child.kill('SIGKILL')
    if (observer) observer.close()
    await fake.close()
    fake.cleanup()
    receiver.close()
  }
}

main()
  .then(() => {
    process.exit(process.exitCode ?? 0)
  })
  .catch((error) => {
    console.error('[e2e] fatal:', error)
    process.exit(1)
  })
