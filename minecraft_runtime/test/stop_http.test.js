'use strict'
/** Real runtime HTTP /minecraft/stop route tests. */
const { spawn } = require('child_process')
const http = require('http')
const net = require('net')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { createFakeServer } = require('./fake_server')

const RUNTIME = path.join(__dirname, '..', 'runtime.js')
const TOKEN = 'stop-http-token'
const USERNAME = 'GuanTou'
let controllerToken = ''
let checks = 0
let failures = 0
function assert(condition, message) {
  checks += 1
  if (!condition) { failures += 1; console.error('  x ' + message) } else { console.log('  ok ' + message) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer(); s.unref()
    s.on('error', reject); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)) })
  })
}
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
  for (let i = 0; i < 120; i += 1) {
    try { const r = await request(port, 'GET', '/minecraft/health'); if (r.status === 200 && r.body && r.body.ok) return } catch {}
    await sleep(250)
  }
  throw new Error('runtime not healthy')
}
async function waitFor(fn, label) {
  for (let i = 0; i < 200; i += 1) { if (await fn()) return; await sleep(150) }
  throw new Error('timeout: ' + label)
}

async function main() {
  const runtimePort = await freePort()
  const serverPort = await freePort()
  const callbackPort = await freePort()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-stop-http-'))
  const authFile = path.join(workDir, 'auth.json')
  fs.writeFileSync(authFile, JSON.stringify({ mode: 'offline', username: USERNAME }), 'utf8')
  const events = []
  const receiver = http.createServer((req, res) => { let raw=''; req.on('data',(d)=>raw+=d); req.on('end',()=>{try { events.push(JSON.parse(raw)) } catch {} res.writeHead(200).end('{"ok":true}')}) })
  await new Promise((r) => receiver.listen(callbackPort, '127.0.0.1', r))
  const fake = createFakeServer({ port: serverPort })
  const child = spawn(process.execPath, [RUNTIME], { env: { ...process.env, MC_RUNTIME_PORT: String(runtimePort), MC_CALLBACK_URL: 'http://127.0.0.1:' + callbackPort + '/events', MC_CALLBACK_TOKEN: 'cb', MC_BRIDGE_TOKEN: TOKEN, MC_AUTH_FILE: authFile, MC_CONNECT_TIMEOUT: '30', MC_FOLLOW_TIMEOUT_MS: '8000' }, stdio: ['ignore','pipe','pipe'] })
  const logs = []; child.stdout.on('data',(d)=>logs.push(d.toString())); child.stderr.on('data',(d)=>logs.push(d.toString()))
  try {
    await waitForRuntime(runtimePort)
    const claim = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'stop-http-test' })
    assert(claim.status === 200 && claim.body.owner_token, 'claim controller')
    controllerToken = claim.body.owner_token
    const join = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort })
    assert(join.status === 200, 'connect 200')
    await waitFor(() => fake.playerByName(USERNAME) !== null, 'bot joined')
    await waitFor(async () => (await request(runtimePort, 'GET', '/minecraft/status')).body.status === 'ONLINE', 'ONLINE')
    await fake.spawnObserver('Tester')
    await sleep(500)
    const followA = await request(runtimePort, 'POST', '/minecraft/follow_player', { username: 'Tester' })
    assert(followA.status === 200 && followA.body.action_id, 'Follow A started: ' + JSON.stringify(followA.body))
    const actionA = followA.body.action_id
    const noAuth = await request(runtimePort, 'POST', '/minecraft/stop', { action_id: actionA }, null)
    assert(noAuth.status === 401, 'missing bridge token -> 401, got ' + noAuth.status)
    const badJson = await new Promise((resolve) => {
      const req = http.request({ host:'127.0.0.1', port:runtimePort, method:'POST', path:'/minecraft/stop', headers:{ 'Content-Type':'application/json', Authorization:'Bearer '+TOKEN } }, (res)=>{res.resume(); res.on('end',()=>resolve(res.statusCode))})
      req.write('{not json'); req.end()
    })
    assert(badJson === 400, 'malformed body -> 400, got ' + badJson)
    const invalid = await request(runtimePort, 'POST', '/minecraft/stop', { action_id: '' })
    assert(invalid.status === 400, 'empty action_id -> 400, got ' + invalid.status)
    const stopIdA =
      's1.' + Buffer.from(actionA, 'utf8').toString('base64url') + '.seed'
    const targeted = await request(runtimePort, 'POST', '/minecraft/stop', {
      action_id: actionA,
      stop_request_id: stopIdA,
    })
    assert(targeted.status === 200 && targeted.body.targeted === true, 'targeted stop marked targeted')
    let stopTerminal = null
    for (let i = 0; i < 100 && !stopTerminal; i += 1) {
      stopTerminal = events.find(
        (item) => item.action_id === actionA && item.status === 'CANCELLED',
      )
      if (!stopTerminal) await sleep(50)
    }
    assert(Boolean(stopTerminal), 'stopped Action A terminal captured')
    assert(
      stopTerminal && stopTerminal.cleanup && stopTerminal.cleanup.attempted === true,
      'scoped stop terminal carries cleanup evidence',
    )
    assert(Array.isArray(targeted.body.cancelled) && targeted.body.cancelled.includes(actionA), 'targeted stop cancelled Action A')
    await sleep(200)
    const followB = await request(runtimePort, 'POST', '/minecraft/follow_player', { username: 'Tester' })
    assert(followB.status === 200 && followB.body.action_id && followB.body.action_id !== actionA, 'Follow B started')
    const actionB = followB.body.action_id
    const conflict = await request(runtimePort, 'POST', '/minecraft/stop', {
      action_id: actionB,
      stop_request_id: stopIdA,
    })
    assert(conflict.status === 409, 'same stop_request_id cannot rebind to Action B, got ' + conflict.status)
    const afterConflict = await request(runtimePort, 'GET', '/minecraft/status')
    assert(afterConflict.body.action && afterConflict.body.action.active_count >= 1, 'Action B survives conflicted Stop')
    for (let i = 0; i < 260; i += 1) {
      await request(runtimePort, 'POST', '/minecraft/stop', {
        action_id: 'act_missing_evict',
        stop_request_id: 'evict-' + i,
      })
    }
    const evicted = await request(runtimePort, 'POST', '/minecraft/stop', {
      action_id: actionB,
      stop_request_id: stopIdA,
    })
    assert(evicted.status === 409, 'evicted stop id cannot rebind to B, got ' + evicted.status)
    const afterEvicted = await request(runtimePort, 'GET', '/minecraft/status')
    assert(afterEvicted.body.action && afterEvicted.body.action.active_count >= 1, 'Action B survives evicted stop id')
    const late = await request(runtimePort, 'POST', '/minecraft/stop', { action_id: actionA })
    assert(late.status === 200 && Array.isArray(late.body.cancelled) && late.body.cancelled.length === 0, 'late Stop A matches nothing')
    const status = await request(runtimePort, 'GET', '/minecraft/status')
    assert(status.body.action && status.body.action.active_count >= 1, 'Action B still active after late Stop A')
    const global = await request(runtimePort, 'POST', '/minecraft/stop')
    assert(global.status === 200 && Array.isArray(global.body.cancelled) && global.body.cancelled.includes(actionB), 'global stop cancels B')
  } finally {
    try { child.kill() } catch {}
    try { await fake.close() } catch {}
    try { fake.cleanup() } catch {}
    await new Promise((r) => receiver.close(r))
  }
  console.log('\n[stop-http] ' + (checks - failures) + '/' + checks + ' checks passed')
  if (failures) { console.error(logs.slice(-20).join('')); process.exit(1) }
  process.exit(0)
}
main().catch((e) => { console.error('[stop-http] fatal', e); process.exit(1) })
