'use strict'
/** Real Node HTTP controller ownership/fencing tests. */
const { spawn } = require('child_process')
const http = require('http')
const net = require('net')
const path = require('path')
const fs = require('fs')
const os = require('os')
const { createFakeServer } = require('./fake_server')

const RUNTIME = path.join(__dirname, '..', 'runtime.js')
const TOKEN = 'controller-http-token'
const USERNAME = 'GuanTou'
let checks = 0
let failures = 0
function assert(condition, message) {
  checks += 1
  if (!condition) { failures += 1; console.error('  x ' + message) } else { console.log('  ok ' + message) }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
function freePort() { return new Promise((resolve, reject) => { const s = net.createServer(); s.unref(); s.on('error', reject); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)) }) }) }
function request(port, method, urlPath, body, token = TOKEN, controllerToken = '') {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body)
    const headers = { 'Content-Type': 'application/json', ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}), ...(token ? { Authorization: 'Bearer ' + token } : {}) }
    if (controllerToken) headers['X-Catoo-Controller-Token'] = controllerToken
    const req = http.request({ host: '127.0.0.1', port, method, path: urlPath, headers }, (res) => {
      let raw = ''; res.on('data', (d) => (raw += d)); res.on('end', () => { let parsed; try { parsed = raw ? JSON.parse(raw) : null } catch { parsed = raw } resolve({ status: res.statusCode, body: parsed }) })
    })
    req.on('error', reject); if (payload) req.write(payload); req.end()
  })
}
async function waitForRuntime(port) { for (let i = 0; i < 240; i += 1) { try { const r = await request(port, 'GET', '/minecraft/health'); if (r.status === 200 && r.body && r.body.ok) return } catch {} await sleep(250) } throw new Error('runtime not healthy') }
async function waitFor(fn, label) { for (let i = 0; i < 240; i += 1) { if (await fn()) return; await sleep(150) } throw new Error('timeout: ' + label) }
async function main() {
  const runtimePort = await freePort(); const serverPort = await freePort(); const callbackPort = await freePort()
  const workDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-controller-'))
  const authFile = path.join(workDir, 'auth.json')
  fs.writeFileSync(authFile, JSON.stringify({ mode: 'offline', username: USERNAME }), 'utf8')
  const receiver = http.createServer((req, res) => { req.resume(); res.writeHead(200).end('{"ok":true}') })
  await new Promise((r) => receiver.listen(callbackPort, '127.0.0.1', r))
  const fake = createFakeServer({ port: serverPort })
  let child = null
  const startRuntime = async () => {
    child = spawn(process.execPath, [RUNTIME], { env: { ...process.env, MC_RUNTIME_PORT: String(runtimePort), MC_CALLBACK_URL: 'http://127.0.0.1:' + callbackPort + '/events', MC_CALLBACK_TOKEN: 'cb', MC_BRIDGE_TOKEN: TOKEN, MC_AUTH_FILE: authFile, MC_CONNECT_TIMEOUT: '30', MC_CONTROLLER_TTL: '1' }, stdio: ['ignore','pipe','pipe'] })
    child.stdout.on('data', () => {}); child.stderr.on('data', () => {})
    await waitForRuntime(runtimePort)
  }
  try {
    await startRuntime()
    const unclaimedNoToken = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort })
    assert(unclaimedNoToken.status === 403, 'UNCLAIMED + no token -> 403')
    const unclaimedStale = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort }, TOKEN, 'stale')
    assert(unclaimedStale.status === 403, 'UNCLAIMED + stale token -> 403')
    const claimA = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'A' })
    assert(claimA.status === 200 && claimA.body.owner_token, 'A claim succeeds')
    assert(claimA.body.owner_epoch === 1, 'A epoch = 1')
    const tokenA = claimA.body.owner_token
    const claimB = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'B' })
    assert(claimB.status === 409 && claimB.body.error.code === 'controller.owned', 'B claim rejected 409')
    const bWrite = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort })
    assert(bWrite.status === 403 && bWrite.body.error.code === 'controller.fenced', 'B write without token 403')
    const aConnect = await request(runtimePort, 'POST', '/minecraft/connect', { host: '127.0.0.1', port: serverPort }, TOKEN, tokenA)
    assert(aConnect.status === 200, 'A connect with token succeeds')
    await waitFor(async () => { const r = await request(runtimePort, 'GET', '/minecraft/status'); return r.body.status === 'ONLINE' }, 'online')
    const staleWrite = await request(runtimePort, 'POST', '/minecraft/chat', { message: 'x' }, TOKEN, 'deadbeef')
    assert(staleWrite.status === 403, 'stale/wrong token write 403')
    const renewA = await request(runtimePort, 'POST', '/minecraft/controller/renew', { controller_id: 'A', owner_epoch: 1, owner_token: tokenA })
    assert(renewA.status === 200 && renewA.body.state === 'ACTIVE', 'A renew succeeds')
    const stopNoOwner = await request(runtimePort, 'POST', '/minecraft/stop', {})
    assert(stopNoOwner.status === 200, 'STOP remains available without owner token')
    const statusNoOwner = await request(runtimePort, 'GET', '/minecraft/status')
    assert(statusNoOwner.status === 200, 'read-only status available without owner token')
    // Pending action blocks takeover/release until it is really terminated.
    const tester = await fake.spawnObserver('Tester')
    await waitFor(() => fake.playerByName('Tester') !== null, 'Tester joined')
    const follow = await request(runtimePort, 'POST', '/minecraft/follow_player', { username: 'Tester' }, TOKEN, tokenA)
    assert(follow.status === 200 && follow.body.action_id, 'A starts real Follow action')
    const releasePending = await request(runtimePort, 'POST', '/minecraft/controller/release', { controller_id: 'A', owner_epoch: 1, owner_token: tokenA })
    assert(releasePending.status === 409 && releasePending.body.error.code === 'controller.actions_pending', 'release blocked while action pending')
    const stopPending = await request(runtimePort, 'POST', '/minecraft/stop', { action_id: follow.body.action_id })
    assert(stopPending.status === 200 && stopPending.body.cancelled.includes(follow.body.action_id), 'STOP available for pending action')
    await sleep(200)
    tester.close()
    const releaseA = await request(runtimePort, 'POST', '/minecraft/controller/release', { controller_id: 'A', owner_epoch: 1, owner_token: tokenA })
    assert(releaseA.status === 200 && releaseA.body.released === true, 'A release succeeds')
    const oldWrite = await request(runtimePort, 'POST', '/minecraft/chat', { message: 'x' }, TOKEN, tokenA)
    assert(oldWrite.status === 403, 'old A token replayed write 403')
    const claimB2 = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'B', owner_epoch: 1 })
    assert(claimB2.status === 200 && claimB2.body.owner_epoch >= 2, 'B takeover rotates epoch')
    const tokenB = claimB2.body.owner_token
    const bChat = await request(runtimePort, 'POST', '/minecraft/chat', { message: 'hi' }, TOKEN, tokenB)
    assert(bChat.status === 200, 'B write with new token succeeds')
    await sleep(1300)
    const expiredB = await request(runtimePort, 'POST', '/minecraft/chat', { message: 'late' }, TOKEN, tokenB)
    assert(expiredB.status === 403, 'expired B token write 403')
    const claimC = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'C', owner_epoch: 2 })
    assert(claimC.status === 200, 'C can claim after B expiry')
    // restart invalidates all old tokens
    child.kill()
    await new Promise((r) => child.once('exit', r))
    await startRuntime()
    const restartWrite = await request(runtimePort, 'POST', '/minecraft/chat', { message: 'x' }, TOKEN, tokenB)
    assert(restartWrite.status === 403, 'restart rejects old controller token')
    const claimD = await request(runtimePort, 'POST', '/minecraft/controller/claim', { controller_id: 'D' })
    assert(claimD.status === 200 && claimD.body.owner_epoch === 1, 'new runtime starts fresh epoch')
  } finally {
    try { if (child) child.kill() } catch {}
    try { await fake.close() } catch {}
    try { fake.cleanup() } catch {}
  }
  console.log('\n[controller] ' + (checks - failures) + '/' + checks + ' checks passed')
  if (failures) process.exit(1)
  process.exit(0)
}
main().catch((error) => { console.error('[controller] fatal', error); process.exit(1) })
