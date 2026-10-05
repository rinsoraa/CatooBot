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
