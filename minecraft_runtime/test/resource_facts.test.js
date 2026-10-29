'use strict'

const assert = require('assert/strict')
const registry = require('prismarine-registry')('1.21.1')
const Item = require('prismarine-item')(registry)
const Block = require('prismarine-block')(registry)
const { itemCollectionFacts } = require('../resource_facts')

let checks = 0
function check(condition, message) { checks += 1; assert(condition, message) }
function item(name) { return new Item(registry.itemsByName[name].id, 1) }

const pickaxe = item('wooden_pickaxe')
const fresh = itemCollectionFacts(pickaxe, registry)
check(fresh.known && fresh.damageable, 'actual registry item is a damageable tool')
check(fresh.durability_known && fresh.remaining_uses === 59, 'fresh wooden pickaxe has 59 uses')
check(fresh.enchantments_known && fresh.has_enchantments === false, 'actual empty enchantment getter is known')
pickaxe.componentMap.set('damage', { type: 'damage', data: 58 })
check(itemCollectionFacts(pickaxe, registry).remaining_uses === 1, '1.21.1 damage component leaves one use')
pickaxe.componentMap.set('damage', { type: 'damage', data: 59 })
check(itemCollectionFacts(pickaxe, registry).remaining_uses === 0, 'exhausted tool is not silently fresh')
pickaxe.componentMap.set('damage', { type: 'damage', data: -1 })
check(!itemCollectionFacts(pickaxe, registry).durability_known, 'invalid negative damage remains unknown')
pickaxe.componentMap.set('damage', { type: 'damage', data: 60 })
check(!itemCollectionFacts(pickaxe, registry).durability_known, 'damage beyond max remains unknown')
const broken = item('stone_pickaxe')
Object.defineProperty(broken, 'durabilityUsed', { get() { throw new Error('unknown item component') } })
check(!itemCollectionFacts(broken, registry).durability_known, 'failed getter never supplies full durability')
const overridden = item('stone_pickaxe')
overridden.componentMap.set('max_damage', { type: 'max_damage', data: 1 })
const custom = itemCollectionFacts(overridden, registry)
check(!custom.durability_known && custom.unsupported_components.includes('max_damage'), 'custom max damage is explicitly unsupported')
const enchanted = item('iron_pickaxe')
enchanted.componentMap.set('enchantments', { type: 'enchantments', data: [{ name: 'silk_touch', lvl: 1 }] })
const enchantFacts = itemCollectionFacts(enchanted, registry)
check(enchantFacts.enchantments_known && enchantFacts.has_enchantments, 'drop-altering enchantment is not hidden')
const removed = item('wooden_pickaxe')
removed.removedComponents.push('tool')
check(itemCollectionFacts(removed, registry).unsupported_components.includes('removed_components'), 'removed vanilla defaults are unsupported')
check(!itemCollectionFacts(item('wooden_pickaxe'), null).known, 'missing registry is unknown')
check(itemCollectionFacts(null, registry).known && !itemCollectionFacts(null, registry).damageable, 'empty hand is a known distinct state')
check(itemCollectionFacts(item('dirt'), registry).damageable === false, 'non-tool is not given fake durability')
check(!JSON.stringify(fresh).includes('nbt') && !JSON.stringify(fresh).includes('lore'), 'raw private item text is not projected')
function harvestable(blockName, toolName = null) {
  const block = Block.fromStateId(registry.blocksByName[blockName].minStateId, 0)
  return block.canHarvest(toolName ? registry.itemsByName[toolName].id : null) === true
}
check(harvestable('stone', 'wooden_pickaxe'), 'actual 1.21.1 stone accepts wooden pickaxe')
check(!harvestable('stone', 'wooden_shovel'), 'actual 1.21.1 stone rejects shovel for drops')
check(!harvestable('iron_ore', 'wooden_pickaxe'), 'actual 1.21.1 iron ore rejects wooden pickaxe')
check(harvestable('iron_ore', 'stone_pickaxe'), 'actual 1.21.1 iron ore accepts stone pickaxe')
check(harvestable('coal_ore', 'wooden_pickaxe'), 'actual 1.21.1 coal accepts wooden pickaxe')
for (const name of ['oak_log', 'spruce_log', 'birch_log', 'jungle_log', 'acacia_log', 'dark_oak_log']) {
  check(harvestable(name), `actual 1.21.1 ${name} allows hand harvesting`)
}
console.log(`[resource-facts-test] ${checks}/${checks} checks passed (real 1.21.1 library items; no server claim)`)
