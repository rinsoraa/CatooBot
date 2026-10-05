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

// ------------------------------------------------------------------ main

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
      MC_MOVE_TIMEOUT_MS: '2500', // Test C 依赖：可达的 12–16 格约需 3s+ → 确定性超时
      MC_FOLLOW_TIMEOUT_MS: '8000', // Test C 的 3s 宽限必须在超时之前完成；Test E 等 8s
      MC_FOLLOW_MAX_CHASE_DISTANCE: '16', // Test D 用 30 格验证「超上限即失败」
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
          if (resp.status === 200 && resp.body.status === 'SUCCEEDED') {
            moveOk = resp.body
            moveDir = [dx, dz]
            break
          }
          moveFail = resp
        }
        assert(moveOk, `Test A：至少一个方向的 move_to 成功（最后失败：${JSON.stringify(moveFail && moveFail.body)}）`)
        assert(moveOk.result && typeof moveOk.result.distance_to_target === 'number', 'Test A：result 带 distance_to_target')
        assert(moveOk.result.distance_to_target <= 2.2, `Test A：到达目标附近（${moveOk.result.distance_to_target} 格）`)
        await waitFor(
          () =>
            events.some(
              (e) =>
                e.event === 'minecraft.action.completed' &&
                e.action === 'move_to' &&
                e.action_id === moveOk.action_id,
            ),
          'move_to completed 事件',
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
          const movePromise = request(runtimePort, 'POST', '/minecraft/move_to', target)
          let started = false
          try {
            await waitFor(async () => {
              const snap = await request(runtimePort, 'GET', '/minecraft/status')
              return Boolean(snap.body.pathfinder && snap.body.pathfinder.moving)
            }, '导航开始', 4000)
            started = true
          } catch {
            await movePromise.catch(() => {}) // 该方向没能开始移动（no-path 等）：换个比例重试
          }
          if (!started) continue

          const stopResult = await request(runtimePort, 'POST', '/minecraft/stop', {})
          assert(
            stopResult.body.cancelled.length === 1,
            `Test B：stop 取消 move_to（得到 ${JSON.stringify(stopResult.body)}）`,
          )
          const moveResp = await movePromise
          assert(moveResp.body.status === 'CANCELLED', `Test B：move_to → CANCELLED（得到 ${moveResp.body.status}）`)
          await waitFor(
            () => events.some((e) => e.event === 'minecraft.action.cancelled' && e.action === 'move_to'),
            'move_to cancelled 事件',
          )
          // §十一：不能只验证 Action 状态——还要验证 goal 清空、isMoving=false、位置停住
          const stopped = (await request(runtimePort, 'GET', '/minecraft/status')).body
          assert(stopped.pathfinder.goal === null, 'Test B：goal == null')
          assert(stopped.pathfinder.moving === false, 'Test B：isMoving == false')
          await sleep(400)
          const later = (await request(runtimePort, 'GET', '/minecraft/status')).body
          const drift = Math.hypot(
            later.position.x - stopped.position.x,
            later.position.z - stopped.position.z,
          )
          assert(drift <= 0.3, `Test B：停止后位置不再漂移（${drift.toFixed(2)} 格）`)
          stopVerified = true
          break
        }
        assert(stopVerified, 'Test B：移动中 STOP 验证完成（goal 清空 + isMoving=false + 位置停住）')
        console.log('[e2e] move_to STOP ✓ goal=null moving=false 位置已停')

        // ---- Test C：move_to 超时 → TIMEOUT + goal 清空（§十三） ----
        let timeoutVerified = false
        let timeoutFail = null
        for (const distance of [16, 12, 20, 10, 14, 24]) {
          const timeoutOrigin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
          const target = {
            x: timeoutOrigin.x + ux * distance,
            y: timeoutOrigin.y,
            z: timeoutOrigin.z + uz * distance,
          }
          const resp = await request(runtimePort, 'POST', '/minecraft/move_to', target)
          timeoutFail = resp
          if (resp.status === 200 && resp.body.status === 'TIMEOUT') {
            await waitFor(
              () => events.some((e) => e.event === 'minecraft.action.timeout' && e.action === 'move_to'),
              'move_to timeout 事件',
            )
            const after = (await request(runtimePort, 'GET', '/minecraft/status')).body
            assert(after.pathfinder.goal === null, 'Test C：超时后 goal == null')
            assert(after.pathfinder.moving === false, 'Test C：超时后 isMoving == false')
            timeoutVerified = true
            break
          }
          // no-path 等非 TIMEOUT 结果：换个更远的比例重试
        }
        assert(
          timeoutVerified,
          `Test C：move_to 超时 → TIMEOUT 且 goal 清空（最后一次响应：${JSON.stringify(timeoutFail && timeoutFail.body)}）`,
        )
        console.log('[e2e] move_to TIMEOUT ✓ goal=null moving=false')

        // 移动测试收尾：无僵尸动作、连接仍 ONLINE
        const moveSettled = (await request(runtimePort, 'GET', '/minecraft/status')).body
        assert(moveSettled.status === 'ONLINE', 'Test A/B/C 后连接仍 ONLINE')
        assert(moveSettled.action.active_count === 0, 'Test A/B/C 后无僵尸动作')
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
          tpTarget(followee, beforeFollow.x + 3, beforeFollow.y, beforeFollow.z)

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
          for (let hop = 1; hop <= 2; hop += 1) {
            if (hop === 2) hopOffsets.reverse() // 第二跳换个方向，避免同一条路
            let reached = false
            for (const [ox, oz] of hopOffsets.slice(0, 3)) {
              const botPos = await botPosNow()
              tpTarget(followee, botPos.x + ox, botPos.y, botPos.z + oz)
              try {
                await waitFor(async () => {
                  const s = await request(runtimePort, 'GET', '/minecraft/status')
                  const me = s.body.position
                  const targetPosition = s.body.pathfinder && s.body.pathfinder.target
                  if (!me || !targetPosition) return false
                  const gap = Math.hypot(me.x - targetPosition.x, me.z - targetPosition.z)
                  return gap <= 4
                }, `第 ${hop} 跳后跟到目标附近`, 9000)
                reached = true
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
          const afterFollow = await botPosNow()
          const followMoved = Math.hypot(afterFollow.x - beforeFollow.x, afterFollow.z - beforeFollow.z)
          assert(followMoved >= 1, `Test A：罐头真的移动了（水平位移 ${followMoved.toFixed(1)} 格）`)
          console.log(`[e2e] follow_player ✓ 目标移动两跳后仍在跟随（位移 ${followMoved.toFixed(1)} 格）`)

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
          await sleep(400)
          const laterFollow = (await request(runtimePort, 'GET', '/minecraft/status')).body
          const followDrift = Math.hypot(
            laterFollow.position.x - stoppedFollow.position.x,
            laterFollow.position.z - stoppedFollow.position.z,
          )
          assert(followDrift <= 0.3, `Test B：停止后位置不再漂移（${followDrift.toFixed(2)} 格）`)
          console.log('[e2e] follow STOP ✓ goal=null moving=false 位置已停')
        } finally {
          followee.close()
        }

        // ---- Test C：玩家消失 → 3s grace → FAILED player_lost（goal 清空） ----
        const ghost = await fake.spawnObserver('Ghost')
        const botForGhost = await botPosNow()
        tpTarget(ghost, botForGhost.x + 3, botForGhost.y, botForGhost.z)
        await sleep(500)
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
          tpTarget(farTarget, botForFar.x + 30, botForFar.y, botForFar.z) // 30 > E2E 的 chase 上限 16
          await sleep(500)
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
          tpTarget(slowTarget, botForSlow.x + 3, botForSlow.y, botForSlow.z)
          await sleep(500)
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
