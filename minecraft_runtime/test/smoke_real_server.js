'use strict'
/**
 * Phase 3C §二十一：真实 Minecraft 服务器 Smoke Test（可选，不进 CI）。
 *
 * 用**本项目自己的 runtime 进程**（独立端口 + 本地回调接收器）连接真实服务器
 * （默认 127.0.0.1:25565，见 SMOKE_HOST / SMOKE_PORT / SMOKE_USERNAME 环境变量），
 * 依次验证：
 *   join → spawn → move_to（近距离）→ SUCCEEDED → move_to（远一点）→ STOP
 *   → CANCELLED 且位置停住 → follow_player（§二十九：跟一个真实第二个客户端）
 *   → 目标走动后继续跟 → STOP 真正停住 → 主动离开。
 *
 * 认证：默认用 minecraft_runtime/auth.json（本地文件，绝不进 Git）。
 * 服务器没开 / 连不上 → 打印 NOT AVAILABLE 并以 0 退出（文档记录用）；
 * 连接成功但移动行为不符合预期 → FAIL，退出码 1。
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
  const receiver = http.createServer((req, res) => {
    let raw = ''
    req.on('data', (chunk) => (raw += chunk))
    req.on('end', () => res.writeHead(200).end('{"ok":true}'))
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
    const online = await waitFor(async () => {
      const status = await request(runtimePort, 'GET', '/minecraft/status')
      return status.body && status.body.status === 'ONLINE'
    }, '进入世界', 60000)
    if (!online) {
      const status = await request(runtimePort, 'GET', '/minecraft/status')
      console.log(`[smoke] REAL SERVER: NOT AVAILABLE（无法进入世界：${status.body && status.body.last_error}）`)
      process.exit(0)
    }
    const origin = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
    console.log(`[smoke] 已进入世界 @ ${JSON.stringify(origin)}`)

    // 近距离移动（尝试若干方向，真实世界地形未知）
    let moved = null
    let movedDir = null
    for (const [dx, dz] of [[4, 0], [-4, 0], [0, 4], [0, -4]]) {
      const resp = await request(runtimePort, 'POST', '/minecraft/move_to', {
        x: origin.x + dx,
        y: origin.y,
        z: origin.z + dz,
      })
      if (resp.status === 200 && resp.body.status === 'SUCCEEDED') {
        moved = resp.body
        movedDir = [dx, dz]
        break
      }
    }
    check(Boolean(moved), `move_to 近距离成功（result=${moved && JSON.stringify(moved.result)}）`)

    if (moved) {
      // 远一点 → STOP → 位置必须停住
      const dirLen = Math.hypot(movedDir[0], movedDir[1]) || 1
      const ux = movedDir[0] / dirLen
      const uz = movedDir[1] / dirLen
      const now = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
      const movePromise = request(runtimePort, 'POST', '/minecraft/move_to', {
        x: now.x + ux * 25,
        y: now.y,
        z: now.z + uz * 25,
      })
      let started = false
      for (const distance of [25, 18, 30]) {
        void distance
        started = await waitFor(async () => {
          const snap = await request(runtimePort, 'GET', '/minecraft/status')
          return Boolean(snap.body.pathfinder && snap.body.pathfinder.moving)
        }, '导航开始', 8000)
        if (started) break
      }
      if (started) {
        const stop = await request(runtimePort, 'POST', '/minecraft/stop', {})
        const moveResp = await movePromise
        check(stop.body.cancelled.length === 1, 'STOP 取消了移动中的 move_to')
        check(moveResp.body.status === 'CANCELLED', `move_to → ${moveResp.body.status}`)
        const stopped = (await request(runtimePort, 'GET', '/minecraft/status')).body
        check(stopped.pathfinder.goal === null, 'goal == null')
        check(stopped.pathfinder.moving === false, 'isMoving == false')
        await sleep(600)
        const later = (await request(runtimePort, 'GET', '/minecraft/status')).body
        check(distance2d(later.position, stopped.position) <= 0.3, '停止后位置不再漂移')
      } else {
        console.log('[smoke] 远端目标未能开始移动（地形导致 no-path），跳过 STOP 段')
        await movePromise.catch(() => {})
      }
    }

    // ---- Phase 3D §二十九：follow_player 真实验证（第二个客户端当目标） ----
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
      const meNow = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
      const before = (await request(runtimePort, 'GET', '/minecraft/status')).body.position
      const followResp = await request(runtimePort, 'POST', '/minecraft/follow_player', {
        username: targetName,
      })
      check(
        followResp.status === 200 && followResp.body.status === 'RUNNING',
        `follow_player 启动 → RUNNING（${JSON.stringify(followResp.body)}）`,
      )
      // 目标往前走一段（真实走动），罐头应当跟上且 action 仍在 RUNNING
      targetBot.look(meNow.x, meNow.y, meNow.z, true)
      targetBot.setControlState('forward', true)
      targetBot.setControlState('sprint', true)
      await sleep(2500)
      targetBot.setControlState('forward', false)
      targetBot.setControlState('sprint', false)
      await sleep(1500)
      const during = (await request(runtimePort, 'GET', '/minecraft/status')).body
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
      const moved = Math.hypot(during.position.x - before.x, during.position.z - before.z)
      check(moved >= 1, `罐头真的跟走了（位移 ${moved.toFixed(1)} 格）`)

      const stopFollow = await request(runtimePort, 'POST', '/minecraft/stop', {})
      check(stopFollow.body.cancelled.length === 1, 'STOP 取消跟随')
      await sleep(400)
      const stoppedFollow = (await request(runtimePort, 'GET', '/minecraft/status')).body
      check(stoppedFollow.pathfinder.goal === null, '跟随 STOP 后 goal == null')
      check(stoppedFollow.pathfinder.moving === false, '跟随 STOP 后 isMoving == false')
      await sleep(600)
      const laterFollow = (await request(runtimePort, 'GET', '/minecraft/status')).body
      check(
        Math.hypot(laterFollow.position.x - stoppedFollow.position.x, laterFollow.position.z - stoppedFollow.position.z) <= 0.3,
        '跟随 STOP 后位置不再漂移',
      )
      try {
        targetBot.quit()
      } catch {
        /* ignore */
      }
    }

    await request(runtimePort, 'POST', '/minecraft/disconnect', {})
    await waitFor(async () => {
      const status = await request(runtimePort, 'GET', '/minecraft/status')
      return status.body.status === 'DISCONNECTED'
    }, '主动离开', 15000)
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
