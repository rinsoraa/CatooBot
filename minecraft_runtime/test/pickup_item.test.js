/**
 * pickup_item 测试（Phase 4H · H–V）：监督循环 / 身份绑定 / 收集确认 / 清理 / 取消。
 *
 * 假 bot 模拟：entities（真实形状的 metadata 槽）、entity.position、pathfinder（记录 setGoal）、
 * playerCollect / entityGone 事件、inventory（真的会变）。测试就是"移动 → 目标移动 →
 * 进入半径 → playerCollect → 实体消失 → 背包增加"这一整条状态机。
 *
 * 覆盖：
 *   H success  I moving target  J target replacement  K wrong item  L target lost
 *   M too far  N playerCollect by bot  O playerCollect by other player
 *   P entityGone without inventory change  Q inventory increased but wrong entity
 *   R cleanup  S STOP  T TIMEOUT  U race  V terminal exactly once
 *   W 真实 1.21.1 metadata 形态 + 成功路径（轮询没看到半径就被收走）也必须 setGoal(null)
 *
 * 运行：node minecraft_runtime/test/pickup_item.test.js
 */
'use strict'

// 测试用的小周期（生产默认 250ms / 1500ms；这两个是 runtime 内部旋钮，不是用户配置）
process.env.MC_PICKUP_POLL_MS = '20'
process.env.MC_PICKUP_INVENTORY_GRACE_MS = '60'

const { Vec3 } = require('vec3')

const registry = require('prismarine-registry')('1.21.1')

const {
  ACTION_REGISTRY,
  PICKUP_DEFAULTS,
  droppedItemSlotIndex,
  itemNameFromRecipeId,
  requireSingleContainerWindow,
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

const PICKUP = ACTION_REGISTRY.pickup_item
const DIRT = registry.itemsByName.dirt.id
const SAND = registry.itemsByName.sand.id
const DIRT_ITEM = 3

function itemMetadata(itemId, count) {
  return [
    { key: 0, type: 0, value: 0 },
    {
      key: 8,
      type: droppedItemSlotIndex({ supportFeature: (f) => registry.supportFeature(f) }),
      value: { present: true, itemId, itemCount: count, nbt: null },
    },
  ]
}

function makeInventory() {
  const slots = new Array(46).fill(null)
  const inv = {
    slots,
    items() {
      return inv.slots.filter((item) => item && item.name && item.count > 0)
    },
    total(name) {
      return inv
        .items()
        .filter((item) => item.name === name)
        .reduce((sum, item) => sum + item.count, 0)
    },
    add(name, count) {
      let remaining = count
      for (let slot = 9; slot < 45 && remaining > 0; slot += 1) {
        const item = inv.slots[slot]
        if (item && item.name === name) {
          item.count += remaining
          remaining = 0
        } else if (!item) {
          inv.slots[slot] = { name, count: remaining, type: registry.itemsByName[name].id }
          remaining = 0
        }
      }
      return remaining === 0
    },
  }
  return inv
}

function makeEmitter() {
  const handlers = new Map()
  return {
    on(event, handler) {
      handlers.set(event, [...(handlers.get(event) || []), handler])
    },
    removeListener(event, handler) {
      handlers.set(
        event,
        (handlers.get(event) || []).filter((entry) => entry !== handler),
      )
    },
    emit(event, ...args) {
      for (const handler of [...(handlers.get(event) || [])]) handler(...args)
    },
    listenerCount() {
      let total = 0
      for (const list of handlers.values()) total += list.length
      return total
    },
  }
}

function makeBot({ items = {} } = {}) {
  const emitter = makeEmitter()
  const inventory = makeInventory()
  for (const [name, count] of Object.entries(items)) inventory.add(name, count)
  const bot = {
    registry,
    supportFeature: (feature) => registry.supportFeature(feature),
    inventory,
    entities: {},
    goals: [],
    cleanedStates: 0,
    entity: { id: 42, position: new Vec3(0, 64, 0) },
    pathfinder: {
      goals: [],
      setGoal(goal, dynamic) {
        this.goals.push({ goal, dynamic })
      },
    },
    clearControlStates() {
      bot.cleanedStates += 1
    },
    ...emitter,
  }
  bot.listenerCount = emitter.listenerCount
  return bot
}

function spawnItem(bot, { id = 7, itemId = DIRT, count = DIRT_ITEM, x = 0, y = 64, z = 5 } = {}) {
  const entity = {
    id,
    name: 'item',
    position: new Vec3(x, y, z),
    metadata: itemMetadata(itemId, count),
  }
  bot.entities[id] = entity
  return entity
}

//: 各种"世界事实"的模拟（都在测试里明确写出来，绝不含糊）
function walkTo(bot, entity, distance = 1.0) {
  bot.entity.position = new Vec3(entity.position.x, entity.position.y, entity.position.z + distance)
}
function collectByBot(bot, entity) {
  bot.inventory.add('dirt', entity.metadata[1].value.itemCount)
  delete bot.entities[entity.id]
  bot.emit('playerCollect', bot.entity, entity)
}
function collectByOther(bot, entity) {
  delete bot.entities[entity.id]
  bot.emit('playerCollect', { id: 999, name: 'player' }, entity)
}
function vanish(bot, entity) {
  delete bot.entities[entity.id]
  bot.emit('entityGone', entity)
}

function makeHarness(definition = PICKUP) {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { pickup_item: definition },
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
      [
        'minecraft.action.completed',
        'minecraft.action.failed',
        'minecraft.action.cancelled',
        'minecraft.action.timeout',
      ].includes(row.event) && row.data.action_id === actionId,
  )
}

function completedResult(events, actionId) {
  const row = events.find(
    (entry) => entry.event === 'minecraft.action.completed' && entry.data.action_id === actionId,
  )
  return row ? row.data.result : null
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

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

const ARGS = { entity_id: 7, expected_item: 'minecraft:dirt' }

async function main() {
  setTimeout(() => {
    console.error('[pickup-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 40000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[pickup-test] 注册表属性 / 参数校验')
  {
    assert(Boolean(PICKUP), 'pickup_item 已注册')
    assert(PICKUP.risk === 'MEDIUM', `risk = MEDIUM（得到 ${PICKUP.risk}）`)
    assert(PICKUP.exclusive === true, 'exclusive = true（会移动 + 改背包）')
    assert(PICKUP.detached === true, 'detached = true（启动即 RUNNING）')
    assert(typeof PICKUP.cleanup === 'function', '有 cleanup')
    assert(
      PICKUP_DEFAULTS.radius === 1.2 && PICKUP_DEFAULTS.maxDistance === 16,
      `常量（得到 radius=${PICKUP_DEFAULTS.radius} max=${PICKUP_DEFAULTS.maxDistance}）`,
    )
    for (const bad of [
      {},
      { entity_id: '7', expected_item: 'dirt' },
      { entity_id: 7.5, expected_item: 'dirt' },
      { entity_id: -1, expected_item: 'dirt' },
      { entity_id: 7 },
      { entity_id: 7, expected_item: '' },
      { entity_id: 7, expected_item: 3 },
    ]) {
      let code = null
      try {
        PICKUP.validate(bad)
      } catch (error) {
        code = error.code
      }
      assert(code === 'action.invalid', `${JSON.stringify(bad)} → action.invalid（得到 ${code}）`)
    }
    assert(PICKUP_DEFAULTS.maxItems === 32, 'maxItems 常量')
    assert(itemNameFromRecipeId('x*1=y*1') === 'x', '（助手仍可用）')
    assert(typeof requireSingleContainerWindow === 'function', '（既有导出未受影响）')
  }

  console.log('[pickup-test] 启动阶段的拒绝（实体不存在 / 不是掉落物 / 物品不符 / 太远）')
  {
    const missing = makeHarness()
    missing.state.bot = makeBot()
    await expectCode(missing.runtime.execute('pickup_item', ARGS), 'item_entity.not_found', '实体不存在')

    const notItem = makeHarness()
    notItem.state.bot = makeBot()
    notItem.state.bot.entities[7] = { id: 7, name: 'zombie', position: new Vec3(0, 64, 5) }
    await expectCode(notItem.runtime.execute('pickup_item', ARGS), 'item_entity.invalid', '不是掉落物')

    const wrong = makeHarness()
    wrong.state.bot = makeBot()
    spawnItem(wrong.state.bot, { itemId: SAND })
    const changed = await expectCode(
      wrong.runtime.execute('pickup_item', ARGS),
      'item_entity.changed',
      'K. 实体上是沙不是土',
    )
    assert(
      changed && changed.detail && changed.detail.expected === 'dirt' && changed.detail.actual === 'sand',
      `K. 如实带 expected/actual（得到 ${JSON.stringify(changed && changed.detail)}）`,
    )

    const far = makeHarness()
    far.state.bot = makeBot()
    spawnItem(far.state.bot, { z: 40 })
    const tooFar = await expectCode(
      far.runtime.execute('pickup_item', ARGS),
      'pickup.target_too_far',
      'M. 40 格外的目标',
    )
    assert(
      tooFar && tooFar.detail && tooFar.detail.distance === 40,
      `M. detail 带距离（得到 ${JSON.stringify(tooFar && tooFar.detail)}）`,
    )
    assert(far.events.filter((row) => row.event === 'minecraft.action.started').length === 1, 'M. 仍然记录 started')
  }

  console.log('[pickup-test] H/N. 成功路径：走过去 → 进入半径 → playerCollect → 背包增加')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    assert(
      started.status === STATES.RUNNING && Boolean(started.action_id),
      `启动即 RUNNING（得到 ${JSON.stringify(started)}）`,
    )
    // §二十二：用的是官方动态 Goal，并且持有**实体引用**（不是固定坐标）
    assert(bot.pathfinder.goals.length === 1, '只 setGoal 一次（动态 Goal 自己跟）')
    assert(
      bot.pathfinder.goals[0].goal.entity === entity && bot.pathfinder.goals[0].dynamic === true,
      'GoalFollow(entity, radius) + dynamic',
    )
    await sleep(40)
    walkTo(bot, entity, 1.0) // 走进拾取半径
    await sleep(60)
    assert(
      bot.pathfinder.goals.some((row) => row.goal === null),
      '进入半径 → setGoal(null)（停导航）',
    )
    assert(bot.cleanedStates >= 1, '进入半径 → clearControlStates')
    collectByBot(bot, entity)
    const terminal = await settle(harness, started.action_id)
    const result = completedResult(harness.events, started.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.completed',
      `H. 终态 completed（得到 ${terminal && terminal.event}）`,
    )
    assert(
      result &&
        result.entity_id === 7 &&
        result.item.name === 'dirt' &&
        result.item.count_before === DIRT_ITEM,
      `H. 结果语义（得到 ${JSON.stringify(result && result.item)}）`,
    )
    assert(
      result.distance_start === 5 && result.distance_collected <= PICKUP_DEFAULTS.radius,
      `H. 距离 before/after（得到 ${result.distance_start} → ${result.distance_collected}）`,
    )
    assert(
      result.inventory_before === 0 && result.inventory_after === DIRT_ITEM && result.collected_count === DIRT_ITEM,
      `N. 背包 before/after（得到 ${result.inventory_before} → ${result.inventory_after}）`,
    )
    assert(result.collected === true, 'H. collected = true')
    assert(bot.listenerCount() === 0, 'R. 成功后监听器已摘除（没有泄漏）')
    assert(terminals(harness.events, started.action_id).length === 1, 'V. 终态恰好一次')
  }

  console.log('[pickup-test] I. 目标会移动：距离按**实时位置**判，不是启动时的坐标')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(40)
    entity.position = new Vec3(0, 64, 12) // Item 漂到更远处（仍在 16 格内）
    await sleep(40)
    assert(
      terminals(harness.events, started.action_id).length === 0,
      'I. 目标移动不会误判成失败（仍在最大距离内）',
    )
    walkTo(bot, entity, 0.8) // 追到**新位置**旁边
    await sleep(60)
    collectByBot(bot, entity)
    const terminal = await settle(harness, started.action_id)
    const result = completedResult(harness.events, started.action_id)
    assert(terminal && terminal.event === 'minecraft.action.completed', 'I. 追到新位置后成功')
    assert(
      result && result.distance_collected <= PICKUP_DEFAULTS.radius,
      `I. distance_collected 是新位置的距离（得到 ${result && result.distance_collected}）`,
    )
  }

  console.log('[pickup-test] J. 目标被替换（id 被复用）→ 绝不改绑')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    // 旧实体没了、同 id 又被分配给了另一个实体（最危险的场景）
    bot.entities[7] = { id: 7, name: 'item', position: new Vec3(0, 64, 1), metadata: itemMetadata(DIRT, 1) }
    const terminal = await settle(harness, started.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.failed' && terminal.data.code === 'target.replaced',
      `J. 目标被替换 → target.replaced（得到 ${terminal && terminal.event}/${terminal && terminal.data.code}）`,
    )
    assert(
      bot.inventory.total('dirt') === 0,
      'J. 绝不去捡那个"新实体"（背包没有变化）',
    )
    assert(
      !bot.pathfinder.goals.some((row) => row.goal && row.goal.entity === bot.entities[7]),
      'J. 没有把 Goal 改绑到新实体',
    )
    assert(entity.metadata, 'J. 旧实体引用仍然只是记录（没有被误用）')
  }

  console.log('[pickup-test] K2/L/O/Q. 物品变了 / 被别的玩家捡走')
  {
    // K2：导航途中实体上的物品被换掉
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    entity.metadata = itemMetadata(SAND, 1)
    const terminal = await settle(harness, started.action_id)
    assert(
      terminal && terminal.data.code === 'item_entity.changed',
      `K2. 物品变成沙 → item_entity.changed（得到 ${terminal && terminal.data.code}）`,
    )

    // O/Q：别的玩家捡走了（就算背包"看起来"增加了也不算 bot 的）
    const bot2 = makeBot()
    const entity2 = spawnItem(bot2, { z: 5 })
    const harness2 = makeHarness()
    harness2.state.bot = bot2
    const started2 = await harness2.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    bot2.inventory.add('dirt', 3) // 背包增加（但不是这次捡的）
    collectByOther(bot2, entity2)
    const terminal2 = await settle(harness2, started2.action_id)
    assert(
      terminal2 && terminal2.data.code === 'pickup.target_lost',
      `O/Q. 别人捡走 → pickup.target_lost（得到 ${terminal2 && terminal2.data.code}）`,
    )
  }

  console.log('[pickup-test] M2. 运行途中目标被拉远 → 停，不无限追')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    entity.position = new Vec3(0, 64, 60) // 被水冲/被推得老远
    const terminal = await settle(harness, started.action_id)
    assert(
      terminal && terminal.data.code === 'pickup.target_too_far',
      `M2. 跑太远 → pickup.target_too_far（得到 ${terminal && terminal.data.code}）`,
    )
    assert(
      bot.pathfinder.goals.some((row) => row.goal === null),
      'M2. 失败时自己清了 Goal',
    )
  }

  console.log('[pickup-test] P. entityGone 但背包没增加 → pickup_unconfirmed')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    walkTo(bot, entity, 0.9)
    await sleep(40)
    vanish(bot, entity) // 服务器销毁 / 掉进未加载区块
    const terminal = await settle(harness, started.action_id, 2000)
    assert(
      terminal && terminal.data.code === 'pickup.unconfirmed',
      `P. 实体没了但背包没变 → pickup_unconfirmed（得到 ${terminal && terminal.data.code}）`,
    )
    assert(
      terminal.data.detail && terminal.data.detail.inventory_after === 0,
      `P. 如实带上 inventory before/after（得到 ${JSON.stringify(terminal.data.detail)}）`,
    )
  }

  console.log('[pickup-test] P2. entityGone + 背包随后到账 → 仍然算成功（§三十三 fallback）')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 5 })
    const harness = makeHarness()
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(30)
    walkTo(bot, entity, 0.9)
    await sleep(40)
    delete bot.entities[entity.id]
    bot.emit('entityGone', entity)
    bot.inventory.add('dirt', DIRT_ITEM) // inventory 包晚一点到
    const terminal = await settle(harness, started.action_id, 2000)
    assert(
      terminal && terminal.event === 'minecraft.action.completed',
      `P2. 背包到账 → completed（得到 ${terminal && terminal.event}）`,
    )
  }

  console.log('[pickup-test] S/U/V. STOP：取消后不报成功、只一个终态、cleanup 恰好一次')
  {
    const bot = makeBot()
    const entity = spawnItem(bot, { z: 12 })
    const counter = { calls: 0 }
    const harness = makeHarness({
      ...PICKUP,
      cleanup(botArg, controller) {
        counter.calls += 1
        return PICKUP.cleanup(botArg, controller)
      },
    })
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    await sleep(40)
    const stopped = harness.runtime.stop()
    assert(stopped.cancelled.length === 1, 'S. STOP 取消了 pickup')
    const terminal = await settle(harness, started.action_id)
    assert(
      terminal && terminal.event === 'minecraft.action.cancelled',
      `S. 终态 CANCELLED（得到 ${terminal && terminal.event}）`,
    )
    assert(counter.calls === 1, `R. cleanup 恰好一次（得到 ${counter.calls}）`)
    assert(
      bot.pathfinder.goals.some((row) => row.goal === null),
      'S. 取消后 setGoal(null)（绝不继续走）',
    )
    assert(bot.listenerCount() === 0, 'R. 取消后监听器已摘除')
    // U. 取消之后才发生收集：不能再冒出 completed
    collectByBot(bot, entity)
    await sleep(80)
    assert(
      terminals(harness.events, started.action_id).length === 1,
      'U/V. 之后发生收集也只保留一个终态（绝不双终态）',
    )
    assert(
      !terminals(harness.events, started.action_id).some((row) => row.event === 'minecraft.action.completed'),
      'U. 没有伪造的 completed',
    )
  }

  console.log('[pickup-test] T. TIMEOUT（目标一直不消失、背包也不变）')
  {
    const bot = makeBot()
    spawnItem(bot, { z: 5 })
    const counter = { calls: 0 }
    const harness = makeHarness({
      ...PICKUP,
      timeout_ms: 120,
      cleanup(botArg, controller) {
        counter.calls += 1
        return PICKUP.cleanup(botArg, controller)
      },
    })
    harness.state.bot = bot
    const started = await harness.runtime.execute('pickup_item', ARGS)
    const terminal = await settle(harness, started.action_id, 3000)
    assert(
      terminal && terminal.event === 'minecraft.action.timeout',
      `T. 超时 → TIMEOUT（得到 ${terminal && terminal.event}）`,
    )
    assert(counter.calls === 1, `T. 超时后 cleanup 恰好一次（得到 ${counter.calls}）`)
    assert(bot.listenerCount() === 0, 'T. 超时后监听器已摘除')
  }

  console.log('[pickup-test] W. 真实 1.21.1 形态：metadata 是按 key 索引的对象、物品栈没有 present')
  {
    // 真机（1.21.1 + Fabric）上 entity.metadata 是 { '0': …, '8': … } 对象，
    // 物品栈那一个的值是 { itemId, itemCount, addedComponentCount, removedComponentCount,
    // components, removeComponents } —— **没有** present。
    // 修复前 start 会对每一个真实掉落物报 item_entity.invalid（读不到栈）。
    const metadataKeys = registry.entitiesByName.item.metadataKeys
    const stackKey = String(metadataKeys.indexOf('item'))
    assert(metadataKeys.indexOf('item') === 8, `1.21.1 的 item 栈在 metadataKeys[8]（得到 ${stackKey}）`)
    const realMetadata = (itemId, itemCount) => ({
      0: 0,
      [stackKey]: {
        itemId,
        itemCount,
        addedComponentCount: 0,
        removedComponentCount: 0,
        components: [],
        removeComponents: [],
      },
    })
    const spawnReal = (bot, name, count, id = 7) => {
      const entity = {
        id,
        name: 'item',
        position: new Vec3(0, 64, 4),
        metadata: realMetadata(registry.itemsByName[name].id, count),
      }
      bot.entities[id] = entity
      return entity
    }

    // W1/K3. 真实形态 + 物品一致 → 正常启动；物品不符 → 如实报 item_entity.changed
    const ok = makeHarness()
    ok.state.bot = makeBot()
    spawnReal(ok.state.bot, 'oak_log', 24)
    const started = await ok.runtime.execute('pickup_item', {
      entity_id: 7,
      expected_item: 'minecraft:oak_log',
    })
    assert(
      started.status === 'RUNNING' && typeof started.action_id === 'string' && started.action_id,
      `真实形态启动 → RUNNING（得到 ${JSON.stringify(started)}）`,
    )
    ok.runtime.stop(started.action_id)
    await sleep(30)

    const mismatch = makeHarness()
    mismatch.state.bot = makeBot()
    spawnReal(mismatch.state.bot, 'oak_log', 24)
    const changed = await expectCode(
      mismatch.runtime.execute('pickup_item', { entity_id: 7, expected_item: 'minecraft:dirt' }),
      'item_entity.changed',
      'K3. 真实形态 + 期望 dirt（实际是 oak_log）',
    )
    assert(
      changed && changed.detail && changed.detail.actual === 'oak_log',
      `K3. actual 取自真实形态（得到 ${JSON.stringify(changed && changed.detail)}）`,
    )

    // W2. present === false 的真实形态（空栈）→ 读不到物品 → item_entity.invalid
    const empty = makeHarness()
    empty.state.bot = makeBot()
    empty.state.bot.entities[7] = {
      id: 7,
      name: 'item',
      position: new Vec3(0, 64, 4),
      metadata: { [stackKey]: { present: false } },
    }
    await expectCode(
      empty.runtime.execute('pickup_item', { entity_id: 7, expected_item: 'minecraft:dirt' }),
      'item_entity.invalid',
      'W2. 空栈（present: false）不当作掉落物',
    )

    // W3. 真实形态走完整成功路径：进入半径 → playerCollect → 背包真增加
    const run = makeHarness()
    run.state.bot = makeBot()
    const target = spawnReal(run.state.bot, 'oak_log', 24)
    const beforeCount = run.state.bot.inventory.total('oak_log')
    const running = await run.runtime.execute('pickup_item', {
      entity_id: 7,
      expected_item: 'oak_log',
    })
    assert(beforeCount === 0 && running.status === 'RUNNING', 'W3. 背包原先没有 oak_log')
    walkTo(run.state.bot, target, 1.0)
    await sleep(80)
    const goals = run.state.bot.pathfinder.goals
    assert(
      goals.length === 2 && goals[0].goal && goals[0].dynamic === true && goals[1].goal === null,
      `W3. 先 GoalFollow(dynamic)，进入半径后 setGoal(null)（得到 ${goals.length} 次 setGoal）`,
    )
    run.state.bot.inventory.add('oak_log', 24)
    delete run.state.bot.entities[7]
    run.state.bot.emit('playerCollect', run.state.bot.entity, target)
    const terminal = await settle(run, running.action_id)
    const result = completedResult(run.events, running.action_id)
    assert(terminal && terminal.event === 'minecraft.action.completed', `W3. 终态 completed（得到 ${terminal && terminal.event}）`)
    assert(
      result &&
        result.collected === true &&
        result.collected_count === 24 &&
        result.item.name === 'oak_log' &&
        result.item.count_before === 24 &&
        result.inventory_after === 24,
      `W3. 结果按真实形态核对（得到 ${JSON.stringify(result)}）`,
    )
    assert(run.state.bot.listenerCount() === 0, 'W3. 监听器全部摘掉（没有残留 playerCollect/entityGone）')
    assert(terminals(run.events, running.action_id).length === 1, 'W3. 只有一个终态事件')
    // W4. 服务器在**两次轮询之间**就把物品收进背包（掉落物掉到下层、罐头跟着掉下去正好踩到）
    //     → 轮询从来没看到「进入半径」，但成功同样必须收掉导航：
    //     ActionRuntime 的契约是 SUCCEEDED 不触发 cleanup，所以 wait 必须自己 setGoal(null)。
    //     真机 smoke 就是这么发现的（拾取成功后 pathfinder.goal 一直是 GoalFollow）。
    const jump = makeHarness()
    jump.state.bot = makeBot()
    const jumpedTarget = spawnReal(jump.state.bot, 'oak_log', 5)
    const jumpStart = await jump.runtime.execute('pickup_item', {
      entity_id: 7,
      expected_item: 'oak_log',
    })
    assert(jumpStart.status === 'RUNNING', 'W4. 启动 → RUNNING')
    const jumpStartGoals = jump.state.bot.pathfinder.goals.slice()
    assert(
      jumpStartGoals.length === 1 &&
        jumpStartGoals[0].goal &&
        jumpStartGoals[0].dynamic === true,
      `W4. 启动时建的是动态 Goal（得到 ${JSON.stringify(jumpStartGoals)}）`,
    )
    jump.state.bot.inventory.add('oak_log', 5)
    delete jump.state.bot.entities[7]
    jump.state.bot.emit('playerCollect', jump.state.bot.entity, jumpedTarget)
    const jumpTerminal = await settle(jump, jumpStart.action_id)
    const jumpGoals = jump.state.bot.pathfinder.goals
    assert(
      jumpTerminal && jumpTerminal.event === 'minecraft.action.completed',
      `W4. 终态 completed（得到 ${jumpTerminal && jumpTerminal.event}）`,
    )
    assert(
      jumpGoals.length === 2 && jumpGoals[1].goal === null,
      'W4. 成功后 setGoal(null)：没有留下 GoalFollow 残留',
    )
    assert(jump.state.bot.listenerCount() === 0, 'W4. 监听器也摘干净了')
  }

  console.log('[pickup-test] 离线 / 没有 bot')
  {
    const harness = makeHarness()
    harness.state.online = false
    harness.state.bot = makeBot()
    await expectCode(harness.runtime.execute('pickup_item', ARGS), 'action.not_online', '离线')
    harness.state.online = true
    harness.state.bot = { entity: null, entities: {} }
    await expectCode(harness.runtime.execute('pickup_item', ARGS), 'action.not_online', '没有进世界')
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[pickup-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[pickup-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[pickup-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[pickup-test] crashed:', error)
  process.exit(1)
})
