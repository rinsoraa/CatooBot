/**
 * dropped_items 测试（Phase 4H · A–G）：掉落物实体的识别、过滤、排序、上限与生命周期。
 *
 * 用**真实物品表**（1.21.1）+ 真实形状的 entity metadata 槽位（与 mineflayer 自己的
 * 判定公式一致），假 bot 只提供 `entities` / `entity.position` / `supportFeature`。
 *
 * 覆盖：
 *   A. item entity extraction   B. filtering   C. sorting   D. max_items
 *   E. stale entity（读不到栈/位置的不进列表）  F. entityGone（读模型不缓存）
 *   G. playerCollect（被捡走后就不该再出现）
 *   H. 真实 1.21.1 形态（metadata 是按 key 索引的对象、物品栈没有 present）
 *
 * 运行：node minecraft_runtime/test/dropped_items.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const registry = require('prismarine-registry')('1.21.1')

const {
  ACTION_REGISTRY,
  PICKUP_DEFAULTS,
  droppedItemSlotIndex,
  droppedItemStack,
  droppedItemsView,
  isDroppedItemEntity,
} = require('../runtime.js')
const { createActionRuntime, STATES } = require('../action_runtime')

const OAK_LOG = registry.itemsByName['oak_log'].id

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

const DROPPED = ACTION_REGISTRY.dropped_items
const DIRT = registry.itemsByName.dirt.id
const SAND = registry.itemsByName.sand.id

/** 真实形状的 entity metadata：item 栈在 type === droppedItemSlotIndex() 的槽里。 */
function itemMetadata(itemId, count) {
  return [
    { key: 0, type: 0, value: 0 },
    {
      key: 8,
      type: droppedItemSlotIndex(makeBot({ entities: {} })),
      value: { present: true, itemId, itemCount: count, nbt: null },
    },
  ]
}

function itemEntity(id, itemId, count, x, y, z) {
  return { id, name: 'item', position: new Vec3(x, y, z), metadata: itemMetadata(itemId, count) }
}

function makeBot(overrides = {}) {
  const bot = {
    registry,
    supportFeature: (feature) => registry.supportFeature(feature),
    entity: { position: new Vec3(0, 64, 0) },
    entities: {},
    ...overrides,
  }
  return bot
}

function makeHarness() {
  const events = []
  const logs = []
  const state = { bot: null, online: true }
  const runtime = createActionRuntime({
    registry: { dropped_items: DROPPED },
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state }
}

async function lookup(runtime) {
  const response = await runtime.execute('dropped_items', {})
  return response.result
}

async function main() {
  setTimeout(() => {
    console.error('[dropped-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[dropped-test] 注册表属性与常量')
  {
    assert(Boolean(DROPPED), 'dropped_items 已注册')
    assert(DROPPED.risk === 'SAFE', `risk = SAFE（得到 ${DROPPED.risk}）`)
    assert(DROPPED.exclusive === false, 'exclusive = false（纯读取，可与前台动作并行）')
    assert(DROPPED.detached !== true, '同步返回（不是持续型动作）')
    assert(
      PICKUP_DEFAULTS.maxItems === 32 && PICKUP_DEFAULTS.radius === 1.2 && PICKUP_DEFAULTS.maxDistance === 16,
      `PICKUP_DEFAULTS（得到 ${JSON.stringify(PICKUP_DEFAULTS)}）`,
    )
  }

  console.log('[dropped-test] A. item entity extraction（用 metadata 槽读真栈）')
  {
    const bot = makeBot()
    const entity = itemEntity(2, DIRT, 3, 0, 64, 3)
    bot.entities = { 2: entity }
    assert(isDroppedItemEntity(bot, entity) === true, 'name=item 被识别为掉落物')
    assert(
      isDroppedItemEntity(bot, { id: 9, name: 'item_stack' }) === true,
      'name=item_stack 也被识别（mineflayer 自己就是这么判的）',
    )
    const stack = droppedItemStack(bot, entity)
    assert(
      stack && stack.name === 'dirt' && stack.count === 3,
      `栈语义（得到 ${JSON.stringify(stack)}）`,
    )
    const view = droppedItemsView(bot)
    assert(view.online === true && view.total === 1, `在线 + 一个掉落物（得到 ${JSON.stringify(view)}）`)
    const row = view.items[0]
    assert(row.entity_id === 2, 'entity_id 原样给出')
    assert(row.item.name === 'dirt' && row.item.count === 3, 'item 语义')
    assert(
      row.position.x === 0 && row.position.y === 64 && row.position.z === 3,
      `position（得到 ${JSON.stringify(row.position)}）`,
    )
    assert(row.distance === 3, `distance（得到 ${row.distance}）`)
    assert(
      Object.keys(row).sort().join(',') === 'distance,entity_id,item,position',
      `只暴露约定字段（得到 ${Object.keys(row).sort().join(',')}）`,
    )
    const raw = JSON.stringify(view)
    for (const forbidden of ['metadata', 'itemId', 'velocity', 'uuid', 'nbt', 'present']) {
      assert(!raw.includes(forbidden), `不泄露 ${forbidden}`)
    }
  }

  console.log('[dropped-test] B. filtering（玩家/怪物/投射物一律不进列表）')
  {
    const bot = makeBot()
    bot.entities = {
      1: { id: 1, name: 'player', position: new Vec3(1, 64, 0), username: 'X' },
      2: itemEntity(2, DIRT, 3, 0, 64, 3),
      3: { id: 3, name: 'zombie', position: new Vec3(2, 64, 0) },
      4: { id: 4, name: 'arrow', position: new Vec3(3, 64, 0) },
      5: { id: 5, name: 'experience_orb', position: new Vec3(4, 64, 0) },
      6: { id: 6, name: 'item_frame', position: new Vec3(5, 64, 0) },
    }
    const view = droppedItemsView(bot)
    assert(
      view.total === 1 && view.items[0].entity_id === 2,
      `只有掉落物进列表（得到 ${JSON.stringify(view.items.map((row) => row.entity_id))}）`,
    )
  }

  console.log('[dropped-test] C. sorting（距离升序，其次 entity_id 升序）')
  {
    const bot = makeBot()
    bot.entities = {
      30: itemEntity(30, DIRT, 1, 0, 64, 5),
      10: itemEntity(10, SAND, 2, 0, 64, 5),
      20: itemEntity(20, DIRT, 1, 0, 64, 2),
    }
    const view = droppedItemsView(bot)
    assert(
      view.items.map((row) => row.entity_id).join(',') === '20,10,30',
      `排序结果（得到 ${view.items.map((row) => row.entity_id).join(',')}）`,
    )
    // 同一世界状态 → 两次读数完全一致（Object.values 顺序不稳定也不影响）
    assert(
      JSON.stringify(droppedItemsView(bot)) === JSON.stringify(view),
      '相同状态下输出稳定',
    )
  }

  console.log('[dropped-test] D. max_items / truncated')
  {
    const bot = makeBot()
    const entities = {}
    for (let index = 0; index < 40; index += 1) {
      entities[100 + index] = itemEntity(100 + index, DIRT, 1, 0, 64, index + 1)
    }
    bot.entities = entities
    const view = droppedItemsView(bot)
    assert(view.total === 40, `total 是真实总数（得到 ${view.total}）`)
    assert(view.truncated === true, 'truncated = true')
    assert(view.items.length === PICKUP_DEFAULTS.maxItems, `只列 32 个（得到 ${view.items.length}）`)
    assert(view.items[0].entity_id === 100, '截断的是最远的那些（近的优先）')
  }

  console.log('[dropped-test] E. stale entity（数据不全的实体不进列表）')
  {
    const bot = makeBot()
    bot.entities = {
      1: itemEntity(1, DIRT, 1, 0, 64, 1),
      2: { id: 2, name: 'item', position: new Vec3(0, 64, 2) }, // 还没有 metadata
      3: { id: 3, name: 'item', metadata: itemMetadata(DIRT, 1) }, // 还没有 position
      4: { id: 4, name: 'item', position: null, metadata: itemMetadata(DIRT, 1) },
      5: { id: 5, name: 'item', position: new Vec3(0, 64, 5), metadata: [{ key: 8, type: 7, value: { present: false } }] },
    }
    const view = droppedItemsView(bot)
    assert(
      view.total === 1 && view.items[0].entity_id === 1,
      `只列出完整可读的那一个（得到 ${JSON.stringify(view.items.map((row) => row.entity_id))}）`,
    )
  }

  console.log('[dropped-test] F/G. entityGone / playerCollect：读模型永远反映当前世界')
  {
    const bot = makeBot()
    bot.entities = { 1: itemEntity(1, DIRT, 1, 0, 64, 1), 2: itemEntity(2, DIRT, 1, 0, 64, 4) }
    const { runtime, state } = makeHarness()
    state.bot = bot
    const before = await lookup(runtime)
    assert(before.total === 2, '两个掉落物')
    // F. entityGone：服务器把实体移出 bot.entities（或者被玩家捡走）
    delete bot.entities[2]
    const afterGone = await lookup(runtime)
    assert(
      afterGone.total === 1 && afterGone.items[0].entity_id === 1,
      '实体消失后立刻不再出现（不缓存旧实体）',
    )
    // G. playerCollect：bot 自己捡走 → 服务器同样移除实体 + 背包增加
    bot.inventory = { items: () => [{ name: 'dirt', count: 1, type: DIRT }] }
    delete bot.entities[1]
    const afterCollect = await lookup(runtime)
    assert(afterCollect.total === 0 && afterCollect.items.length === 0, '捡走后列表为空')
  }

  console.log('[dropped-test] H. 真实 1.21.1 形态：metadata 是按 key 索引的对象、物品栈没有 present')
  {
    // 这一段直接照着真实服务器（1.21.1 + Fabric）观察到的形状写：
    //   entity.metadata 不是 [{key,type,value}] 数组，而是 { '0': .., '8': .. } 对象；
    //   物品栈那一个的值是 { itemId, itemCount, addedComponentCount, removedComponentCount,
    //   components, removeComponents } —— **没有** present 字段。
    // 修复前 decodeRawItemStack 要求 present 才认，真实服务器上掉落物因此全部读不出来。
    const metadataKeys = registry.entitiesByName.item.metadataKeys
    const stackKey = String(metadataKeys.indexOf('item'))
    assert(metadataKeys.indexOf('item') === 8, `1.21.1 的 item 栈在 metadataKeys[8]（得到 ${stackKey}）`)
    const realStack = (itemId, itemCount) => ({
      itemId,
      itemCount,
      addedComponentCount: 0,
      removedComponentCount: 0,
      components: [],
      removeComponents: [],
    })
    const realMetadata = (itemId, itemCount) => ({
      0: 0,
      [stackKey]: realStack(itemId, itemCount),
    })
    const bot = makeBot()
    bot.entities = {
      1: { id: 1, name: 'item', position: new Vec3(0, 64, 3), metadata: realMetadata(OAK_LOG, 5) },
      2: { id: 2, name: 'item', position: new Vec3(0, 64, 1), metadata: realMetadata(DIRT, 24) },
      // present === false 仍然是"空栈"（老版本客户端/插件会这么给）→ 不进列表
      3: { id: 3, name: 'item', position: new Vec3(0, 64, 2), metadata: { [stackKey]: { present: false } } },
    }
    const view = droppedItemsView(bot)
    assert(view.total === 2, `真实形态能读出两个掉落物（得到 ${view.total}）`)
    assert(
      view.items[0].entity_id === 2 &&
        view.items[0].item.name === 'dirt' &&
        view.items[0].item.count === 24,
      `最近的那个是 dirt ×24（得到 ${JSON.stringify(view.items[0])}）`,
    )
    assert(
      view.items[1].entity_id === 1 &&
        view.items[1].item.name === 'oak_log' &&
        view.items[1].item.count === 5,
      `其次是 oak_log ×5（得到 ${JSON.stringify(view.items[1])}）`,
    )
    // 投影仍然只有那五个字段：没有 metadata / itemId 原始数字 / components
    assert(
      JSON.stringify(Object.keys(view.items[0]).sort()) ===
        JSON.stringify(['distance', 'entity_id', 'item', 'position']),
      `投影字段固定（得到 ${JSON.stringify(Object.keys(view.items[0]))}）`,
    )
    assert(
      JSON.stringify(Object.keys(view.items[0].item).sort()) === JSON.stringify(['count', 'name']),
      'item 只有 name/count（不泄露内部数字 id）',
    )
    // 扫描回退：metadataKeys 拿不到（插件/怪版本）时也能靠值扫描读出物品栈
    const noKeys = makeBot()
    noKeys.registry = { items: registry.items, entitiesByName: {} }
    delete noKeys.registry.entitiesByName.item
    noKeys.entities = { 7: { id: 7, name: 'item', position: new Vec3(0, 64, 1), metadata: realMetadata(OAK_LOG, 3) } }
    const scanned = droppedItemsView(noKeys)
    assert(
      scanned.total === 1 && scanned.items[0].item.name === 'oak_log' && scanned.items[0].item.count === 3,
      `没有 metadataKeys 时靠扫描读出（得到 ${JSON.stringify(scanned.items)}）`,
    )
    const stack = droppedItemStack(makeBot(), {
      id: 9,
      name: 'item',
      position: new Vec3(0, 64, 1),
      metadata: realMetadata(OAK_LOG, 2),
    })
    assert(stack && stack.name === 'oak_log' && stack.count === 2, `droppedItemStack 直接读真实形态（得到 ${JSON.stringify(stack)}）`)
  }

  console.log('[dropped-test] 离线 / 空列表 / 上限边界')
  {
    const { runtime, state } = makeHarness()
    state.bot = makeBot()
    const empty = await lookup(runtime)
    assert(
      empty.online === true && empty.total === 0 && empty.truncated === false && Array.isArray(empty.items),
      `空列表如实返回（得到 ${JSON.stringify(empty)}）`,
    )
    // bot 引用没了（还没进世界）→ 动作自己如实拒绝
    state.bot = null
    let noBot = null
    try {
      await runtime.execute('dropped_items', {})
    } catch (error) {
      noBot = error.code
    }
    assert(noBot === 'action.not_online', `没有 bot → action.not_online（得到 ${noBot}）`)
    // 离线（runtime 层先拦）
    state.bot = makeBot()
    state.online = false
    let code = null
    try {
      await runtime.execute('dropped_items', {})
    } catch (error) {
      code = error.code
    }
    assert(code === 'action.not_online', `离线执行 → action.not_online（得到 ${code}）`)
  }

  clearInterval(keepAlive)
  console.log('')
  console.log(`[dropped-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.log(`[dropped-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[dropped-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[dropped-test] crashed:', error)
  process.exit(1)
})
