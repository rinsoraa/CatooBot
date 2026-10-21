'use strict'

const assert = require('assert/strict')
const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const Block = require('prismarine-block')(registry)
const Item = require('prismarine-item')(registry)
const { collectionSafety, collectionReadiness } = require('../resource_facts')
const target = new Vec3(1, 64, 3)
let checks = 0
function check(condition, message) { assert(condition, message); checks++; console.log('  OK   ' + message) }
function rig(name = 'oak_log') {
  const overrides = new Map()
  const block = name => Block.fromStateId(registry.blocksByName[name].minStateId, 0)
  const bot = {
    registry, heldItem: null, entity: { position: new Vec3(1, 64, 1) },
    blockAt(pos) {
      const key = `${pos.x},${pos.y},${pos.z}`
      return overrides.has(key) ? overrides.get(key) : block(pos.equals(target) ? name : pos.y < 64 ? 'stone' : 'air')
    },
  }
  return { bot, set(pos, name) { overrides.set(`${pos.x},${pos.y},${pos.z}`, name ? block(name) : null) } }
}
let scene = rig()
check(collectionReadiness(scene.bot, target, { expectedEmptyHand: true }).allowed, 'loaded flat log target permits explicitly frozen empty hand')
check(!collectionReadiness(scene.bot, target).allowed, 'omitted hand constraint does not freeze empty hand')
scene.bot.heldItem = new Item(registry.itemsByName.dirt.id, 1)
check(!collectionReadiness(scene.bot, target, { expectedEmptyHand: true }).allowed, 'hand changed after planning is refused')
for (const name of ['stone', 'cobblestone', 'coal_ore', 'iron_ore']) {
  scene = rig(name)
  check(!collectionReadiness(scene.bot, target, { expectedEmptyHand: true }).allowed, name + ' cannot be harvested with bare hands')
  const tool = name === 'iron_ore' ? 'stone_pickaxe' : 'wooden_pickaxe'
  scene.bot.heldItem = new Item(registry.itemsByName[tool].id, 1)
  check(collectionReadiness(scene.bot, target, { expectedTool: tool }).allowed, name + ' accepts actual registry harvesting level')
  scene.bot.heldItem.componentMap.set('damage', { type: 'damage', data: registry.itemsByName[tool].maxDurability })
  check(!collectionReadiness(scene.bot, target, { expectedTool: tool }).allowed, name + ' refuses exhausted tool at dispatch')
}
scene = rig('oak_log')
scene.bot.heldItem = new Item(registry.itemsByName.iron_axe.id, 1)
scene.bot.heldItem.componentMap.set('enchantments', { type: 'enchantments', data: [{ name: 'silk_touch', lvl: 1 }] })
check(!collectionReadiness(scene.bot, target, { expectedTool: 'iron_axe' }).allowed, 'changed enchantment profile is unsupported')
scene = rig('diamond_ore')
check(!collectionReadiness(scene.bot, target, { expectedEmptyHand: true }).allowed, 'uncontracted resource cannot execute through collection flag')
for (const name of ['lava', 'water', 'powder_snow', 'magma_block']) {
  scene = rig(); scene.set(target.offset(1, 0, 0), name)
  check(!collectionSafety(scene.bot, target).allowed, 'adjacent ' + name + ' refuses local collection')
}
scene = rig(); scene.set(target.offset(0, 1, 0), 'sand')
check(collectionSafety(scene.bot, target).reason === 'resource.overhead_gravity', 'overhead gravity block is refused')
scene = rig(); scene.set(target.offset(1, 0, 0), null)
check(!collectionSafety(scene.bot, target).known, 'unloaded neighborhood remains unknown')
scene = rig(); scene.set(target.offset(1, -1, 0), 'air')
check(!collectionSafety(scene.bot, target).allowed, 'adjacent pickup floor hole is refused')
scene = rig(); scene.set(scene.bot.entity.position.floored().offset(0, -1, 0), 'air')
check(!collectionSafety(scene.bot, target).allowed, 'missing player support is refused')
scene = rig()
check(collectionSafety(scene.bot, target.offset(0, -1, 0)).reason === 'resource.below_feet', 'mining below player feet is refused')
console.log(`[collection-readiness-test] ${checks}/${checks} checks passed (no live server)`)
