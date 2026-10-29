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
const output = process.env.P27_OUTPUT || path.join(root, 'data/p2_7/healthy')
fs.mkdirSync(output, { recursive: true })
const disabled = process.argv.includes('--disabled')
const failure = process.argv.includes('--failure')
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
  const marker = 'P27_' + crypto.randomUUID()
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

async function main() {
  bot = mineflayer.createBot({ host: '127.0.0.1', port: 25565, username: 'P27Bot', auth: 'offline', version: '1.21.1' })
  mate = mineflayer.createBot({ host: '127.0.0.1', port: 25565, username: 'P27Mate', auth: 'offline', version: '1.21.1' })
  for (const client of [bot, mate]) client.on('error', error => write({ client: client.username, error: String(error) }))
  await bounded(Promise.all([once(bot, 'spawn'), once(mate, 'spawn')]), 'clients spawn', 30000)
  await command(`gamemode survival ${bot.username}`)
  await command(`effect give ${bot.username} minecraft:resistance 600 255 true`)
  await command(`give ${bot.username} minecraft:netherite_axe`)
  await until(() => bot.inventory.items().find(item => item.name === 'netherite_axe'), 'axe inventory')
  await bot.equip(bot.inventory.items().find(item => item.name === 'netherite_axe'), 'hand')
  const p = { x: 11005, y: 100, z: 11003 }
  await room('minecraft:overworld', p)
  await move(bot, 'minecraft:overworld', p)
  const count = failure ? 4 : 1
  for (let index = 0; index < count; index++) {
    if (index) {
      await command(`setblock ${p.x} ${p.y} ${p.z} minecraft:oak_log`)
      await until(() => bot.blockAt(new Vec3(p.x, p.y, p.z))?.name === 'oak_log', 'replacement loaded')
    }
    const entry = { run, index, pos: p, disabled, failure }
    if (!disabled) entry.reservation = await reservation(p, `p27_${run}_${index}`)
    if (failure) {
      await bounded(bot.dig(bot.blockAt(new Vec3(p.x, p.y, p.z)), true), 'fault injection dig')
      entry.actual = await world('minecraft:overworld', p, 'air')
    } else {
      Object.assign(entry, await dig(bot, p, events().length))
      if (!disabled) {
        assert.equal(entry.event.receiver.state, 'SELF_CONFIRMED')
        assert.equal(entry.event.event.reservation_id, entry.reservation.result.reservation_id)
      }
    }
    entry.status = 'PASS'; results.push(entry); write(entry)
  }
  const summary = { run, disabled, failure, status: 'PASS', results }
  fs.writeFileSync(path.join(output, 'fixture-summary.json'), JSON.stringify(summary, null, 2))
}
const deadline = setTimeout(() => { console.error('experiment budget exceeded'); process.exit(2) }, 90000)
main().catch(error => { write({ fatal: String(error), status: 'FAIL' }); process.exitCode = 1 }).finally(async () => {
  clearTimeout(deadline)
  for (const timer of timers) clearTimeout(timer)
  if (bot) bot.quit(); if (mate) mate.quit()
  await delay(100)
})
