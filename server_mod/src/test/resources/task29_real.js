'use strict'
// Optional real-server SHADOW experiment, never CI or a product-task success test.
// Needs an already backed-up isolated offline Fabric server, test-only OP accounts,
// CATOO_ATTRIBUTION_TOKEN, and a separately running shadow receiver. No embedded secrets.
const fs = require('fs')
const path = require('path')
const crypto = require('crypto')
const assert = require('assert')
const { once } = require('events')
const root = process.env.CATOOBOT_ROOT || process.cwd()
const mineflayer = require(path.join(root, 'minecraft_runtime/node_modules/mineflayer'))
const { Vec3 } = require(path.join(root, 'minecraft_runtime/node_modules/vec3'))
const output = process.env.TASK29_OUTPUT || path.join(root, 'data/task29')
fs.mkdirSync(output, { recursive: true })
const disabled = process.argv.includes('--disabled')
const token = process.env.CATOO_ATTRIBUTION_TOKEN
assert(disabled || token, 'missing attribution token')
const run = crypto.randomUUID()
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const results = []
const timers = new Set()
let bot, mate

function write(value) {
  const record = { run, recorded_at: new Date().toISOString(), ...value }
  fs.appendFileSync(path.join(output, disabled ? 'disabled-attempts.jsonl' : 'attempts.jsonl'), JSON.stringify(record) + '\n')
  console.log(JSON.stringify(record))
}
async function bounded(promise, label, ms = 10000) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('timeout: ' + label)), ms)
      timers.add(timer)
    })])
  } finally { clearTimeout(timer); timers.delete(timer) }
}
async function until(test, label, ms = 10000) {
  const start = Date.now()
  while (Date.now() - start < ms) {
    const value = await test()
    if (value) return value
    await delay(100)
  }
  throw new Error('timeout: ' + label)
}
function dimension(client) {
  const text = String(client.game?.dimension || '')
  if (text.includes('nether')) return 'minecraft:the_nether'
  if (text.includes('end')) return 'minecraft:the_end'
  return text.includes('overworld') ? 'minecraft:overworld' : ''
}
function events() {
  const file = path.join(output, 'receiver-events.jsonl')
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)) : []
}
async function api(route, payload, name) {
  const body = payload === undefined ? undefined : JSON.stringify(payload)
  const signed = name === undefined ? (body || '') : name
  const signature = crypto.createHmac('sha256', token).update(signed).digest('hex')
  const response = await fetch('http://127.0.0.1:19132' + route, {
    method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-Signature': signature },
    body, signal: AbortSignal.timeout(5000),
  })
  return { code: response.status, ...(await response.json()) }
}
async function command(text) {
  write({ fixture_command: text })
  mate.chat('/' + text)
  await delay(160)
}
async function world(dim, p, block) {
  const marker = 'T29_' + crypto.randomUUID()
  const received = new Promise((resolve, reject) => {
    const listener = text => { if (text.includes(marker)) { clearTimeout(timer); mate.off('messagestr', listener); resolve(true) } }
    const timer = setTimeout(() => { mate.off('messagestr', listener); reject(new Error('server block check failed: ' + [dim, p, block])) }, 5000)
    mate.on('messagestr', listener)
  })
  mate.chat(`/execute in ${dim} if block ${p.x} ${p.y} ${p.z} minecraft:${block} run tellraw ${mate.username} {"text":"${marker}"}`)
  await received
  return { source: 'server_execute_if_block', dimension: dim, pos: `${p.x},${p.y},${p.z}`, block: 'minecraft:' + block }
}
async function room(dim, p) {
  await command(`execute in ${dim} run forceload add ${p.x} ${p.z}`)
  await command(`execute in ${dim} run fill ${p.x - 5} 98 ${p.z - 3} ${p.x + 1} 103 ${p.z + 3} minecraft:stone`)
  await command(`execute in ${dim} run fill ${p.x - 4} 99 ${p.z - 2} ${p.x} 102 ${p.z + 2} minecraft:air`)
  await command(`execute in ${dim} run setblock ${p.x} ${p.y} ${p.z} minecraft:oak_log`)
  return world(dim, p, 'oak_log')
}
async function move(client, dim, p, offset = 0) {
  const target = { x: p.x - 1.5 - offset, y: 99, z: p.z + 0.5 }
  await command(`execute in ${dim} run tp ${client.username} ${target.x} ${target.y} ${target.z}`)
  await until(() => dimension(client) === dim && client.entity?.position?.distanceTo(new Vec3(target.x, target.y, target.z)) < 1.5,
    'client/server dimension and position convergence ' + client.username, 15000)
  await until(() => client.blockAt(new Vec3(p.x, p.y, p.z))?.name === 'oak_log', 'loaded target block', 10000)
  await delay(350)
  return { client: client.username, dimension: dimension(client), position: client.entity.position.clone() }
}
async function reservation(p, id) {
  const session = await api('/session?name=' + encodeURIComponent(bot.username), undefined, bot.username)
  assert.equal(session.code, 200)
  const payload = { session_ref: session.session_ref, action_id: id, nonce: crypto.randomUUID(),
    dimension: 'minecraft:overworld', pos: `${p.x},${p.y},${p.z}`, x: p.x, y: p.y, z: p.z,
    expected_block: 'minecraft:oak_log', ttl_ms: 120000 }
  const result = await api('/reserve', payload)
  assert.equal(result.code, 200, JSON.stringify(result))
  assert.equal(result.ok, true)
  return { payload, result, session, reserved_at: Date.now() }
}
async function dig(client, p, start) {
  const block = client.blockAt(new Vec3(p.x, p.y, p.z))
  assert(block && block.name === 'oak_log', 'target not loaded or changed')
  assert(client.entity.position.distanceTo(block.position) < 5, 'fixture outside dig reach')
  await bounded(client.dig(block, true), 'normal horizontal dig', 10000)
  const actual = await world(dimension(client), p, 'air')
  if (disabled) return { actual }
  const event = await until(() => events().slice(start).find(entry => entry.event.pos === `${p.x},${p.y},${p.z}` &&
    entry.event.player_uuid === client.player.uuid && entry.event.dimension === dimension(client)), 'real delivered AFTER', 5000)
  return { actual, event }
}
async function sample(kind, index) {
  const p = { x: 10005, y: 100, z: 10003 + index * 10 }
  const id = `task29_${run}_${kind}_${index}`
  const attempt = { id, kind, pos: p }
  try {
    attempt.before = await room('minecraft:overworld', p)
    attempt.bot_observation = await move(bot, 'minecraft:overworld', p)
    if (kind === 'other') await move(mate, 'minecraft:overworld', p, 1)
    attempt.reservation = await reservation(p, id)
    const start = events().length
    const stats = await api('/stats')
    if (kind === 'external') {
      await command(`execute in minecraft:overworld run setblock ${p.x} ${p.y} ${p.z} minecraft:air`)
      attempt.after = await world('minecraft:overworld', p, 'air')
      await delay(800)
      assert(!events().slice(start).some(entry => entry.event.pos === `${p.x},${p.y},${p.z}`), 'command unexpectedly produced AFTER')
      attempt.classification = 'UNKNOWN_NO_PLAYER_EVENT'
    } else {
      const result = await dig(kind === 'other' ? mate : bot, p, start)
      Object.assign(attempt, result)
      if (kind === 'normal') {
        assert.equal(result.event.event.reservation_id, attempt.reservation.result.reservation_id)
        assert.equal(result.event.event.action_id, id)
        assert.equal(result.event.receiver.state, 'SELF_CONFIRMED')
      } else {
        assert.notEqual(result.event.event.shadow, 'SELF_CONFIRMED')
        assert.notEqual(result.event.receiver.state, 'SELF_CONFIRMED')
        assert.equal((await api('/stats')).consumed, stats.consumed)
      }
    }
    attempt.status = 'PASS'
  } catch (error) { attempt.status = 'FAIL'; attempt.error = String(error); throw error }
  finally { results.push(attempt); write(attempt) }
}
async function cross(index) {
  const p = { x: 10005, y: 100, z: 10003 + index * 10 }
  const id = `task29_${run}_cross_${index}`
  const attempt = { id, kind: 'cross', pos: p }
  try {
    attempt.overworld_before = await room('minecraft:overworld', p)
    attempt.nether_before = await room('minecraft:the_nether', p)
    await move(bot, 'minecraft:overworld', p)
    attempt.reservation = await reservation(p, id)
    const before = await api('/stats')
    attempt.nether_observation = await move(bot, 'minecraft:the_nether', p)
    const negative = await dig(bot, p, events().length)
    attempt.negative = negative
    assert.equal(negative.event.event.dimension, 'minecraft:the_nether')
    assert.notEqual(negative.event.event.shadow, 'SELF_CONFIRMED')
    assert.notEqual(negative.event.receiver.state, 'SELF_CONFIRMED')
    assert.equal((await api('/stats')).consumed, before.consumed)
    attempt.overworld_preserved = await world('minecraft:overworld', p, 'oak_log')
    assert(Date.now() - attempt.reservation.reserved_at < 120000, 'reservation expired before paired positive')
    await move(bot, 'minecraft:overworld', p)
    const positive = await dig(bot, p, events().length)
    attempt.paired_positive = positive
    assert.equal(positive.event.event.reservation_id, attempt.reservation.result.reservation_id)
    assert.equal(positive.event.event.action_id, id)
    assert.equal(positive.event.receiver.state, 'SELF_CONFIRMED')
    assert.equal((await api('/stats')).consumed, before.consumed + 1)
    attempt.status = 'PASS'
  } catch (error) { attempt.status = 'FAIL'; attempt.error = String(error); throw error }
  finally { results.push(attempt); write(attempt) }
}
async function main() {
  bot = mineflayer.createBot({ host: '127.0.0.1', port: 25565, username: 'Task29Bot', auth: 'offline', version: '1.21.1' })
  mate = mineflayer.createBot({ host: '127.0.0.1', port: 25565, username: 'Task29Mate', auth: 'offline', version: '1.21.1' })
  for (const client of [bot, mate]) client.on('error', error => write({ client: client.username, error: String(error) }))
  await bounded(Promise.all([once(bot, 'spawn'), once(mate, 'spawn')]), 'both clients spawn', 30000)
  write({ fixture_ready: true, run, disabled, clients: [bot.username, mate.username] })
  await until(() => fs.existsSync(path.join(output, disabled ? 'disabled-op-ready' : 'op-ready')), 'operator grants prepared', 30000)
  for (const client of [bot, mate]) {
    await command(`gamemode survival ${client.username}`)
    await command(`effect give ${client.username} minecraft:resistance 600 255 true`)
    await command(`effect give ${client.username} minecraft:fire_resistance 600 0 true`)
    await command(`give ${client.username} minecraft:netherite_axe`)
    await until(() => client.inventory.items().find(item => item.name === 'netherite_axe'), 'axe inventory')
    await client.equip(client.inventory.items().find(item => item.name === 'netherite_axe'), 'hand')
  }
  if (disabled) {
    const before = events().length
    for (let i = 0; i < 2; i++) {
      const p = { x: 10005, y: 100, z: 10503 + i * 10 }
      await room('minecraft:overworld', p); await move(bot, 'minecraft:overworld', p)
      write({ kind: 'disabled_dig', index: i, ...(await dig(bot, p, events().length)), status: 'PASS' })
    }
    assert.equal(events().length, before, 'disabled mod delivered an event')
  } else {
    await sample('normal', 1) // new-build positive control before cross-dimension test
    let crossed = false
    for (let i = 0; i < 3 && !crossed; i++) {
      try { await cross(2 + i); crossed = true } catch (error) { write({ cross_retry: i, error: String(error) }) }
    }
    assert(crossed, 'strictly paired cross-dimension test did not pass within 3 attempts')
    for (let i = 0; i < 10; i++) await sample('normal', 10 + i)
    for (let i = 0; i < 5; i++) await sample('external', 25 + i)
    for (let i = 0; i < 5; i++) await sample('other', 35 + i)
    write({ final_stats: await api('/stats'), experiment: 'shadow protocol, NOT product-task E3', status: 'PASS' })
    const oldSession = await api('/session?name=' + encodeURIComponent(bot.username), undefined, bot.username)
    const ended = once(bot, 'end')
    bot.quit()
    await bounded(ended, 'bot disconnect observed')
    await until(async () => (await api('/session?name=' + encodeURIComponent(bot.username), undefined, bot.username)).code === 404,
      'server invalidates disconnected session')
    const refused = await api('/reserve', { session_ref: oldSession.session_ref, action_id: 'disconnected_' + run,
      nonce: crypto.randomUUID(), dimension: 'minecraft:overworld', pos: '10005,100,10013',
      x: 10005, y: 100, z: 10013, expected_block: 'minecraft:oak_log', ttl_ms: 120000 })
    assert.equal(refused.code, 409)
    assert.equal(refused.reason, 'session_not_current')
    write({ disconnected_session_rejected: true, session_ref: oldSession.session_ref, response: refused })
  }
  fs.writeFileSync(path.join(output, disabled ? 'disabled-summary.json' : 'real-summary.json'), JSON.stringify({ run, disabled, results }, null, 2))
}
const deadline = setTimeout(() => { console.error('experiment budget exceeded'); process.exit(2) }, 360000)
main().catch(error => { write({ fatal: String(error), status: 'FAIL' }); process.exitCode = 1 }).finally(async () => {
  clearTimeout(deadline)
  for (const timer of timers) clearTimeout(timer)
  if (bot) bot.quit(); if (mate) mate.quit()
  await delay(500)
})
