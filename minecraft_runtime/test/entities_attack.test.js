'use strict'
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { ACTION_REGISTRY, entitiesView, ATTACK_DEFAULTS } = require('../runtime.js')

let failures = 0
let checks = 0
function assert(c, label) { checks += 1; if (c) console.log('  ✓ ' + label); else { failures += 1; console.error('  ✗ ' + label) } }

function entity(id, name, x, y, z, extra = {}) {
  return { id, name, position: new Vec3(x, y, z), uuid: 'uuid-' + id, ...extra }
}
function botWith(entities) {
  return {
    registry,
    entity: { id: 99, position: new Vec3(0, 64, 0) },
    entities: { 99: { id: 99, name: 'player', position: new Vec3(0, 64, 0) }, ...entities },
    heldItem: { name: 'iron_sword', count: 1, type: registry.itemsByName.iron_sword.id },
    attack(entity) { if (entity.health !== undefined) entity.health -= 6; },
  }
}

async function main() {
  const entities = ACTION_REGISTRY.entities
  const attack = ACTION_REGISTRY.attack
  assert(entities.risk === 'SAFE' && entities.exclusive === false, 'entities is SAFE/non-exclusive')
  assert(attack.risk === 'MEDIUM' && attack.detached === true, 'attack is MEDIUM/detached')
  assert(ATTACK_DEFAULTS.maxDistance === 4, 'attack max distance is 4')

  const zombie = entity(1, 'zombie', 0, 64, 2, { health: 20 })
  const player = entity(2, 'player', 0, 64, 3, { username: 'Other' })
  const item = entity(3, 'item', 0, 64, 1)
  const bot = botWith({ 1: zombie, 2: player, 3: item })
  const view = entitiesView(bot)
  assert(view.total === 2, 'two living entities (self/item excluded)')
  assert(view.entities[0].type === 'zombie' && view.entities[0].distance === 2, 'nearest zombie first')
  assert(view.entities.some((row) => row.is_player === true), 'player flagged')
  assert(view.truncated === false, 'not truncated')

  let rejected = false
  try { attack.start(bot, { entity_id: 2, expected_type: 'player', expected_uuid: 'uuid-2', max_distance: 4 }) } catch (error) { rejected = error.code === 'target.player' }
  assert(rejected, 'player target rejected')

  rejected = false
  try { attack.start(bot, { entity_id: 1, expected_type: 'skeleton', expected_uuid: 'uuid-1', max_distance: 4 }) } catch (error) { rejected = error.code === 'target.type_changed' }
  assert(rejected, 'type mismatch rejected')

  const far = entity(4, 'zombie', 0, 64, 9, { health: 20 })
  bot.entities[4] = far
  rejected = false
  try { attack.start(bot, { entity_id: 4, expected_type: 'zombie', expected_uuid: 'uuid-4', max_distance: 4 }) } catch (error) { rejected = error.code === 'target.too_far' }
  assert(rejected, 'too far rejected')

  const state = attack.start(bot, { entity_id: 1, expected_type: 'zombie', expected_uuid: 'uuid-1', max_distance: 4 })
  const result = await attack.wait(bot, { entity_id: 1, expected_type: 'zombie', expected_uuid: 'uuid-1', max_distance: 4 }, { cancelled: false }, state)
  assert(result.hit === true && result.health_before === 20 && result.health_after === 14, 'one hit reports real health delta')
  assert(result.damage_confirmed === true, 'damage confirmed from re-read')

  if (failures) { console.error('[entities-attack-test] FAIL ' + failures + '/' + checks); process.exit(1) }
  console.log('[entities-attack-test] ALL ' + checks + ' CHECKS PASSED')
  process.exit(0)
}
main().catch((error) => { console.error(error); process.exit(1) })
