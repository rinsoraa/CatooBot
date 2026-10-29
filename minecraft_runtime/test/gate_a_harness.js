'use strict'
/** Isolated Node runtime + fake Minecraft server harness for the Gate A E2 test. */
const { spawn } = require('child_process')
const http = require('http')
const net = require('net')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { createFakeServer } = require('./fake_server')

const RUNTIME = path.join(__dirname, '..', 'runtime.js')
const TOKEN = 'gate-a-token'
const USERNAME = 'GuanTou'
let controllerToken = ''
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function request(port, method, urlPath, body, token = TOKEN) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body)
    const req = http.request({ host: '127.0.0.1', port, method, path: urlPath,
      headers: { 'Content-Type': 'application/json', ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}), ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(controllerToken ? { 'X-Catoo-Controller-Token': controllerToken } : {}) } }, (res) => {
      let raw = ''; res.on('data', (d) => (raw += d)); res.on('end', () => {
        let parsed; try { parsed = raw ? JSON.parse(raw) : null } catch { parsed = raw }
        resolve({ status: res.statusCode, body: parsed })
      })
    })
    req.on('error', reject); if (payload) req.write(payload); req.end()
  })
}

async function waitForRuntime(port) {
  for (let i = 0; i < 240; i += 1) {
    try { const r = await request(port, 'GET', '/minecraft/health'); if (r.status === 200 && r.body && r.body.ok) return } catch {}
    await sleep(250)
  }
  throw new Error('runtime not healthy')
}
async function waitFor(fn, label) {
  for (let i = 0; i < 240; i += 1) { if (await fn()) return; await sleep(150) }
  throw new Error('timeout: ' + label)
}

async function main() {
  const cfg = JSON.parse(process.argv[2])
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-gate-a-'))
  const authFile = path.join(workDir, 'auth.json')
  fs.writeFileSync(authFile, JSON.stringify({ mode: 'offline', username: USERNAME }), 'utf8')
  const fake = createFakeServer({ port: cfg.serverPort })
  const observers = new Map()
  const control = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1')
    if (url.searchParams.get('token') !== TOKEN) {
      res.writeHead(401, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: false, code: 'unauthorized' }))
      return
    }
    const name = url.searchParams.get('name') || ''
    try {
      if (url.pathname === '/observer/spawn') {
        if (!name || observers.has(name)) {
          res.writeHead(409, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ ok: false, code: 'already_exists' }))
          return
        }
        const observer = await fake.spawnObserver(name)
        observers.set(name, observer)
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ ok: true, name, state: 'spawned' }))
        return
      }
      if (url.pathname === '/observer/close') {
        const observer = observers.get(name)
        if (!observer) {
          res.writeHead(404, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ ok: false, code: 'not_found' }))
          return
        }
        observer.close()
        await waitFor(() => fake.playerByName(name) === null, 'observer ' + name + ' closed')
        observers.delete(name)
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ ok: true, name, state: 'closed' }))
        return
      }
      if (url.pathname === '/observer/move_near') {
        const player = fake.playerByName(name)
        const anchorName = url.searchParams.get('anchor') || USERNAME
        const anchor = fake.playerByName(anchorName)
        if (!player || !anchor) {
          res.writeHead(404, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ ok: false, code: 'not_found' }))
          return
        }
        const x = anchor.position.x + 1
        const y = anchor.position.y
        const z = anchor.position.z
        const steps = {}
        // (1) Server-authoritative move: only this can change what OTHER clients
        // (the bot) see. A position packet written from the observer only updates
        // the observer's own client state.
        steps.server_move = fake.movePlayerServerSide(name, x, y, z)
        // (2) Keep the fake client's own view consistent with the server.
        const observer = observers.get(name)
        try {
          observer.bot.entity.position.set(x, y, z)
          observer.bot.entity.velocity.set(0, 0, 0)
          observer.bot._client.write('position', { x, y, z, onGround: true })
          steps.client_packet = true
        } catch (error) {
          steps.client_packet = false
          steps.client_error = String(error)
        }
        // (3) Optional rejoin: a fresh join makes the server broadcast the entity to
        // every connected client, which is the only reliable cure for "the bot never
        // received this player's entity spawn packet" (flying-squid does not replay
        // already-joined players to a newly connected client).
        if (url.searchParams.get('rejoin') === '1') {
          try {
            observer.close()
            observers.delete(name)
            await waitFor(() => fake.playerByName(name) === null, 'observer ' + name + ' gone')
            const fresh = await fake.spawnObserver(name)
            observers.set(name, fresh)
            steps.rejoined = true
          } catch (error) {
            steps.rejoined = false
            steps.rejoin_error = String(error)
          }
        }
        const anchorNow = fake.playerByName(anchorName)
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(
          JSON.stringify({
            ok: true,
            name,
            x,
            y,
            z,
            anchor: anchorName,
            anchor_position: anchorNow
              ? { x: anchorNow.position.x, y: anchorNow.position.y, z: anchorNow.position.z }
              : null,
            server_players: fake.playerNames(),
            observers: [...observers.keys()],
            ...steps,
          }),
        )
        return
      }
      if (url.pathname === '/observer/state') {
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({
          ok: true,
          name,
          present: fake.playerByName(name) !== null,
          managed: observers.has(name),
        }))
        return
      }
      res.writeHead(404, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: false, code: 'not_found' }))
    } catch (err) {
      res.writeHead(500, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: false, code: 'error', message: String(err) }))
    }
  })
  const child = spawn(process.execPath, [RUNTIME], { env: { ...process.env,
    MC_RUNTIME_PORT: String(cfg.runtimePort),
    MC_CALLBACK_URL: cfg.callbackUrl,
    MC_CALLBACK_TOKEN: 'cb',
    MC_BRIDGE_TOKEN: TOKEN,
    MC_AUTH_FILE: authFile,
    MC_CONNECT_TIMEOUT: '30',
    MC_FOLLOW_TIMEOUT_MS: '15000' }, stdio: ['ignore','pipe','pipe'] })
  const logs = []
  child.stdout.on('data', (d) => { logs.push(d.toString()); process.stderr.write('[child] ' + d.toString()) })
  child.stderr.on('data', (d) => { logs.push(d.toString()); process.stderr.write('[child-err] ' + d.toString()) })
  const cleanup = () => {
    for (const observer of observers.values()) {
      try { observer.close() } catch {}
    }
    try { control.close() } catch {}
    try { child.kill('SIGTERM') } catch {}
    try { fake.close ? fake.close() : null } catch {}
  }
  process.on('SIGTERM', () => { cleanup(); process.exit(0) })
  process.on('SIGINT', () => { cleanup(); process.exit(0) })
  try {
    await waitForRuntime(cfg.runtimePort)
    const controllerId = cfg.controllerId || 'gate-a-harness'
    const claim = await request(cfg.runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: controllerId })
    if (claim.status !== 200 || !claim.body.owner_token) throw new Error('harness claim failed: ' + JSON.stringify(claim))
    controllerToken = claim.body.owner_token
    const join = await request(cfg.runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: cfg.serverPort })
    if (join.status !== 200) throw new Error('connect failed: ' + JSON.stringify(join))
    await waitFor(() => fake.playerByName(USERNAME) !== null, 'bot joined')
    await waitFor(async () => (await request(cfg.runtimePort, 'GET', '/minecraft/status')).body.status === 'ONLINE', 'ONLINE')
    if (!cfg.sharedController) {
      const release = await request(cfg.runtimePort, 'POST', '/minecraft/controller/release', {
        controller_id: controllerId, owner_epoch: claim.body.owner_epoch, owner_token: controllerToken,
      })
      if (release.status !== 200) throw new Error('harness release failed: ' + JSON.stringify(release))
      controllerToken = ''
    }
    const tester = await fake.spawnObserver('Tester')
    observers.set('Tester', tester)
    await new Promise((resolve) => control.listen(0, '127.0.0.1', resolve))
    await sleep(600)
    process.stdout.write('READY ' + JSON.stringify({
      runtimePort: cfg.runtimePort,
      serverPort: cfg.serverPort,
      controlPort: control.address().port,
    }) + '\n')
    await new Promise(() => {})
  } catch (err) {
    process.stderr.write('HARNESS_FAIL ' + (err && err.stack ? err.stack : String(err)) + '\n')
    process.stderr.write(logs.join(''))
    cleanup()
    process.exit(1)
  }
}
main()
