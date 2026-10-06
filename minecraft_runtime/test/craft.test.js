/**
 * craft 测试（Phase 4F · F–P）。
 *
 * 用**真实 minecraft-data 配方表**（1.21.1）+ 假 inventory + 按 recipe 语义模拟的
 * 假 `bot.craft`（真的消耗材料、真的产出产物；也可以故意"什么都不做"或抛错）。
 * 这样"重读 inventory 复核"这条硬门禁是被真实地测到的，而不是自己编的结论。
 *
 * 覆盖：
 *   F. success   G. output reread   H. ingredient reread   I. craft_unconfirmed
 *   J. recipe changed   K. insufficient materials   L. cancellation   M. timeout
 *   N. race   O. cleanup exactly once   P. action.busy
 *   + validate / offline / 禁止 recipe chain（§四十三）/ 不泄露 raw Recipe
 *
 * 运行：node minecraft_runtime/test/craft.test.js
 */
'use strict'

const registry = require('prismarine-registry')('1.21.1')
const { Recipe } = require('prismarine-recipe')(registry)

const {
  ACTION_REGISTRY,
  describeRecipeIngredients,
  describeRecipeResult,
  readableRecipeKey,
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

const CRAFT = ACTION_REGISTRY.craft
const OAK_PLANKS = 'oak_planks'
const STICK = 'stick'
const STICK_OAK_ID = 'stick*4=oak_planks*2'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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
        .filter(
          (item) => registry.itemsByName[item.name] && registry.itemsByName[item.name].id === id,
        )
        .reduce((total, item) => total + item.count, 0)
    },
    total(name) {
      return inv
        .items()
        .filter((item) => item.name === name)
        .reduce((sum, item) => sum + item.count, 0)
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

/** 按配方语义真实改变假 inventory（= 真 crafting 的效果）。 */
function applyRecipe(bot, recipe) {
  const result = describeRecipeResult(recipe, registry)
  for (const entry of describeRecipeIngredients(recipe, registry)) {
    if (!bot.inventory.remove(entry.name, entry.count)) {
      throw new Error(`missing ${entry.name}`)
    }
  }
  bot.inventory.add(result.name, result.count_per_craft)
}

function makeBot({ inventory = makeInventory(), craftImpl = null, entity = { position: null } } = {}) {
  const bot = {
    registry,
    inventory,
    entity,
    craftCalls: [],
    cleanedStates: 0,
    recipesFor(itemType, metadata, minResultCount, craftingTable) {
      minResultCount = minResultCount ?? 1
      return Recipe.find(itemType, metadata).filter((recipe) => {
        if (recipe.requiresTable && !craftingTable) return false
        const craftCount = Math.ceil(minResultCount / recipe.result.count)
        return recipe.delta.every(
          (delta) => bot.inventory.count(delta.id, delta.metadata) + delta.count * craftCount >= 0,
        )
      })
    },
    async craft(recipe, count, craftingTable) {
      bot.craftCalls.push({ recipe, count, craftingTable })
      if (craftImpl) return craftImpl(bot, recipe, count, craftingTable)
      applyRecipe(bot, recipe)
    },
    clearControlStates() {
      bot.cleanedStates += 1
    },
  }
  return bot
}

/**
 * 包一层 cleanup 计数器：ActionRuntime 的"cleanup 至多一次"是对**动作定义**的保证，
 * 而 stop() 自己还会调一次全局 clearControlStates（那是控制面兜底，不算动作 cleanup）——
 * 所以要分开数，不能靠 bot.clearControlStates 的调用次数。
 */
function withCleanupCounter(definition = CRAFT) {
  const counter = { calls: 0 }
  const wrapped = {
    ...definition,
    cleanup(bot, controller) {
      counter.calls += 1
      return definition.cleanup(bot, controller)
    },
  }
  return { definition: wrapped, counter }
}

function makeHarness(options = {}) {
  const definition = options.craft || CRAFT
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { craft: definition, ...(options.extra || {}) },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

function terminals(events, actionId) {
  return events.filter(
    (row) =>
      ['minecraft.action.completed', 'minecraft.action.failed', 'minecraft.action.cancelled',
        'minecraft.action.timeout'].includes(row.event) &&
      (!actionId || row.data.action_id === actionId),
  )
}

function completedResult(events, actionId) {
  const row = events.find(
    (entry) => entry.event === 'minecraft.action.completed' && entry.data.action_id === actionId,
  )
  return row ? row.data.result : null
}

async function settle(harness, actionId, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const rows = terminals(harness.events, actionId)
    if (rows.length > 0) return rows[0]
    await sleep(10)
  }
  return null
}

async function expectCode(promise, code, label) {
  try {
    await promise
  } catch (error) {
    assert(
      error && error.code === code,
      `${label} → ${code}（得到 ${error && (error.code || error.message)}）`,
    )
    return error
  }
  assert(false, `${label} → 期望 ${code}，但成功返回了`)
  return null
}

/** 工作台配方在真实数据里的 id（不硬编码，现场从配方表算）。 */
function tableRecipeId(itemName) {
  const entry = registry.itemsByName[itemName]
  const recipe = Recipe.find(entry.id, null).find((row) => row.requiresTable)
  return recipe ? readableRecipeKey(recipe, registry) : ''
}

async function main() {
  setTimeout(() => {
    console.error('[craft-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[craft-test] 注册表属性与参数校验')
  {
    assert(Boolean(CRAFT), 'craft 已注册')
    assert(CRAFT.exclusive === true, 'exclusive = true（合成期间 inventory 在变）')
    assert(CRAFT.risk === 'MEDIUM', `risk = MEDIUM（得到 ${CRAFT.risk}）`)
    assert(CRAFT.detached === true, 'detached = true（启动即 RUNNING）')
    assert(typeof CRAFT.validate === 'function' && typeof CRAFT.start === 'function', '有 validate/start/wait')
    assert(typeof CRAFT.cleanup === 'function', '有 cleanup')

    const ok = CRAFT.validate({ recipe_id: ' stick*4=oak_planks*2 ' })
    assert(ok.recipe_id === 'stick*4=oak_planks*2', `合法 recipe_id 归一化（得到 ${ok.recipe_id}）`)
    for (const bad of [{}, { recipe_id: '' }, { recipe_id: '   ' }, { recipe_id: 7 }, { recipe_id: 'x'.repeat(220) }]) {
      let code = null
      try {
        CRAFT.validate(bad)
      } catch (error) {
        code = error.code
      }
      assert(code === 'recipe.invalid', `非法参数 ${JSON.stringify(bad).slice(0, 40)} → recipe.invalid（得到 ${code}）`)
    }
  }

  console.log('[craft-test] F/G/H. success：产物增加 + 材料消耗（都来自重读）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }) })
    const { runtime, events, state } = makeHarness()
    state.bot = bot
    const started = await runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    assert(
      started.status === STATES.RUNNING && Boolean(started.action_id),
      `启动即 RUNNING + action_id（得到 ${JSON.stringify(started)}）`,
    )
    const terminal = await settle(harnessOf(events, started.action_id), started.action_id)
    const result = completedResult(events, started.action_id)
    assert(terminal && terminal.event === 'minecraft.action.completed', `终态 completed（得到 ${terminal && terminal.event}）`)
    assert(result && result.recipe_id === STICK_OAK_ID, 'result.recipe_id 原样返回')
    assert(
      result.item === STICK &&
        result.result.name === STICK &&
        result.result.count_per_craft === 4 &&
        result.result.crafted_count === 4,
      `F. result 语义（得到 ${JSON.stringify(result.result)}）`,
    )
    assert(
      result.ingredients.length === 1 &&
        result.ingredients[0].name === OAK_PLANKS &&
        result.ingredients[0].consumed === 2,
      `H. 材料消耗来自重读（得到 ${JSON.stringify(result.ingredients)}）`,
    )
    assert(
      result.before.result_count === 0 && result.after.result_count === 4,
      `G. 产物 before/after（得到 ${result.before.result_count} → ${result.after.result_count}）`,
    )
    assert(
      result.before.ingredient_counts[OAK_PLANKS] === 2 &&
        result.after.ingredient_counts[OAK_PLANKS] === 0,
      `H. 材料 before/after（得到 ${JSON.stringify(result.before.ingredient_counts)} → ${JSON.stringify(result.after.ingredient_counts)}）`,
    )
    // 独立核验：真实（假）inventory 确实是这个状态
    assert(bot.inventory.total(STICK) === 4 && bot.inventory.total(OAK_PLANKS) === 0, 'F. 底层 inventory 真的变了')
    assert(
      bot.craftCalls.length === 1 && bot.craftCalls[0].count === 1 && bot.craftCalls[0].craftingTable === null,
      '只调一次 bot.craft(recipe, 1, null)（2×2，不传工作台）',
    )
    const raw = JSON.stringify(result)
    for (const forbidden of ['inShape', 'delta', 'metadata', 'requiresTable']) {
      assert(!raw.includes(forbidden), `结果里不泄露 raw Recipe 字段 ${forbidden}`)
    }
  }

  console.log('[craft-test] I. craft_unconfirmed：resolve 了但世界没变 / 只消耗没产出')
  {
    const noop = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }), craftImpl: async () => {} })
    const harness = makeHarness()
    harness.state.bot = noop
    const started = await harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    const terminal = await settle(harness, started.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.failed' && terminal.data.code === 'craft.unconfirmed',
      `I. 没变化 → craft.unconfirmed（得到 ${terminal && terminal.event}/${terminal && terminal.data.code}）`,
    )
    assert(
      terminal.data.error.includes('产物 +0'),
      `I. 错误信息带真实数字（得到 ${terminal.data.error}）`,
    )

    const partial = makeBot({
      inventory: makeInventory({ [OAK_PLANKS]: 2 }),
      craftImpl: async (bot, recipe) => {
        // 只消耗材料、不产出（模拟 inventory packet 延迟/异常）
        for (const entry of describeRecipeIngredients(recipe, registry)) {
          bot.inventory.remove(entry.name, entry.count)
        }
      },
    })
    const harness2 = makeHarness()
    harness2.state.bot = partial
    const started2 = await harness2.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    const terminal2 = await settle(harness2, started2.action_id)
    assert(
      terminal2 && terminal2.data.code === 'craft.unconfirmed',
      `I. 只消耗没产出 → craft.unconfirmed（得到 ${terminal2 && terminal2.data.code}）`,
    )
  }

  console.log('[craft-test] J. recipe changed / unavailable / not found')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 8 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    // 签名对不上（材料数量被改过）→ recipe.changed
    await expectCode(
      runtime.execute('craft', { recipe_id: 'stick*4=oak_planks*3' }),
      'recipe.changed',
      'J. 签名变了',
    )
    // 工作台配方 → recipe.unavailable（绝不去找/开工作台）
    const tableId = tableRecipeId('chest')
    assert(tableId.startsWith('!'), `工作台配方 id 带 ! 前缀（${tableId}）`)
    const unavailable = await expectCode(
      runtime.execute('craft', { recipe_id: tableId }),
      'recipe.unavailable',
      'J. 工作台配方',
    )
    assert(String(unavailable && unavailable.message).includes('工作台'), 'J. 说明"需要工作台"')
    // 物品不存在
    await expectCode(
      runtime.execute('craft', { recipe_id: 'not_a_real_item*1=x*1' }),
      'recipe.not_found',
      'J. 物品不存在',
    )
    assert(bot.craftCalls.length === 0, 'J. 这三种情况都没有调用 bot.craft')
    assert(bot.cleanedStates === 0, 'J. 没开始的动作不产生 cleanup')
  }

  console.log('[craft-test] K. material_insufficient（绝不自动准备材料 / 绝不 recipe chain）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 1 }) })
    const { runtime, state } = makeHarness()
    state.bot = bot
    const error = await expectCode(
      runtime.execute('craft', { recipe_id: STICK_OAK_ID }),
      'material.insufficient',
      'K. 木板只有 1 个',
    )
    assert(
      error && Array.isArray(error.detail.missing) && error.detail.missing[0].name === OAK_PLANKS,
      `K. detail.missing 说清差多少（得到 ${JSON.stringify(error && error.detail.missing)}）`,
    )
    assert(bot.craftCalls.length === 0, 'K. 材料不够时绝不调用 bot.craft')

    // §四十三：只有原木、没有木板 → 直接失败，绝不"先做木板再做箱子"
    const logOnly = makeBot({ inventory: makeInventory({ oak_log: 8 }) })
    const harness2 = makeHarness()
    harness2.state.bot = logOnly
    const stickError = await expectCode(
      harness2.runtime.execute('craft', { recipe_id: STICK_OAK_ID }),
      'material.insufficient',
      'K. 只有 oak_log 时做 stick',
    )
    assert(
      logOnly.craftCalls.length === 0 && logOnly.inventory.total('oak_log') === 8,
      'K. 绝不自动把原木做成木板（inventory 一个都没动）',
    )
    assert(Boolean(stickError), 'K. 如实返回 material_insufficient')
  }

  console.log('[craft-test] L/M/N. 取消 / 超时 / 竞态：只允许一个终态')
  {
    // L. crafting 挂住 → STOP → CANCELLED（cleanup 恰好一次、绝无 completed）
    const hang = deferred()
    const bot = makeBot({
      inventory: makeInventory({ [OAK_PLANKS]: 2 }),
      craftImpl: () => hang.promise,
    })
    const counted = withCleanupCounter()
    const harness = makeHarness({ craft: counted.definition })
    harness.state.bot = bot
    const started = await harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    await sleep(30)
    const stopResult = harness.runtime.stop()
    assert(stopResult.cancelled.length === 1, 'L. STOP 取消了 craft')
    assert(
      terminals(harness.events, started.action_id).length === 1 &&
        terminals(harness.events, started.action_id)[0].event === 'minecraft.action.cancelled',
      'L. 终态是 CANCELLED',
    )
    assert(counted.counter.calls === 1, `L. cleanup 恰好一次（得到 ${counted.counter.calls}）`)
    hang.resolve(null) // 底层 craft 之后才完成：仍然不能变成 SUCCEEDED
    await sleep(60)
    assert(
      terminals(harness.events, started.action_id).length === 1,
      'L. 底层完成后仍然是恰好一个终态（绝不双终态）',
    )
    assert(
      !terminals(harness.events, started.action_id).some((row) => row.event === 'minecraft.action.completed'),
      'L. 没有伪造的 completed',
    )

    // M. 超时
    const hang2 = deferred()
    const bot2 = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }), craftImpl: () => hang2.promise })
    const counted2 = withCleanupCounter({ ...CRAFT, timeout_ms: 120 })
    const harness2 = makeHarness({ craft: counted2.definition })
    harness2.state.bot = bot2
    const started2 = await harness2.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    const terminal2 = await settle(harness2, started2.action_id, 2000)
    assert(
      terminal2 && terminal2.event === 'minecraft.action.timeout',
      `M. 超时 → TIMEOUT（得到 ${terminal2 && terminal2.event}）`,
    )
    assert(counted2.counter.calls === 1, `M. 超时后 cleanup 恰好一次（得到 ${counted2.counter.calls}）`)

    // N. race：craft 已完成、同时收到 STOP → 只能 CANCELLED
    const gate = deferred()
    const bot3 = makeBot({
      inventory: makeInventory({ [OAK_PLANKS]: 2 }),
      craftImpl: async (innerBot, recipe) => {
        applyRecipe(innerBot, recipe)
        await gate.promise // 完成"落地"与 STOP 交错的窗口
      },
    })
    const harness3 = makeHarness()
    harness3.state.bot = bot3
    const started3 = await harness3.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    await sleep(20)
    harness3.runtime.stop()
    gate.resolve(null)
    await sleep(80)
    const rows = terminals(harness3.events, started3.action_id)
    assert(rows.length === 1, `N. race 后终态恰好一个（得到 ${rows.length}）`)
    assert(rows[0].event === 'minecraft.action.cancelled', `N. 只能是 CANCELLED（得到 ${rows[0].event}）`)
    assert(
      bot3.inventory.total(STICK) === 4,
      'N. 底层确实合成成功了（如实暴露：取消时世界可能已经变了，但不报 SUCCEEDED）',
    )
  }

  console.log('[craft-test] O. cleanup 恰好一次')
  {
    const hang = deferred()
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }), craftImpl: () => hang.promise })
    const counted = withCleanupCounter()
    const harness = makeHarness({ craft: counted.definition })
    harness.state.bot = bot
    await harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID })
    await sleep(20)
    harness.runtime.stop()
    harness.runtime.stop()
    harness.runtime.cancelAll('disconnect')
    assert(counted.counter.calls === 1, `O. stop×2 + cancelAll 只 cleanup 一次（得到 ${counted.counter.calls}）`)
    hang.resolve(null)
    await sleep(40)
  }

  console.log('[craft-test] P. action.busy（与其它前台动作互斥）')
  {
    const bot = makeBot({ inventory: makeInventory({ [OAK_PLANKS]: 2 }) })
    const hang = new Promise(() => {})
    const harness = makeHarness({
      extra: {
        move_to: {
          exclusive: true,
          timeout_ms: 5000,
          risk: 'LOW',
          async run() {
            await hang
          },
        },
      },
    })
    harness.state.bot = bot
    const pending = harness.runtime.execute('move_to', { x: 1, y: 2, z: 3 }).catch(() => null)
    await sleep(20)
    await expectCode(harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID }), 'action.busy', 'P. move_to 跑着时 craft')
    assert(bot.craftCalls.length === 0, 'P. 被拒时绝不调用 bot.craft')
    harness.runtime.stop()
    await pending
  }

  console.log('[craft-test] 离线 / 未进世界')
  {
    const harness = makeHarness()
    harness.state.bot = makeBot({ entity: null })
    await expectCode(harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID }), 'action.not_online', '无 bot.entity')
    harness.state.online = false
    harness.state.bot = makeBot({})
    await expectCode(harness.runtime.execute('craft', { recipe_id: STICK_OAK_ID }), 'action.not_online', '离线')
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[craft-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[craft-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[craft-test] ALL CHECKS PASSED')
}

/** settle() 的小包装：把 events 数组包成 harness 形状（只为复用 settle）。 */
function harnessOf(events, actionId) {
  return { events, actionId }
}

main().catch((error) => {
  console.error('[craft-test] crashed:', error)
  process.exit(1)
})
