'use strict'
const assert = require('assert/strict')
const net = require('net')
const path = require('path')
const crypto = require('crypto')
const { spawn } = require('child_process')
const { once } = require('events')
const { sign } = require('../strict_attribution')

async function main() {
  const allocate = net.createServer()
  allocate.listen(0, '127.0.0.1'); await once(allocate, 'listening')
  const port = allocate.address().port
  await new Promise(resolve => allocate.close(resolve))
  const key = crypto.randomBytes(32).toString('hex'), bridge = crypto.randomBytes(32).toString('hex')
  const child = spawn(process.execPath, [path.join(__dirname, '../runtime.js')], {
    env: { ...process.env, MC_RUNTIME_PORT: String(port), MC_BRIDGE_TOKEN: bridge,
      MC_ATTRIBUTION_STRICT: '1', MC_ATTRIBUTION_TOKEN: key, MC_CALLBACK_URL: '' },
    stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  })
  const exited = once(child, 'exit')
  let logs = '', checks = 0
  child.stdout.on('data', value => { logs = (logs + value).slice(-32000) })
  child.stderr.on('data', value => { logs = (logs + value).slice(-32000) })
  const base = 'http://127.0.0.1:' + port
  const check = (condition, label) => { assert(condition, label); checks++; console.log('  OK   ' + label) }
  try {
    const deadline = Date.now() + 15000
    let online = false
    while (Date.now() < deadline && !online) {
      try { online = (await fetch(base + '/minecraft/health', { headers: { Authorization: 'Bearer ' + bridge }, signal: AbortSignal.timeout(500) })).ok } catch {}
      if (!online) await new Promise(resolve => setTimeout(resolve, 50))
    }
    check(online, 'actual strict runtime HTTP process starts')
    check((await fetch(base + '/minecraft/health')).status === 401, 'evidence channel does not open product API authentication')
    const event = JSON.stringify({ schema: 1, source: 'server_block_break', event_id: 'unused',
      reservation_id: 'unused', action_id: 'act_old', shadow: 'SELF_CONFIRMED' })
    const send = (body, signature) => fetch(base + '/minecraft/attribution/event', { method: 'POST', body,
      headers: { 'Content-Type': 'application/json', 'X-Signature': signature } })
    check((await send(event, bridge)).status === 401, 'product bearer cannot impersonate signed server evidence')
    const inactive = await send(event, sign(event, key))
    check(inactive.status === 200 && (await inactive.json()).reason === 'inactive', 'authenticated late event stays inactive across real HTTP route')
    const oversized = ' '.repeat(8193)
    check((await send(oversized, sign(oversized, key))).status === 413, 'actual HTTP reader rejects oversized body')
    const began = Date.now()
    const response = await new Promise((resolve, reject) => {
      const socket = net.connect(port, '127.0.0.1')
      let raw = ''
      socket.setTimeout(9000, () => { socket.destroy(); reject(new Error('incomplete request did not time out')) })
      socket.on('connect', () => socket.write('POST /minecraft/attribution/event HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\nX-Signature: ' + '0'.repeat(64) + '\r\nConnection: close\r\n\r\n{'))
      socket.on('data', chunk => { raw += chunk.toString() })
      socket.on('error', reject)
      socket.on('close', () => resolve(raw))
    })
    check(String(response).includes('408') && Date.now() - began < 8500, 'slow incomplete body has bounded receive deadline')
    check((await fetch(base + '/minecraft/health', { headers: { Authorization: 'Bearer ' + bridge } })).ok, 'runtime remains healthy after rejected and timed-out evidence')
    check(!logs.includes(key) && !logs.includes(bridge), 'credential literals absent from runtime logs')
    console.log('[attribution-http-test] ' + checks + '/' + checks + ' checks passed')
  } finally {
    child.kill('SIGTERM')
    await Promise.race([exited, new Promise((_, reject) => setTimeout(() => reject(new Error('owned HTTP test process did not exit')), 3000))])
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
