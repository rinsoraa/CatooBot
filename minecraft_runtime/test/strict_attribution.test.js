'use strict'
const assert = require('assert/strict')
const http = require('http')
const crypto = require('crypto')
const { once } = require('events')
const { StrictAttribution, sign, verify, COLLECTION_PROTOCOL } = require('../strict_attribution')
const token = crypto.randomBytes(32).toString('hex')
const uuid = crypto.randomUUID(), ref = crypto.randomUUID()
let passed = 0, mode = '', active, hold, lastFields, reservation, pickupReceipt, dropUuid
function check(value, label) { assert(value, label); passed++; console.log('  OK   ' + label) }
function event(fields = lastFields) {
  return { schema: 1, source: 'server_block_break', event_id: crypto.randomUUID(),
    reservation_id: reservation, action_id: fields.action_id, session_ref: ref, session_seq: 1,
    player_uuid: uuid, dimension: 'minecraft:overworld', pos: '1,64,3',
    block_before: 'minecraft:oak_log', block_after: 'minecraft:air', shadow: 'SELF_CONFIRMED' }
}
function collection(fields = lastFields) {
  return { schema: 1, source: 'server_drop_evidence', collection_protocol: COLLECTION_PROTOCOL, event_id: crypto.randomUUID(),
    break_event_id: active.pending.get(fields.action_id).event.event_id,
    reservation_id: reservation, action_id: fields.action_id, session_ref: ref, session_seq: 1,
    player_uuid: uuid, dimension: 'minecraft:overworld', pos: '1,64,3',
    block_before: 'minecraft:oak_log', drops: [{ uuid: dropUuid, entity_id: 77,
      item: 'minecraft:oak_log', count: 1 }] }
}
function pickupEvent(fields, patch = {}) {
  return { schema: 1, source: 'server_drop_pickup', collection_protocol: COLLECTION_PROTOCOL, event_id: crypto.randomUUID(),
    booking_id: pickupReceipt.booking_id, pickup_action_id: fields.action_id,
    break_event_id: pickupReceipt.break_event_id, break_reservation_id: pickupReceipt.reservation_id,
    break_action_id: pickupReceipt.break_action_id, session_ref: ref, session_seq: 1,
    player_uuid: uuid, dimension: 'minecraft:overworld', drop_uuid: dropUuid,
    item: 'minecraft:oak_log', count: 1, inventory_before: 3, inventory_after: 4,
    collected: 1, confirmed: true, reason: '', ...patch }
}
function deliver(value, client = active) {
  const body = JSON.stringify(value)
  return client.receive(body, sign(body, token))
}
const api = http.createServer(async (request, response) => {
  let raw = ''
  for await (const chunk of request) raw += chunk
  const url = new URL(request.url, 'http://localhost')
  const write = value => {
    const body = mode === 'oversize' ? ' '.repeat(9000) : JSON.stringify(value)
    response.writeHead(200, { 'Content-Type': 'application/json',
      'X-Signature': mode === 'unsigned' ? '' : sign(body, token) })
    response.end(body)
  }
  if (url.pathname === '/session') {
    assert(verify(url.searchParams.get('name') + '\n' + url.searchParams.get('nonce'), token, request.headers['x-signature']))
    write({ ok: true, schema: 1, nonce: mode === 'replay' ? 'old' : url.searchParams.get('nonce'),
      session_ref: ref, session_seq: 1, player_uuid: uuid,
      ...(mode === 'legacy' ? {} : { collection_protocol: mode === 'wrong_protocol' ? 'future-v9' : mode === 'numeric_protocol' ? 1 : COLLECTION_PROTOCOL }) })
  } else if (url.pathname === '/collection/pickup-reserve') {
    assert(verify(raw, token, request.headers['x-signature']))
    lastFields = JSON.parse(raw)
    pickupReceipt = { ok: true, schema: 1, session_ref: ref, action_id: lastFields.action_id,
      nonce: lastFields.nonce, booking_id: crypto.randomUUID(), drop_uuid: lastFields.drop_uuid,
      break_event_id: crypto.randomUUID(), reservation_id: reservation, break_action_id: 'dig-parent',
      ...(mode === 'receipt_downgrade' ? {} : { collection_protocol: COLLECTION_PROTOCOL }) }
    write(pickupReceipt)
  } else {
    assert(verify(raw, token, request.headers['x-signature']))
    lastFields = JSON.parse(raw); reservation = crypto.randomUUID()
    const receipt = { ok: true, schema: 1, session_ref: ref, action_id: lastFields.action_id,
      nonce: lastFields.nonce, reservation_id: reservation,
      ...(lastFields.collection_protocol && mode !== 'receipt_downgrade' ? { collection_protocol: COLLECTION_PROTOCOL } : {}) }
    if (mode === 'early') deliver(event())
    if (mode === 'wrong_receipt') receipt.action_id = 'different'
    if (mode === 'hold') { hold = () => write(receipt); return }
    write(receipt)
  }
})

async function main() {
  dropUuid = crypto.randomUUID()
  api.listen(0, '127.0.0.1'); await once(api, 'listening')
  const url = 'http://127.0.0.1:' + api.address().port
  let sequence = 0
  const input = () => ({ actionId: 'act_rt_probe_' + ++sequence, sessionId: 'mc_probe', name: 'ProbeBot', uuid,
    dimension: 'overworld', target: { x: 1, y: 64, z: 3 }, block: 'oak_log' })
  const create = () => { active = new StrictAttribution({ enabled: true, url, token }); return active.begin(input()) }
  let ctx = create(); await ctx.prepare()
  const valid = event()
  check(deliver(valid).code === 200, 'authenticated event correlates after signed registration')
  const proof = await ctx.wait(20)
  check(proof.schema === 3 && proof.confirm_basis === 'server_block_break' && proof.strict_self_proof === true,
    'new evidence schema carries server proof, never lifecycle timing')
  check(deliver(valid).reason === 'duplicate', 'identical event consumed once')
  check(deliver(event()).reason === 'conflict' && await ctx.wait(1) === null, 'conflicting second event cannot prove action')
  ctx.close(); check(active.pending.size === 0 && deliver(valid).reason === 'inactive', 'closed action drops state and ignores late delivery')

  for (mode of ['legacy', 'wrong_protocol', 'numeric_protocol', 'receipt_downgrade']) {
    ctx = active.begin({ ...input(), requireCollection: true })
    await assert.rejects(ctx.prepare(), error => error.code === 'block.attribution_unavailable')
    check(ctx.state.closed && active.pending.size === 0, 'collection refuses missing/unknown/downgraded protocol: ' + mode)
    const pickup = active.beginPickup({ actionId: 'pickup_protocol_' + ++sequence, sessionId: 'mc_probe',
      name: 'ProbeBot', uuid, dimension: 'overworld', entityUuid: dropUuid })
    await assert.rejects(pickup.prepare(), error => error.code === 'block.attribution_unavailable')
    check(pickup.state.closed && active.pickupPending.size === 0, 'pickup refuses missing/unknown/downgraded protocol: ' + mode)
  }
  mode = 'legacy'; ctx = create(); await ctx.prepare(); deliver(event())
  check((await ctx.wait(20)).strict_self_proof === true, 'legacy P3 signed break remains compatible without collection claim')
  ctx.close(); mode = ''

  ctx = active.begin({ ...input(), requireCollection: true }); await ctx.prepare()
  check(deliver(event()).code === 200, 'break certificate accepted for exact collection scope')
  const collectionEvent = collection()
  check(deliver(collectionEvent).code === 200, 'exact spawned entity evidence accepted')
  const collected = await ctx.wait(20)
  check(collected.server_collection.drops[0].uuid === dropUuid &&
    collected.server_collection.action_id === lastFields.action_id &&
    collected.server_collection.reservation_id === reservation,
    'drop UUID remains independently tied to action, session and break certificate')
  check(deliver(collectionEvent).reason === 'duplicate', 'duplicate exact collection event remains one fact')
  ctx.close()
  for (const patch of [
    { collection_protocol: undefined }, { collection_protocol: 1 },
    { drops: [{ uuid: 'not-a-uuid', entity_id: 77, item: 'minecraft:oak_log', count: 1 }] },
    { drops: [{ uuid: dropUuid, entity_id: 77, item: 'minecraft:diamond', count: 1 }] },
    { drops: [{ uuid: dropUuid, entity_id: 77, item: 'minecraft:oak_log', count: 2 }] },
    { session_seq: 2 }, { break_event_id: crypto.randomUUID() },
  ]) {
    ctx = active.begin({ ...input(), requireCollection: true }); await ctx.prepare(); deliver(event())
    check(deliver({ ...collection(), ...patch }).code !== 200 && await ctx.wait(1) === null,
      'bad collection version/UUID/drop/count/session/parent never becomes proof: ' + Object.keys(patch)[0])
    ctx.close()
  }
  ctx = active.begin({ ...input(), requireCollection: true }); await ctx.prepare()
  let collectionSettled = false
  const pendingCollection = ctx.wait(200).then(value => { collectionSettled = true; return value })
  deliver(event())
  await new Promise(resolve => setImmediate(resolve))
  check(!collectionSettled, 'break notification alone does not terminate a pending collection wait')
  deliver(collection())
  check((await pendingCollection).server_collection.drops[0].uuid === dropUuid,
    'separate later drop event completes the same bounded collection wait')
  ctx.close()
  ctx = active.begin({ ...input(), requireCollection: true }); await ctx.prepare()
  check(deliver(event()).code === 200 && await ctx.wait(5) === null,
    'break alone cannot satisfy a resource collection requirement')
  const malformed = collection()
  malformed.drops = [{ ...malformed.drops[0], uuid: undefined }]
  check(deliver(malformed).code === 400, 'malformed collection drop is refused')
  ctx.close()

  const pickupInput = () => ({ actionId: 'pickup_' + (++sequence), sessionId: 'mc_probe',
    name: 'ProbeBot', uuid, dimension: 'overworld', entityUuid: dropUuid })
  let pickup = active.beginPickup(pickupInput()); await pickup.prepare()
  check(deliver(pickupEvent(lastFields)).reason === 'pickup_accepted', 'exact signed pickup evidence accepted')
  const pickupProof = await pickup.wait(20)
  check(pickupProof && pickupProof.drop_uuid === dropUuid && pickupProof.strict_self_proof &&
    pickupProof.inventory_before === 3 && pickupProof.inventory_after === 4,
    'pickup proof binds entity, booking and independent inventory delta')
  pickup.close()
  pickup = active.beginPickup({ ...pickupInput(), expectedBreakAction: 'different-parent', expectedItem: 'oak_log' })
  await assert.rejects(pickup.prepare(), error => error.code === 'block.attribution_unavailable')
  check(pickup.state.closed, 'signed receipt from another break cannot enable pickup')
  pickup = active.beginPickup({ ...pickupInput(), expectedBreakAction: 'dig-parent', expectedItem: 'oak_log' })
  await pickup.prepare()
  check(deliver(pickupEvent(lastFields, { break_action_id: 'another-break' })).code === 409 &&
    await pickup.wait(1) === null, 'another break event cannot satisfy the frozen collection parent')
  check(deliver(pickupEvent(lastFields, { break_action_id: 'dig-parent', item: 'minecraft:dirt' })).code === 409,
    'signed pickup of another item cannot satisfy the resource contract')
  check(deliver(pickupEvent(lastFields, { break_action_id: 'dig-parent', inventory_after: 3 })).code === 409,
    'confirmed flag cannot replace an actual inventory increment')
  const exactPickup = pickupEvent(lastFields, { break_action_id: 'dig-parent' })
  check(deliver({ ...exactPickup, break_reservation_id: crypto.randomUUID() }).code === 409,
    'pickup must match the exact break reservation, not only the event id')
  check(deliver({ ...exactPickup, break_reservation_id: undefined }).code === 400,
    'a missing break reservation cannot satisfy pickup')
  check(deliver(exactPickup).code === 200 && (await pickup.wait(1)).break_action_id === 'dig-parent',
    'matching parent, item and inventory delta can prove pickup')
  check(deliver({ ...exactPickup, event_id: crypto.randomUUID() }).code === 409 &&
    await pickup.wait(1) === null, 'conflicting second pickup invalidates the evidence context')
  pickup.close()
  pickup = active.beginPickup(pickupInput()); await pickup.prepare()
  check(deliver(pickupEvent(lastFields, { drop_uuid: crypto.randomUUID() })).code === 409 &&
    await pickup.wait(5) === null, 'a different entity UUID cannot prove pickup')
  pickup.close()
  pickup = active.beginPickup(pickupInput()); await pickup.prepare()
  check(deliver(pickupEvent(lastFields, { confirmed: false, collected: 0, reason: 'entity_merged' })).code === 200 &&
    await pickup.wait(5) === null, 'signed failed pickup remains unknown, never success')
  pickup.close()

  for (const patch of [{ schema: true }, { source: 'inferred' }, { event_id: '' }, { action_id: 4 },
    { reservation_id: 'wrong' }, { session_ref: 'old' }, { session_seq: '1' }, { player_uuid: crypto.randomUUID() },
    { dimension: 'minecraft:the_nether' }, { pos: '2,64,3' }, { block_before: 'minecraft:stone' },
    { block_after: 'minecraft:water' }]) {
    ctx = create(); await ctx.prepare()
    check(deliver({ ...event(), ...patch }).code !== 200 && await ctx.wait(1) === null,
      'mismatched schema/identity/target cannot create proof: ' + Object.keys(patch)[0]); ctx.close()
  }
  ctx = create(); await ctx.prepare()
  check(deliver({ ...event(), shadow: 'UNKNOWN', action_id: '', reservation_id: '' }).reason === 'unproven' && await ctx.wait(1) === null,
    'signed non-self observation acknowledged as unproven, never consumed as self proof')
  check(active.receive(JSON.stringify(event()), '0'.repeat(64)).code === 401 && await ctx.wait(1) === null, 'bad HMAC gives no proof')
  check(active.receive(' '.repeat(8193), '').code === 413, 'event body memory bounded')
  check(await ctx.wait(5) === null, 'missing event remains unconfirmed'); ctx.close()
  for (mode of ['unsigned', 'replay', 'wrong_receipt', 'oversize']) {
    ctx = create(); await assert.rejects(ctx.prepare(), /严格服务端归因不可用/)
    check(active.pending.size === 0, 'untrusted or uncertain registration closes context: ' + mode)
  }
  mode = 'early'; ctx = create(); await ctx.prepare()
  check((await ctx.wait(10)).strict_self_proof === true, 'event arriving before receipt still requires matching signed receipt'); ctx.close()
  mode = 'hold'; hold = null; ctx = create(); const waiting = ctx.prepare()
  while (!hold) await new Promise(resolve => setTimeout(resolve, 5))
  ctx.close(); hold(); await assert.rejects(waiting)
  check(active.pending.size === 0, 'cancellation during registration cannot reopen context with late receipt')
  mode = ''; const disabled = new StrictAttribution()
  check(disabled.begin(input()) === null && disabled.receive('{}', '').code === 503, 'default disabled has no strict evidence path')
  assert.throws(() => new StrictAttribution({ enabled: true, url: 'https://example.org', token }).begin(input()))
  check(true, 'strict client cannot send credentials off loopback')

  // Cross the real registry start/wait/cleanup boundary with this live authenticated HTTP peer.
  process.env.MC_ATTRIBUTION_STRICT = '1'; process.env.MC_ATTRIBUTION_URL = url; process.env.MC_ATTRIBUTION_TOKEN = token
  const { ACTION_REGISTRY, strictAttribution } = require('../runtime')
  const { Vec3 } = require('vec3')
  const def = ACTION_REGISTRY.dig
  function bot() {
    let block = { name: 'oak_log', position: new Vec3(1, 64, 3) }
    return { username: 'ProbeBot', player: { uuid }, game: { dimension: 'overworld' },
      entity: { position: new Vec3(1, 63, 1) }, heldItem: null, calls: 0,
      blockAt: () => block, canDigBlock: () => true,
      async dig() { this.calls++; block = { name: 'air', position: new Vec3(1, 64, 3) }; if (mode !== 'missing') deliver(event(), strictAttribution) },
      stopDigging() {}, clearControlStates() {} }
  }
  const params = { x: 1, y: 64, z: 3, expected_block: 'oak_log' }
  let fake = bot(), cancellation = { cancelled: false }, controller = { record: { action_id: 'act_rt_actual_1' } }
  let state = await def.start(fake, params, cancellation, controller); state.sessionId = 'mc_product'
  state.connectionStillCurrent = () => true // G1 fake connection; G2 uses the actual runtime connection closure.
  const result = await def.wait(fake, params, cancellation, state, controller)
  check(fake.calls === 1 && result.attribution.schema === 3 && result.attribution.strict_self_proof, 'actual dig implementation requires and emits server evidence')
  mode = 'missing'; fake = bot(); controller = { record: { action_id: 'act_rt_actual_missing', started_at: Date.now() / 1000 - 29.1 } }
  state = await def.start(fake, params, cancellation, controller); state.sessionId = 'mc_product'
  state.connectionStillCurrent = () => true
  await assert.rejects(def.wait(fake, params, cancellation, state, controller), error => error.code === 'block.attribution_unconfirmed')
  check(fake.calls === 1 && strictAttribution.pending.size === 0, 'actual block disappearance without a server event is FAILED, never inferred success')
  mode = 'unsigned'; fake = bot(); controller = { record: { action_id: 'act_rt_actual_2' } }
  state = await def.start(fake, params, cancellation, controller); state.sessionId = 'mc_product'
  state.connectionStillCurrent = () => true
  await assert.rejects(def.wait(fake, params, cancellation, state, controller), error => error.code === 'block.attribution_unavailable')
  check(fake.calls === 0, 'actual dig sends zero game actions when registration authentication fails')
  mode = 'hold'; hold = null; fake = bot(); controller = { record: { action_id: 'act_rt_actual_3' } }; cancellation = { cancelled: false }
  state = await def.start(fake, params, cancellation, controller); state.sessionId = 'mc_product'
  state.connectionStillCurrent = () => true
  const running = def.wait(fake, params, cancellation, state, controller)
  while (!hold) await new Promise(resolve => setTimeout(resolve, 5))
  cancellation.cancelled = true; cancellation.reason = 'stop'; def.cleanup(fake, controller); hold()
  await assert.rejects(running)
  check(fake.calls === 0 && strictAttribution.pending.size === 0, 'actual cleanup cancels registration before any dig, late response stays inert')
  for (const change of ['dimension', 'identity', 'connection']) {
    mode = 'hold'; hold = null; fake = bot(); cancellation = { cancelled: false }
    controller = { record: { action_id: 'act_rt_changed_' + change } }
    state = await def.start(fake, params, cancellation, controller); state.sessionId = 'mc_product'
    state.connectionStillCurrent = () => true
    const pending = def.wait(fake, params, cancellation, state, controller)
    while (!hold) await new Promise(resolve => setTimeout(resolve, 5))
    if (change === 'dimension') fake.game.dimension = 'the_nether'
    if (change === 'identity') fake.player.uuid = crypto.randomUUID()
    if (change === 'connection') state.connectionStillCurrent = () => false
    hold()
    await assert.rejects(pending, error => error.code === 'block.changed')
    check(fake.calls === 0 && strictAttribution.pending.size === 0, 'registration cannot authorize a changed ' + change)
  }
  const registry = require('prismarine-registry')('1.21.1')
  const Block = require('prismarine-block')(registry)
  const Item = require('prismarine-item')(registry)
  for (mode of ['legacy', 'wrong_protocol', 'numeric_protocol', 'receipt_downgrade']) {
    fake = bot(); fake.registry = registry; fake.entity.position = new Vec3(1, 64, 1)
    fake.blockAt = position => Block.fromStateId(registry.blocksByName[
      position.equals(new Vec3(1, 64, 3)) ? 'oak_log' : position.y < 64 ? 'stone' : 'air'
    ].minStateId, 0)
    const args = { ...params, require_collection: true, expected_empty_hand: true }
    controller = { record: { action_id: 'act_rt_protocol_' + mode } }; cancellation = { cancelled: false }
    state = await def.start(fake, args, cancellation, controller)
    state.sessionId = 'mc_product'; state.connectionStillCurrent = () => true
    await assert.rejects(def.wait(fake, args, cancellation, state, controller), error => error.code === 'block.attribution_unavailable')
    check(fake.calls === 0 && strictAttribution.pending.size === 0,
      'actual resource dig performs zero world calls on incompatible protocol: ' + mode)
    const originalPickup = strictAttribution.beginPickup
    strictAttribution.beginPickup = fields => originalPickup.call(strictAttribution, { ...fields, sessionId: 'mc_product' })
    let moves = 0
    try {
      fake.supportFeature = feature => registry.supportFeature(feature)
      fake.pathfinder = { setGoal() { moves++ } }
      fake.entities = { 77: { id: 77, name: 'item', uuid: dropUuid, position: new Vec3(1, 64, 3),
        metadata: [{ key: 8, type: 7, value: { present: true, itemId: registry.itemsByName.oak_log.id, itemCount: 1, nbt: null } }] } }
      await assert.rejects(ACTION_REGISTRY.pickup_item.start(fake,
        { entity_id: 77, entity_uuid: dropUuid, expected_item: 'oak_log', expected_break_action: 'dig-parent' },
        cancellation, { record: { action_id: 'act_pickup_protocol_' + mode } }),
      error => error.code === 'block.attribution_unavailable')
      check(moves === 0 && strictAttribution.pickupPending.size === 0,
        'actual pickup performs zero pathfinder calls on incompatible protocol: ' + mode)
    } finally { strictAttribution.beginPickup = originalPickup }
  }
  mode = ''
  for (const change of ['durability', 'empty_hand', 'liquid', 'support']) {
    mode = 'hold'; hold = null; fake = bot(); cancellation = { cancelled: false }
    fake.registry = registry
    fake.entity.position = new Vec3(1, 64, 1)
    fake.heldItem = change === 'durability' ? new Item(registry.itemsByName.iron_axe.id, 1) : null
    const overrides = new Map()
    fake.blockAt = position => {
      const name = overrides.get(position.toString()) ||
        (position.equals(new Vec3(1, 64, 3)) ? 'oak_log' : position.y < 64 ? 'stone' : 'air')
      return Block.fromStateId(registry.blocksByName[name].minStateId, 0)
    }
    const collectionParams = { ...params, require_collection: true,
      ...(change === 'durability' ? { expected_tool: 'iron_axe' } : { expected_empty_hand: true }) }
    controller = { record: { action_id: 'act_rt_precondition_' + change } }
    state = await def.start(fake, collectionParams, cancellation, controller)
    state.sessionId = 'mc_product'; state.connectionStillCurrent = () => true
    const pending = def.wait(fake, collectionParams, cancellation, state, controller)
    while (!hold) await new Promise(resolve => setTimeout(resolve, 5))
    if (change === 'durability') fake.heldItem.componentMap.set('damage', { type: 'damage', data: 250 })
    if (change === 'empty_hand') fake.heldItem = new Item(registry.itemsByName.dirt.id, 1)
    if (change === 'liquid') overrides.set(new Vec3(2, 64, 3).toString(), 'lava')
    if (change === 'support') overrides.set(new Vec3(1, 63, 1).toString(), 'air')
    hold()
    await assert.rejects(pending, error => error.code.startsWith('resource.'))
    check(fake.calls === 0 && strictAttribution.pending.size === 0,
      'zero digging after registration changes ' + change)
  }
  console.log('[strict-attribution-test] ' + passed + '/' + passed + ' checks passed')
}
main().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => { api.closeAllConnections(); api.close() })
