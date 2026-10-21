'use strict'

/** R3: bounded vanilla item facts. No raw NBT/components or tool recommendations. */
function itemCollectionFacts(item, registry) {
  if (item === null || item === undefined) {
    return { known: true, name: null, damageable: false, durability_known: true,
      remaining_uses: null, enchantments_known: true, has_enchantments: false,
      components_known: true, unsupported_components: [] }
  }
  const metadata = Number.isInteger(item.type) && registry && registry.items
    ? registry.items[item.type] : null
  const known = Boolean(metadata && metadata.name === item.name)
  const map = item.componentMap
  const removed = item.removedComponents
  const componentsKnown = map instanceof Map && Array.isArray(removed) && map.size <= 32 && removed.length <= 32
  const harmless = new Set(['damage', 'custom_name', 'lore', 'repair_cost'])
  const unsupported = componentsKnown
    ? [...map.keys()].filter((key) => !harmless.has(key)).slice(0, 8).map((key) => String(key).slice(0, 64))
    : ['components_unknown']
  if (componentsKnown && removed.length) unsupported.push('removed_components')
  const max = known ? metadata.maxDurability : null
  const damageable = Number.isInteger(max) && max > 0
  let remaining = null
  let durabilityKnown = known && !damageable && componentsKnown
  if (damageable && componentsKnown && !map.has('max_damage')) {
    try {
      const used = item.durabilityUsed
      if (Number.isInteger(used) && used >= 0 && used <= max) {
        remaining = max - used
        durabilityKnown = true
      }
    } catch (_) { /* unknown */ }
  }
  let enchantmentsKnown = false
  let hasEnchantments = null
  try {
    const enchants = item.enchants
    if (Array.isArray(enchants) && enchants.length <= 32) {
      enchantmentsKnown = true
      hasEnchantments = enchants.length > 0
    }
  } catch (_) { /* unknown */ }
  return {
    known, name: known ? metadata.name : null, damageable,
    durability_known: durabilityKnown, remaining_uses: remaining,
    enchantments_known: enchantmentsKnown, has_enchantments: hasEnchantments,
    components_known: componentsKnown, unsupported_components: unsupported,
  }
}

const COLLECTION_DROPS = Object.freeze({
  oak_log: 'oak_log', spruce_log: 'spruce_log', birch_log: 'birch_log', jungle_log: 'jungle_log',
  acacia_log: 'acacia_log', dark_oak_log: 'dark_oak_log', stone: 'cobblestone',
  cobblestone: 'cobblestone', coal_ore: 'coal', iron_ore: 'raw_iron', dirt: 'dirt', sand: 'sand',
})
const COLLECTION_BLOCKS = new Set(Object.keys(COLLECTION_DROPS))
const HAZARDS = new Set(['water', 'lava', 'bubble_column', 'fire', 'soul_fire', 'cactus',
  'magma_block', 'campfire', 'soul_campfire', 'powder_snow', 'sweet_berry_bush', 'pointed_dripstone'])
const FALLING = new Set(['sand', 'red_sand', 'gravel', 'anvil', 'chipped_anvil', 'damaged_anvil',
  'white_concrete_powder', 'orange_concrete_powder', 'magenta_concrete_powder', 'light_blue_concrete_powder',
  'yellow_concrete_powder', 'lime_concrete_powder', 'pink_concrete_powder', 'gray_concrete_powder',
  'light_gray_concrete_powder', 'cyan_concrete_powder', 'purple_concrete_powder', 'blue_concrete_powder',
  'brown_concrete_powder', 'green_concrete_powder', 'red_concrete_powder', 'black_concrete_powder'])

/** Current local facts only. Unknown terrain never establishes a safe collection precondition. */
function collectionSafety(bot, position) {
  const fail = (reason, known = true) => ({ known, allowed: false, reason })
  if (!bot || !bot.entity || !bot.entity.position || typeof bot.blockAt !== 'function') {
    return fail('resource.safety_unknown', false)
  }
  try {
    const feet = bot.entity.position.floored()
    if (position.y < feet.y) return fail('resource.below_feet')
    const support = bot.blockAt(feet.offset(0, -1, 0))
    if (!support || !support.name) return fail('resource.support_unknown', false)
    if (support.boundingBox !== 'block' || HAZARDS.has(support.name)) return fail('resource.support_unsafe')
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 2; dy++) {
        for (let dz = -1; dz <= 1; dz++) {
          if (dx === 0 && dy === 0 && dz === 0) continue
          const block = bot.blockAt(position.offset(dx, dy, dz))
          if (!block || !block.name) return fail('resource.neighborhood_unknown', false)
          if (HAZARDS.has(block.name)) return fail('resource.neighborhood_hazard')
          if (dy > 0 && dx === 0 && dz === 0 && FALLING.has(block.name)) {
            return fail('resource.overhead_gravity')
          }
          // Pickup needs a stable floor adjacent to the removed target, rather
          // than a hole or a liquid surface which navigation might drop into.
          if (dy === -1) {
            const floor = bot.blockAt(position.offset(dx, feet.y - 1 - position.y, dz))
            if (!floor || !floor.name) return fail('resource.target_floor_unknown', false)
            if (floor.boundingBox !== 'block' || HAZARDS.has(floor.name)) return fail('resource.target_floor_unsafe')
          }
        }
      }
    }
    return { known: true, allowed: true, reason: 'resource.local_safety_checked' }
  } catch (_) { return fail('resource.safety_unknown', false) }
}

function collectionReadiness(bot, position, { expectedTool = null, expectedEmptyHand = false } = {}) {
  const fail = reason => ({ allowed: false, reason })
  try {
    const block = bot.blockAt(position)
    if (!block || !COLLECTION_BLOCKS.has(block.name)) return fail('resource.unsupported')
    const held = bot.heldItem || null
    const name = held ? held.name : null
    if ((!expectedTool && !expectedEmptyHand) ||
        (expectedEmptyHand && held) || (expectedTool && expectedTool !== name)) {
      return fail('resource.frozen_hand_changed_or_missing')
    }
    const tool = itemCollectionFacts(held, bot.registry)
    if (!tool.known || !tool.components_known || tool.unsupported_components.length ||
        !tool.enchantments_known || tool.has_enchantments !== false) return fail('resource.tool_profile_unknown_or_unsupported')
    if (held && (!Number.isSafeInteger(held.count) || held.count < 1)) return fail('resource.tool_missing')
    if (tool.damageable && (!tool.durability_known || tool.remaining_uses < 1)) {
      return fail('resource.tool_durability_insufficient_or_unknown')
    }
    if (typeof block.canHarvest !== 'function' || block.canHarvest(held ? held.type : null) !== true) {
      return fail('resource.tool_cannot_harvest')
    }
    return collectionSafety(bot, position)
  } catch (_) { return fail('resource.precondition_unknown') }
}

module.exports = { itemCollectionFacts, collectionSafety, collectionReadiness, COLLECTION_DROPS }
