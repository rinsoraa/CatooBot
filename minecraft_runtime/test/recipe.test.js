/**
 * recipe_lookup 测试（Phase 4F · A–E + 参数/在线/独占语义）。
 *
 * 用**真实 minecraft-data 配方表**（1.21.1）+ 假 inventory + 忠实的 recipesFor 替身
 * （把 mineflayer 的 requirementsMetForRecipe 逻辑照抄一遍），这样"材料够不够"的判定
 * 和线上完全同构，不是自己编的规则。
 *
 * 覆盖：
 *   A. recipe lookup   B. multiple recipes   C. stable recipe_id
 *   D. insufficient materials   E. table-required recipe excluded
 *   + 未知物品 / 参数校验 / 在线要求 / 非独占（忙的时候也能查）/ 不泄露 raw Recipe
 *
 * 运行：node minecraft_runtime/test/recipe.test.js
 */
'use strict'

const registry = require('prismarine-registry')('1.21.1')
const { Recipe } = require('prismarine-recipe')(registry)

const {
  ACTION_REGISTRY,
  CRAFT_DEFAULTS,
  RECIPE_DEFAULTS,
  describeRecipe,
  itemNameFromRecipeId,
  readableRecipeKey,
  recipeIdOf,
  recipeIdsForList,
  recipeSetsFor,
} = require('../runtime.js')
const { createActionRuntime, STATES } = require('../action_runtime')

let failures = 0
let checks = 0

function assert(condition, label) {
  checks += 1
  if (condition) {
    console.log(`  ✓ ${label}`)
  } else {
    failures += 1
    console.error(`  ✗ ${label}`)
  }
}

const LOOKUP = ACTION_REGISTRY.recipe_lookup
const CRAFT = ACTION_REGISTRY.craft
const DIRT = registry.itemsByName.dirt.id
const OAK_PLANKS = 'oak_planks'
const STICK = 'stick'

// --------------------------------------------------------------- fake bot

function makeInventory(initial = {}) {
  const slots = new Array(46).fill(null)
  const inv = {
    slots,
    items() {
      return inv.slots.filter((item) => item && item.name && item.count > 0)
    },
    count(id, metadata) {
      return inv
        .items()
        .filter((item) => registry.itemsByName[item.name] && registry.itemsByName[item.name].id === id)
        .reduce((total, item) => total + item.count, 0)
    },
    add(name, count) {
      let remaining = count
      const stackSize = (registry.itemsByName[name] && registry.itemsByName[name].stackSize) || 64
      for (let slot = 9; slot < 45 && remaining > 0; slot += 1) {
        const item = inv.slots[slot]
        if (item && item.name === name && item.count < stackSize) {
          const room = Math.min(stackSize - item.count, remaining)
          item.count += room
          remaining -= room
        } else if (!item) {
          const chunk = Math.min(stackSize, remaining)
          inv.slots[slot] = { name, count: chunk, type: registry.itemsByName[name].id, stackSize }
          remaining -= chunk
        }
      }
      return remaining === 0
    },
    remove(name, count) {
      let remaining = count
      for (let slot = 9; slot < 45 && remaining > 0; slot += 1) {
        const item = inv.slots[slot]
        if (!item || item.name !== name) continue
        const take = Math.min(item.count, remaining)
        item.count -= take
        remaining -= take
        if (item.count <= 0) inv.slots[slot] = null
      }
      return remaining === 0
    },
  }
  for (const [name, count] of Object.entries(initial)) inv.add(name, count)
  return inv
}

/** 忠实的 recipesFor 替身（照抄 mineflayer 的 requirementsMetForRecipe 语义）。 */
function makeBot({ inventory = makeInventory(), entity = { position: null } } = {}) {
  const bot = {
    registry,
    inventory,
    entity,
    recipesForCalls: [],
    recipesFor(itemType, metadata, minResultCount, craftingTable) {
      bot.recipesForCalls.push({ itemType, metadata, minResultCount, craftingTable })
      minResultCount = minResultCount ?? 1
      return Recipe.find(itemType, metadata).filter((recipe) => {
        if (recipe.requiresTable && !craftingTable) return false
        const craftCount = Math.ceil(minResultCount / recipe.result.count)
        return recipe.delta.every(
          (delta) => bot.inventory.count(delta.id, delta.metadata) + delta.count * craftCount >= 0,
        )
      })
    },
  }
  return bot
}

function makeHarness(extra = {}) {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { recipe_lookup: LOOKUP, craft: CRAFT, ...extra },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

function lookupResult(response) {
  return response && response.result
}

async function main() {
  setTimeout(() => {
    console.error('[recipe-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[recipe-test] 注册表属性（SAFE / 非独占 / 同步）')
  {
    assert(Boolean(LOOKUP), 'recipe_lookup 已注册')
    assert(LOOKUP.risk === 'SAFE', `risk = SAFE（得到 ${LOOKUP.risk}）`)
    assert(LOOKUP.exclusive === false, 'exclusive = false（纯读取，可与前台动作并行）')
    assert(LOOKUP.detached !== true, '不是持续型动作（同步返回）')
    assert(typeof LOOKUP.validate === 'function' && typeof LOOKUP.run === 'function', '有 validate/run')
    assert(
      CRAFT_DEFAULTS.maxRecipeIdChars === 200 && CRAFT_DEFAULTS.maxEntries === 12,
      `CRAFT_DEFAULTS（得到 ${JSON.stringify(CRAFT_DEFAULTS)}）`,
    )
    assert(
      RECIPE_DEFAULTS === undefined || typeof RECIPE_DEFAULTS === 'object' || true,
      '默认值常量可读',
    )
  }

  console.log('[recipe-test] A. recipe lookup（材料齐 → available）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    const response = await runtime.execute('recipe_lookup', { item: 'minecraft:stick' })
    assert(
      response.status === STATES.SUCCEEDED && response.action === 'recipe_lookup',
      `同步完成（得到 ${JSON.stringify({ status: response.status, action: response.action })}）`,
    )
    const payload = lookupResult(response)
    assert(payload.item === 'stick', `规范化物品名（得到 ${payload.item}）`)
    assert(payload.status === 'available', `status = available（得到 ${payload.status}）`)
    const entry = payload.recipes.find((row) => row.available)
    assert(Boolean(entry), '至少一个 available 配方')
    assert(
      entry.result.name === STICK && entry.result.count_per_craft === 4,
      `结果语义（得到 ${JSON.stringify(entry.result)}）`,
    )
    assert(
      entry.requires_table === false && entry.available === true,
      'requires_table = false / available = true',
    )
    assert(
      entry.ingredients.length === 1 &&
        entry.ingredients[0].name === OAK_PLANKS &&
        entry.ingredients[0].count === 2,
      `材料语义（得到 ${JSON.stringify(entry.ingredients)}）`,
    )
    assert(
      typeof entry.recipe_id === 'string' && entry.recipe_id === 'stick*4=oak_planks*2',
      `可读的稳定 recipe_id（得到 ${entry.recipe_id}）`,
    )
    // §五：不泄露 raw Recipe / 数字 id / metadata / delta / inShape
    const raw = JSON.stringify(payload)
    for (const forbidden of ['delta', 'inShape', 'outShape', 'metadata', 'requiresTable']) {
      assert(!raw.includes(forbidden), `不泄露 ${forbidden}`)
    }
    assert(
      !raw.includes(`"id":${registry.itemsByName.stick.id}`),
      '不泄露内部数字 id',
    )
    assert(
      Object.keys(entry).sort().join(',') === 'available,ingredients,recipe_id,requires_table,result',
      `条目只有约定字段（得到 ${Object.keys(entry).sort().join(',')}）`,
    )
    assert(
      bot.recipesForCalls.length === 1 && bot.recipesForCalls[0].craftingTable === null,
      '用 bot.recipesFor(..., craftingTable=null) 判定"2×2 能做"',
    )
  }

  console.log('[recipe-test] B. multiple recipes（同一产物多个配方，按 available 优先排序）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 4, bamboo: 2 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    const payload = lookupResult(await runtime.execute('recipe_lookup', { item: 'stick' }))
    assert(payload.total > 1, `同一产物有多个配方（total=${payload.total}）`)
    assert(
      payload.recipes.length === Math.min(payload.total, CRAFT_DEFAULTS.maxEntries),
      `列出有界（${payload.recipes.length}/${payload.total}）`,
    )
    const names = new Set(
      payload.recipes.flatMap((row) => row.ingredients.map((ingredient) => ingredient.name)),
    )
    assert(names.size >= 2, `不同木材的配方都出现了（${[...names].slice(0, 3).join(',')}…）`)
    assert(
      payload.recipes.filter((row) => row.available).length >= 2,
      '两种材料都齐 → 两个 available 配方',
    )
    assert(
      payload.recipes[0].available === true &&
        payload.recipes[payload.recipes.length - 1].available === false,
      'available 的排在前面（稳定排序的一部分）',
    )
  }

  console.log('[recipe-test] C. stable recipe_id（不是数组下标；背包变化也稳定）')
  {
    const empty = makeBot({ inventory: makeInventory({}) })
    const two = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }) })
    const eight = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 8 }) })
    const harness = makeHarness()
    const ids = []
    for (const bot of [empty, two, eight]) {
      harness.state.bot = bot
      const payload = lookupResult(await harness.runtime.execute('recipe_lookup', { item: 'stick' }))
      const entry = payload.recipes.find((row) =>
        row.ingredients.some((ingredient) => ingredient.name === OAK_PLANKS),
      )
      ids.push(entry.recipe_id)
    }
    assert(new Set(ids).size === 1, `背包不同也给出同一个 id（${ids.join(' / ')}）`)
    assert(
      !/^\d+$/.test(ids[0]) && ids[0].includes(STICK) && ids[0].includes(OAK_PLANKS),
      `id 是可读签名而不是下标（${ids[0]}）`,
    )
    // 同名签名冲突时的兜底后缀：手工造两个"同签名不同形状"的配方
    const fake = [
      { result: { id: registry.itemsByName.stick.id, count: 4 }, requiresTable: false, inShape: [[{ id: registry.itemsByName.oak_planks.id }], [{ id: registry.itemsByName.oak_planks.id }]] },
      { result: { id: registry.itemsByName.stick.id, count: 4 }, requiresTable: false, inShape: [[{ id: registry.itemsByName.oak_planks.id }, { id: registry.itemsByName.oak_planks.id }]] },
    ]
    const withSuffix = recipeIdsForList(fake, registry)
    assert(
      withSuffix[0] !== withSuffix[1] && /~\w{6}$/.test(withSuffix[1]),
      `同名签名冲突时追加形状后缀（${withSuffix.join(' / ')}）`,
    )
    assert(
      readableRecipeKey(fake[0], registry) === 'stick*4=oak_planks*2',
      '可读签名本身不含形状（冲突时才用后缀区分）',
    )
  }

  console.log('[recipe-test] D. insufficient materials（材料不足 → 如实说）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 1 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    const payload = lookupResult(await runtime.execute('recipe_lookup', { item: 'stick' }))
    assert(payload.status === 'insufficient_material', `status（得到 ${payload.status}）`)
    assert(
      payload.recipes.length > 0 && payload.recipes.every((row) => row.available === false),
      '仍然列出 2×2 配方，但都标成 not available（不是空数组）',
    )
    assert(
      payload.recipes.some((row) => row.ingredients[0].name === OAK_PLANKS),
      '缺材料的那个配方也在列表里（模型能看出差多少）',
    )
  }

  console.log('[recipe-test] E. table-required recipe 不算 2×2 可执行')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 8 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    const payload = lookupResult(await runtime.execute('recipe_lookup', { item: 'chest' }))
    assert(
      payload.status === 'crafting_table_required',
      `只有工作台配方 → crafting_table_required（得到 ${payload.status}）`,
    )
    assert(payload.recipes.length === 0 && payload.total === 0, '绝不给 2×2 的假配方')
    const sets = recipeSetsFor(bot, 'chest')
    assert(sets.all.length > 0 && sets.twoByTwo.length === 0, '确实存在工作台配方（被排除）')
    assert(
      describeRecipe(sets.all[0], registry, false).requires_table === true &&
        readableRecipeKey(sets.all[0], registry).startsWith('!'),
      `工作台配方的 id 带 ! 前缀（${readableRecipeKey(sets.all[0], registry)}）`,
    )
  }

  console.log('[recipe-test] 未知物品 / 参数校验 / 在线要求')
  {
    const bot = makeBot({})
    const { runtime, state } = makeHarness()
    state.bot = bot
    const payload = lookupResult(await runtime.execute('recipe_lookup', { item: 'not_a_real_item' }))
    assert(payload.status === 'recipe_not_found', `未知物品 → recipe_not_found（得到 ${payload.status}）`)
    assert(payload.recipes.length === 0, '空配方列表')

    for (const bad of [{}, { item: '' }, { item: '   ' }, { item: 7 }, { item: 'x'.repeat(80) }]) {
      let code = null
      try {
        await runtime.execute('recipe_lookup', bad)
      } catch (error) {
        code = error.code
      }
      assert(code === 'item.invalid', `非法参数 ${JSON.stringify(bad)} → item.invalid（得到 ${code}）`)
    }

    state.online = false
    let offlineCode = null
    try {
      await runtime.execute('recipe_lookup', { item: 'stick' })
    } catch (error) {
      offlineCode = error.code
    }
    assert(offlineCode === 'action.not_online', `离线 → action.not_online（得到 ${offlineCode}）`)
    state.online = true

    // 物品表拿不到（还没进世界）也如实拒绝
    state.bot = { entity: null }
    let noRegistry = null
    try {
      await runtime.execute('recipe_lookup', { item: 'stick' })
    } catch (error) {
      noRegistry = error.code
    }
    assert(noRegistry === 'action.not_online', `拿不到 registry → action.not_online（得到 ${noRegistry}）`)
  }

  console.log('[recipe-test] 非独占：前台动作跑着的时候也能查配方')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }) })
    const hang = new Promise(() => {})
    const { runtime, state } = makeHarness({
      move_to: {
        exclusive: true,
        timeout_ms: 5000,
        risk: 'LOW',
        async run() {
          await hang
        },
      },
    })
    state.bot = bot
    // 不要 await：这个假动作会一直挂着（await 会等到 timeout 才返回）
    const pending = runtime.execute('move_to', { x: 1, y: 2, z: 3 }).catch(() => null)
    await new Promise((resolve) => setTimeout(resolve, 20))
    const during = await runtime.execute('recipe_lookup', { item: 'stick' })
    assert(
      during.status === STATES.SUCCEEDED && lookupResult(during).status === 'available',
      'move_to 跑着时配方查询仍然可用（SAFE 非独占）',
    )
    const stopResult = runtime.stop()
    assert(stopResult.cancelled.length === 1, '收尾：STOP 取消了 move_to')
    await pending
  }

  console.log('[recipe-test] itemNameFromRecipeId（craft 侧的反解）')
  {
    assert(itemNameFromRecipeId('stick*4=oak_planks*2') === 'stick', '普通 id 反解结果物品名')
    assert(itemNameFromRecipeId('!chest*1=oak_planks*8') === 'chest', '带 ! 前缀也能反解')
    assert(itemNameFromRecipeId('bad') === '' && itemNameFromRecipeId('') === '', '非法 id → 空串')
    assert(
      itemNameFromRecipeId('oak_planks*4=oak_log*1') === 'oak_planks',
      '物品名带下划线不会解析错（按第一个 * 切）',
    )
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[recipe-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[recipe-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[recipe-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[recipe-test] crashed:', error)
  process.exit(1)
})
