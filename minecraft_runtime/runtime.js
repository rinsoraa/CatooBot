'use strict'
/**
 * CatooBot Minecraft Runtime（Phase 1 · 连接层）。
 *
 * 一个长期存活的 Node 进程，管理恰好一个 mineflayer bot，并把它包成本地
 * HTTP Bridge API（任务书 §CatooBot → Minecraft Bridge API）：
 *
 *   POST /minecraft/connect     { host, port }      → { session_id, status }
 *   GET  /minecraft/status                          → 连接状态 / 基础世界状态
 *   POST /minecraft/chat        { message }         → 让 bot 在服务器里发言
 *   POST /minecraft/disconnect                      → 主动退出
 *   GET  /minecraft/health                          → 进程级存活探针
 *
 * 生命周期是显式状态机（任务书 §生命周期）：
 *   DISCONNECTED → CONNECTING → AUTHENTICATING → CONNECTED → SPAWNING → ONLINE
 *   任意活动态 → DISCONNECTING → DISCONNECTED；失败落 ERROR（可重新 connect）。
 *
 * Bridge 事件通过 HTTP POST 回调推给 CatooBot（MC_CALLBACK_URL +
 * MC_CALLBACK_TOKEN，带退避重试与有界队列）。认证信息只从本地 auth.json
 * 读取，绝不经过 Bridge API 传输，也绝不写进日志。
 *
 * 本进程被设计为「永不因 bot 故障而崩溃」：未捕获异常只记录并转成
 * minecraft.error 事件；HTTP 服务器才是进程的心跳。
 */

const http = require('http')
const path = require('path')
const fs = require('fs')
const mineflayer = require('mineflayer')
const Vec3 = require('vec3').Vec3 ?? require('vec3')
const pathfinder = require('mineflayer-pathfinder')
const { Movements, goals } = pathfinder
const { createActionRuntime, ActionError } = require('./action_runtime')

// --------------------------------------------------------------- configuration

const RUNTIME_VERSION = 'phase1.0'
const PORT = Number.parseInt(process.env.MC_RUNTIME_PORT || '25580', 10)
const CALLBACK_URL = process.env.MC_CALLBACK_URL || ''
const CALLBACK_TOKEN = process.env.MC_CALLBACK_TOKEN || ''
const AUTH_FILE = process.env.MC_AUTH_FILE || path.join(__dirname, 'auth.json')
const CONNECT_TIMEOUT_S = Number.parseFloat(process.env.MC_CONNECT_TIMEOUT || '75')
const BRIDGE_TOKEN = process.env.MC_BRIDGE_TOKEN || ''

const CHAT_MAX_CHARS = 256

// Phase 3C：move_to（非破坏性导航）
const MOVE_TO_RADIUS = 1.5 // GoalNear 半径：进入约 1.5 格即视为到达
const MOVE_MAX_DISTANCE = Number.parseFloat(process.env.MC_MOVE_MAX_DISTANCE || '64')
const MOVE_TIMEOUT_MS = Number.parseInt(process.env.MC_MOVE_TIMEOUT_MS || '30000', 10)

// Phase 4B：dig（第一个世界修改动作；单方块、MEDIUM、需要确认）
const DIG_DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.MC_DIG_TIMEOUT_MS || '30000', 10)
const DIG_MAX_DISTANCE = Number.parseFloat(process.env.MC_DIG_MAX_DISTANCE || '5')
//: 每次调用读取：测量与 mineflayer 的 canDigBlock 同口径（眼睛 → 方块中心）
function digTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_DIG_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : DIG_DEFAULT_TIMEOUT_MS
}

function digMaxDistance() {
  const raw = Number.parseFloat(process.env.MC_DIG_MAX_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : DIG_MAX_DISTANCE
}

// Phase 4C：place（放置单个方块；MEDIUM，需要确认）+ inventory 只读切片
const PLACE_DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.MC_PLACE_TIMEOUT_MS || '30000', 10)
const PLACE_MAX_DISTANCE = Number.parseFloat(process.env.MC_PLACE_MAX_DISTANCE || '5')
//: 只允许这六个方向（绝不接受任意 {dx,dy,dz} 或浮点方向）
const PLACE_FACES = Object.freeze({
  up: { x: 0, y: 1, z: 0 },
  down: { x: 0, y: -1, z: 0 },
  north: { x: 0, y: 0, z: -1 },
  south: { x: 0, y: 0, z: 1 },
  east: { x: 1, y: 0, z: 0 },
  west: { x: -1, y: 0, z: 0 },
})
const MAX_PLACE_ITEM_CHARS = 64
//: 背包切片最多列多少种物品（有界；LLM 不需要看全部原始 slot）
const INVENTORY_MAX_KINDS = 40

function placeTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_PLACE_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : PLACE_DEFAULT_TIMEOUT_MS
}

function placeMaxDistance() {
  const raw = Number.parseFloat(process.env.MC_PLACE_MAX_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : PLACE_MAX_DISTANCE
}

/** 物品名归一化：`minecraft:dirt` / `dirt` 视为同一个（比较用，回执仍报实际名）。 */
function normalizeItemName(name) {
  return String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/^minecraft:/, '')
}

/** 只读背包切片（§四）：按物品名聚合，绝不外泄 slot/NBT/window/容器/盔甲/cursor。 */
function inventorySlice(bot) {
  // 在线判定只看"有没有 bot"（路由传 state.bot；断线时它是 null）——
  // 不依赖模块级状态机，便于单测直测。
  if (bot === null || bot === undefined) {
    return { ok: true, online: false, selected_hotbar_slot: null, held_item: null, items: [] }
  }
  const counts = new Map()
  let raw = []
  try {
    raw = typeof bot.inventory?.items === 'function' ? bot.inventory.items() : []
  } catch (error) {
    log('warn', 'inventory read failed', { error: error.message })
    raw = []
  }
  for (const item of raw) {
    if (!item || !item.name || !item.count) continue
    const name = normalizeItemName(item.name)
    counts.set(name, (counts.get(name) || 0) + item.count)
  }
  const items = [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, INVENTORY_MAX_KINDS)
  const held = bot.heldItem
  return {
    ok: true,
    online: true,
    selected_hotbar_slot: Number.isInteger(bot.quickBarSlot) ? bot.quickBarSlot : null,
    held_item: held && held.name ? { name: normalizeItemName(held.name), count: held.count || 0 } : null,
    items,
  }
}

// Phase 4D：equip / inventory_move（背包写操作；MEDIUM，需要确认）
const EQUIP_DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.MC_EQUIP_TIMEOUT_MS || '15000', 10)
const MOVE_DEFAULT_TIMEOUT_MS = Number.parseInt(
  process.env.MC_INVENTORY_MOVE_TIMEOUT_MS || '15000',
  10,
)
//: mineflayer 玩家窗口（window id 0）的绝对槽位：0=合成产物 1-4=合成格 5-8=盔甲
//: 9-35=主背包 36-44=快捷栏 45=副手。本阶段只允许操作 **9..44**（主背包+快捷栏）。
const PLAYER_WINDOW_SLOTS = Object.freeze({ craftingEnd: 8, inventoryStart: 9, hotbarStart: 36, inventoryEnd: 45 })

function inventorySlotBounds(bot) {
  const inv = bot && bot.inventory ? bot.inventory : null
  const start = Number.isInteger(inv && inv.inventoryStart) ? inv.inventoryStart : PLAYER_WINDOW_SLOTS.inventoryStart
  const hotbarStart = Number.isInteger(inv && inv.hotbarStart)
    ? inv.hotbarStart
    : PLAYER_WINDOW_SLOTS.hotbarStart
  const end = Number.isInteger(inv && inv.inventoryEnd) ? inv.inventoryEnd : PLAYER_WINDOW_SLOTS.inventoryEnd
  return { start, hotbarStart, end, last: end - 1 }
}

function equipTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_EQUIP_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : EQUIP_DEFAULT_TIMEOUT_MS
}

function inventoryMoveTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_INVENTORY_MOVE_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : MOVE_DEFAULT_TIMEOUT_MS
}

/** 只读**调试**槽位视图（WebUI Move Test / smoke 用；LLM 工具绝不使用它）。 */
function inventorySlots(bot) {
  if (bot === null || bot === undefined) {
    return { ok: true, online: false, hotbar_start: null, inventory_start: null, slots: [] }
  }
  const bounds = inventorySlotBounds(bot)
  const slots = []
  for (let slot = bounds.start; slot < bounds.end; slot += 1) {
    const item = bot.inventory?.slots ? bot.inventory.slots[slot] : null
    if (!item || !item.name) continue
    slots.push({
      slot,
      name: normalizeItemName(item.name),
      count: item.count || 0,
      hotbar: slot >= bounds.hotbarStart,
    })
  }
  return {
    ok: true,
    online: true,
    hotbar_start: bounds.hotbarStart,
    inventory_start: bounds.start,
    slots,
  }
}

/** 按槽位稳定顺序找第一个匹配的物品（§六：不随机、不按数量、不换槽）。 */
function findInventoryItem(bot, itemName) {
  const bounds = inventorySlotBounds(bot)
  const wanted = normalizeItemName(itemName)
  for (let slot = bounds.start; slot < bounds.end; slot += 1) {
    const item = bot.inventory?.slots ? bot.inventory.slots[slot] : null
    if (item && item.name && normalizeItemName(item.name) === wanted) {
      return { item, slot }
    }
  }
  return null
}

function requireInventorySlot(value, field, bot) {
  if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value)) {
    throw new ActionError(`${field} 必须是整数`, 'slot.invalid', 400)
  }
  const bounds = inventorySlotBounds(bot)
  if (value < bounds.start || value >= bounds.end) {
    throw new ActionError(
      `${field} 超出可操作范围（${bounds.start}~${bounds.last}：主背包 + 快捷栏）`,
      'slot.invalid',
      400,
    )
  }
  return value
}

// ---------------------------------------------------------- Phase 4E: container

//: 本阶段只支持这两种**方块**（按 block.name 判定；trapped_chest / shulker / furnace /
//: hopper / dispenser / crafting_table 等一律 unsupported —— 绝不按"看起来像容器"判断）
const CONTAINER_BLOCK_TYPES = Object.freeze({
  // 归一化后的方块名：1.16.x 的 mineflayer 给的是 'chest'；老/新版本可能带 minecraft: 前缀，
  // 两种写法都接受（与 item 名一样只在比较时归一化，回执也用归一化名）
  chest: 'Chest',
  barrel: 'Barrel',
})
//: 单方块容器的容器槽位数：从真实 window 结构得出后必须等于它（双箱是 54 → 拒绝）
const CONTAINER_SLOT_COUNT = 27
//: 容器窗口里玩家背包部分的槽位数（主背包 27 + 快捷栏 9）
const CONTAINER_PLAYER_SLOTS = 36
const CONTAINER_DIRECTIONS = Object.freeze(['withdraw', 'deposit'])
const CONTAINER_DEFAULT_TIMEOUT_MS = Number.parseInt(
  process.env.MC_CONTAINER_TIMEOUT_MS || '30000',
  10,
)
const CONTAINER_MAX_DISTANCE = Number.parseFloat(process.env.MC_CONTAINER_MAX_DISTANCE || '5')

function containerTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_CONTAINER_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : CONTAINER_DEFAULT_TIMEOUT_MS
}

function containerMaxDistance() {
  const raw = Number.parseFloat(process.env.MC_CONTAINER_MAX_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : CONTAINER_MAX_DISTANCE
}

/** §五：方块坐标必须是整数（容器交互没有小数坐标），且在世界边界内。 */
function validateBlockCoords(params) {
  const coords = {}
  for (const name of ['x', 'y', 'z']) {
    const value = params[name]
    if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value)) {
      throw new ActionError(`坐标 ${name} 必须是整数（方块坐标没有小数）`, 'action.invalid', 400)
    }
    coords[name] = value
  }
  if (Math.abs(coords.x) > 3.0e7 || Math.abs(coords.z) > 3.0e7 || coords.y < -512 || coords.y > 2048) {
    throw new ActionError('坐标超出 Minecraft 世界边界', 'action.invalid', 400)
  }
  return coords
}

/** 方块名归一化：去掉 minecraft: 前缀（回执也用归一化名，与 item 名同一套规则）。 */
function normalizeBlockName(name) {
  return String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/^minecraft:/, '')
}

function blockNameOf(block) {
  return block && block.name ? normalizeBlockName(block.name) : ''
}

/** 支持的单方块容器的显示名（空串 = 本阶段不支持）。 */
function containerTypeLabel(blockName) {
  return CONTAINER_BLOCK_TYPES[normalizeBlockName(blockName)] || ''
}

/** §六/§七：只按 block.name 判类型（不靠 blockEntity、不靠"看起来像"）→ 否则 unsupported。 */
function requireContainerBlock(bot, params) {
  const block = bot.blockAt(new Vec3(params.x, params.y, params.z))
  if (!block) {
    throw new ActionError(
      `目标位置没有加载方块（${params.x},${params.y},${params.z}）`,
      'block.unavailable',
      404,
    )
  }
  if (!containerTypeLabel(block.name)) {
    throw new ActionError(
      `这个位置不是箱子或桶（${block.name}）——本阶段只支持单方块 Chest / Barrel`,
      'container.unsupported',
      422,
      { block: block.name },
    )
  }
  return block
}

/** §八/§九：眼睛 → 容器方块中心的距离（与 dig/place 同口径）；超了拒绝，绝不自己走过去。 */
function requireContainerDistance(bot, block) {
  const maxDistance = containerMaxDistance()
  const center = block.position.offset(0.5, 0.5, 0.5)
  const eyes = bot.entity.position.offset(0, 1.65, 0)
  const distance = eyes.distanceTo(center)
  if (distance > maxDistance) {
    throw new ActionError(
      `容器距离 ${distance.toFixed(1)} 格，超过上限 ${maxDistance} 格（本阶段不会自己走过去）`,
      'container.too_far',
      422,
      { distance: round2(distance) },
    )
  }
}

/** 容器槽位数：**从真实窗口结构推导**（不硬编码 27）；拿不到结构返回 null。 */
function containerSlotCountOf(window) {
  if (!window || !Number.isInteger(window.inventoryStart)) return null
  return window.inventoryStart
}

/** §十/§十二：单方块容器 —— 容器侧必须是 27 格、玩家侧必须是 36 格（双箱 54 → 拒绝）。 */
function requireSingleContainerWindow(window) {
  const size = containerSlotCountOf(window)
  const playerSlots =
    Number.isInteger(window.inventoryEnd) && Number.isInteger(window.inventoryStart)
      ? window.inventoryEnd - window.inventoryStart
      : null
  if (size !== CONTAINER_SLOT_COUNT || playerSlots !== CONTAINER_PLAYER_SLOTS) {
    throw new ActionError(
      `只支持单方块容器（容器槽位 ${size === null ? '未知' : size}，玩家背包 ${playerSlots === null ? '未知' : playerSlots} 格）`,
      'container.unsupported',
      422,
      size === null ? null : { container_slots: size, player_slots: playerSlots },
    )
  }
  return size
}

/** 玩家窗口绝对槽位（9..44，Phase 4D 的编号）→ 容器窗口里的绝对槽位。 */
function windowSlotForInventorySlot(window, bot, inventorySlot) {
  const bounds = inventorySlotBounds(bot)
  const offset = window.inventoryStart - bounds.start
  const windowSlot = inventorySlot + offset
  if (
    !Number.isInteger(windowSlot) ||
    windowSlot < window.inventoryStart ||
    windowSlot >= window.inventoryEnd
  ) {
    throw new ActionError('容器窗口里的玩家背包结构与预期不符', 'container.unsupported', 422)
  }
  return windowSlot
}

function readWindowSlot(window, slot) {
  const item = window && Array.isArray(window.slots) ? window.slots[slot] : null
  return item && item.name ? item : null
}

/** LLM 只看到 {name, count}（不含 NBT / 内部 id / cursor / window 对象）。 */
function describeItem(item) {
  return item && item.name ? { name: normalizeItemName(item.name), count: item.count || 0 } : null
}

function itemCountOf(item, wantedName) {
  if (!item || !item.name) return 0
  return normalizeItemName(item.name) === wantedName ? item.count || 0 : 0
}

/** §二十四：真实堆叠上限 —— item.stackSize，其次 minecraft-data；拿不到 → 0（保守：不允许合并）。 */
function itemStackCapacity(bot, item) {
  if (!item) return 0
  if (Number.isFinite(item.stackSize) && item.stackSize > 0) return item.stackSize
  const entry =
    bot && bot.registry && bot.registry.items && Number.isFinite(item.type)
      ? bot.registry.items[item.type]
      : null
  if (entry && Number.isFinite(entry.stackSize) && entry.stackSize > 0) return entry.stackSize
  return 0
}

/** §十一：只返回**非空**容器槽位（槽位号是容器内的 0..size-1；不带 window / NBT / cursor）。 */
function readContainerSlots(window, size) {
  const slots = []
  for (let slot = 0; slot < size; slot += 1) {
    const item = readWindowSlot(window, slot)
    if (!item) continue
    slots.push({ slot, name: normalizeItemName(item.name), count: item.count || 0 })
  }
  return slots
}

/** 关闭窗口（幂等：当前窗口不是它 → 什么都不做）。绝不抛：把错误如实回给调用方。 */
function closeContainerWindow(bot, window) {
  if (!window) return { ok: true, error: '' }
  if (bot && bot.currentWindow !== undefined && bot.currentWindow !== window) {
    return { ok: true, error: '' }
  }
  try {
    if (bot && typeof bot.closeWindow === 'function') {
      const pending = bot.closeWindow(window)
      // pre-1.17 的 resync click 失败不代表"窗口还开着"（close_window 包已同步发出）
      if (pending && typeof pending.catch === 'function') pending.catch(() => {})
    } else if (typeof window.close === 'function') {
      window.close()
    }
    return { ok: true, error: '' }
  } catch (error) {
    return { ok: false, error: String(error && error.message ? error.message : error) }
  }
}

/** 正常收尾的关闭：先摘掉 controller 上的记录（cleanup 不再重复关），再真的关。 */
function closeAndForget(bot, controller, window) {
  if (controller) controller.window = null
  return closeContainerWindow(bot, window)
}

/** cleanup 的兜底关闭：controller.window 是动作自己记下的窗口（至多一次、绝不抛）。 */
function closeTrackedWindow(bot, controller) {
  const window = controller ? controller.window : null
  if (!window) return
  controller.window = null
  const closed = closeContainerWindow(bot, window)
  if (!closed.ok) {
    log('warn', 'container cleanup close failed', { error: closed.error })
  }
}

/** 打开容器窗口：失败 → container.open_failed；超时/取消期间才开出来 → 立刻关掉再取消。 */
async function openContainerWindow(bot, block, token, controller) {
  if (bot.currentWindow) {
    // 上一次异常可能留下一个开着的窗口：openBlock 会等不到新的 windowOpen，先 best-effort 关掉
    log('warn', 'closing a leftover window before opening a container', {
      window_id: bot.currentWindow.id,
    })
    closeContainerWindow(bot, bot.currentWindow)
  }
  if (typeof bot.openContainer !== 'function') {
    throw new ActionError('当前 runtime 不支持打开容器', 'container.open_failed', 500)
  }
  let window = null
  try {
    window = await bot.openContainer(block)
  } catch (error) {
    const message = String(error && error.message ? error.message : error)
    throw new ActionError(`打开容器失败：${message}`, 'container.open_failed', 500)
  }
  if (token && token.cancelled) {
    closeContainerWindow(bot, window)
    throw new ActionCancelled(token.reason)
  }
  if (!window || !Array.isArray(window.slots)) {
    throw new ActionError('服务器没有返回可用的容器窗口', 'container.open_failed', 500)
  }
  if (controller) controller.window = window
  return window
}

/** §二十-§二十三：source 的**真实状态**校验（打开窗口之后按现状判定，旧 inspect 结果不算数）。 */
function requireContainerTransferSource(source, params) {
  if (!source || !source.name) {
    throw new ActionError('要移动的那个槽位是空的', 'item.not_found', 404)
  }
  const actual = normalizeItemName(source.name)
  if (actual !== params.item) {
    throw new ActionError(`槽位上是 ${actual}，不是 ${params.item}`, 'item.changed', 409, {
      expected: params.item,
      actual,
    })
  }
  if ((source.count || 0) < params.count) {
    throw new ActionError(
      `槽位上只有 ${source.count || 0} 个，不够 ${params.count} 个`,
      'item.count_insufficient',
      409,
      { available: source.count || 0, requested: params.count },
    )
  }
}

/** §二十一/§二十三：destination 空或同名未满才允许；否则拒绝（绝不交换、绝不换槽）。 */
function requireContainerTransferDestination(bot, destination, params) {
  if (!destination || !destination.name) return
  const actual = normalizeItemName(destination.name)
  const capacity = itemStackCapacity(bot, destination)
  const stackable = actual === params.item && capacity > 0 && (destination.count || 0) < capacity
  if (!stackable) {
    throw new ActionError(`目标槽位已经被 ${actual} 占用`, 'destination.occupied', 409, {
      actual,
    })
  }
}

/** transfer 抛错 → 稳定错误码（窗口没了 ≠ 普通失败）。 */
function classifyContainerTransferError(error) {
  const message = String(error && error.message ? error.message : error)
  if (/destination full/i.test(message)) {
    return new ActionError('目标槽位放不下', 'destination.occupied', 409)
  }
  if (/window|closed/i.test(message)) {
    return new ActionError('容器窗口已经关闭', 'container.closed', 409)
  }
  return new ActionError(`搬运物品失败：${message}`, 'action.failed', 500)
}

// ---------------------------------------------------------- Phase 4F: crafting

//: 只支持**玩家自身 2×2 背包合成**（craftingTable = null）；工作台留给后续阶段
const CRAFT_DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.MC_CRAFT_TIMEOUT_MS || '30000', 10)
const LOOKUP_DEFAULT_TIMEOUT_MS = Number.parseInt(
  process.env.MC_RECIPE_LOOKUP_TIMEOUT_MS || '10000',
  10,
)
//: recipe_id 长度上限（可读签名形如 ``stick*4=oak_planks*2``）
const CRAFT_MAX_RECIPE_ID_CHARS = 200
//: 一次 lookup 最多列多少个 2×2 配方（有界；按"材料齐了"优先 + 名字稳定排序）
const RECIPE_MAX_ENTRIES = 12

function craftTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_CRAFT_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : CRAFT_DEFAULT_TIMEOUT_MS
}

function recipeLookupTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_RECIPE_LOOKUP_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : LOOKUP_DEFAULT_TIMEOUT_MS
}

/** prismarine-recipe 的 Recipe 类（按 registry 懒加载并缓存：不同版本各自的配方表）。 */
const RECIPE_CLASSES = new Map()
function recipeClassFor(bot) {
  const registry = bot && bot.registry
  if (!registry) throw new ActionError('罐头还没有进入世界（拿不到物品表）', 'action.not_online', 400)
  const key = `${registry.version && registry.version.minecraftVersion ? registry.version.minecraftVersion : 'unknown'}`
  let cached = RECIPE_CLASSES.get(key)
  if (!cached) {
    cached = require('prismarine-recipe')(registry).Recipe
    RECIPE_CLASSES.set(key, cached)
  }
  return cached
}

function registryItemName(registry, id) {
  const entry = registry && registry.items ? registry.items[id] : null
  return entry && entry.name ? normalizeItemName(entry.name) : ''
}

function registryItemId(registry, name) {
  const entry = registry && registry.itemsByName ? registry.itemsByName[normalizeItemName(name)] : null
  return entry ? entry.id : null
}

/** 配方的材料清单（同名合并；只返回 {name, count}，不泄露 id/metadata）。 */
function describeRecipeIngredients(recipe, registry) {
  const counts = new Map()
  const add = (cell, amount) => {
    if (!cell || cell.id === -1 || cell.id === null || cell.id === undefined) return
    const name = registryItemName(registry, cell.id)
    if (!name) return
    counts.set(name, (counts.get(name) || 0) + amount)
  }
  if (recipe.inShape) for (const row of recipe.inShape) for (const cell of row) add(cell, 1)
  if (recipe.outShape) for (const row of recipe.outShape) for (const cell of row) add(cell, 1)
  if (recipe.ingredients) for (const cell of recipe.ingredients) add(cell, 1)
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.count - b.count)
}

function describeRecipeResult(recipe, registry) {
  return {
    name: registryItemName(registry, recipe.result.id),
    count_per_craft: recipe.result.count || 1,
  }
}

/** 可读的规范签名：``stick*4=oak_planks*2``（需要工作台的加 ``!`` 前缀）。 */
function readableRecipeKey(recipe, registry) {
  const result = describeRecipeResult(recipe, registry)
  const ingredients = describeRecipeIngredients(recipe, registry)
  const body = ingredients.map((entry) => `${entry.name}*${entry.count}`).join('+')
  return `${recipe.requiresTable ? '!' : ''}${result.name}*${result.count_per_craft}=${body}`
}

/**
 * 稳定 recipe_id（§七）：可读规范签名；只有**同一个物品的配方里出现同名签名**时才追加
 * 形状摘要后缀（按形状排序的稳定 hash），绝不使用数组下标。
 */
function recipeIdOf(recipe, registry) {
  const base = readableRecipeKey(recipe, registry)
  return base
}

function recipeIdsForList(recipes, registry) {
  const keys = recipes.map((recipe) => readableRecipeKey(recipe, registry))
  const duplicates = new Set(keys.filter((key, index) => keys.indexOf(key) !== index))
  return recipes.map((recipe, index) => {
    const key = keys[index]
    if (!duplicates.has(key)) return key
    return `${key}~${shapeTagOf(recipe)}`
  })
}

/** 形状摘要（只在同名签名冲突时使用）：把形状压成 w×h + 每格物品名的短 hash。 */
function shapeTagOf(recipe) {
  const crypto = require('crypto')
  const shape = recipe.inShape
    ? recipe.inShape.map((row) => row.map((cell) => (cell && cell.id !== -1 ? String(cell.id) : '-')).join(','))
    : [`shapeless:${(recipe.ingredients || []).map((cell) => (cell ? String(cell.id) : '-')).join(',')}`]
  return crypto.createHash('sha1').update(JSON.stringify(shape)).digest('hex').slice(0, 6)
}

/** recipe_id → 目标物品名（第一个 ``*`` 之前就是结果物品名；物品名里不会出现 ``*``）。 */
function itemNameFromRecipeId(recipeId) {
  const body = String(recipeId || '').replace(/^!/, '')
  const star = body.indexOf('*')
  if (star <= 0) return ''
  return normalizeItemName(body.slice(0, star))
}

/** §二十五：按名字汇总**整个玩家背包**（不只 heldItem；crafted item 常落在别的槽位）。 */
function countInventoryItem(bot, itemName) {
  const wanted = normalizeItemName(itemName)
  if (!wanted) return 0
  let total = 0
  let items = []
  try {
    items = typeof bot.inventory?.items === 'function' ? bot.inventory.items() : []
  } catch (error) {
    log('warn', 'inventory read failed', { error: error.message })
    items = []
  }
  for (const item of items) {
    if (!item || !item.name) continue
    if (normalizeItemName(item.name) === wanted) total += item.count || 0
  }
  return total
}

function inventoryCountsFor(bot, names) {
  const counts = {}
  for (const name of names) counts[normalizeItemName(name)] = countInventoryItem(bot, name)
  return counts
}

/** 语义投影（§五/§九）：只有 result / requires_table / available / ingredients。 */
function describeRecipe(recipe, registry, available) {
  const result = describeRecipeResult(recipe, registry)
  return {
    recipe_id: recipeIdOf(recipe, registry),
    result,
    requires_table: Boolean(recipe.requiresTable),
    available: Boolean(available),
    ingredients: describeRecipeIngredients(recipe, registry),
  }
}

/** 当前物品的配方拆分：全部 / 2×2 可执行 / 现在材料就够的。 */
//: Phase 4G：工作台的最大交互距离（与 dig/place/container 同口径：眼睛 → 方块中心）。
//: 给 → crafting_table_too_far，绝不自己走过去。
const CRAFTING_TABLE_MAX_DISTANCE = Number.parseFloat(
  process.env.MC_CRAFTING_TABLE_MAX_DISTANCE || '5',
)
//: 本阶段**只**认这一种工作台（stonecutter / smithing_table / cartography 等一律 invalid）
const CRAFTING_TABLE_BLOCK = 'crafting_table'

function craftingTableMaxDistance() {
  const raw = Number.parseFloat(process.env.MC_CRAFTING_TABLE_MAX_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : CRAFTING_TABLE_MAX_DISTANCE
}

/**
 * crafting_table 参数校验（§五）：要么没有，要么**明确的整数方块坐标**。
 * 绝不接受 ``"nearest"`` / ``"auto"`` / ``"any"`` 这种隐式目标 ——
 * 用户确认的必须是一个明确的世界交互对象。
 */
function validateCraftingTableParam(value) {
  if (value === undefined || value === null) return null
  if (typeof value !== 'object' || Array.isArray(value)) {
    throw new ActionError(
      'crafting_table 必须是 {x, y, z} 这样的方块坐标（不接受 nearest / auto）',
      'table.invalid',
      400,
    )
  }
  const coords = {}
  for (const name of ['x', 'y', 'z']) {
    const raw = value[name]
    if (typeof raw !== 'number' || !Number.isFinite(raw) || !Number.isInteger(raw)) {
      throw new ActionError(`crafting_table.${name} 必须是整数`, 'table.invalid', 400)
    }
    coords[name] = raw
  }
  if (Math.abs(coords.x) > 3.0e7 || Math.abs(coords.z) > 3.0e7 || coords.y < -512 || coords.y > 2048) {
    throw new ActionError('crafting_table 坐标超出 Minecraft 世界边界', 'table.invalid', 400)
  }
  return coords
}

/**
 * §七/§十八：实时验证工作台方块（lookup 与 craft 都要跑，绝不用旧对象）。
 * 没有（区块未加载 / 被挖掉 → air）→ table.missing；不是工作台 → table.invalid；
 * 太远 → table.too_far。三种都绝不继续。
 */
function requireCraftingTableBlock(bot, coords) {
  if (!coords) return null
  const block = bot.blockAt(new Vec3(coords.x, coords.y, coords.z))
  if (!block || isAir(block.name)) {
    throw new ActionError(
      `那里没有工作台（${coords.x},${coords.y},${coords.z}）`,
      'table.missing',
      404,
      { crafting_table: coords },
    )
  }
  const blockName = normalizeBlockName(block.name)
  if (blockName !== CRAFTING_TABLE_BLOCK) {
    throw new ActionError(
      `那个位置不是工作台（${block.name}）——本阶段只认 crafting_table`,
      'table.invalid',
      422,
      { block: block.name, crafting_table: coords },
    )
  }
  const maxDistance = craftingTableMaxDistance()
  const center = block.position.offset(0.5, 0.5, 0.5)
  const eyes = bot.entity.position.offset(0, 1.65, 0)
  const distance = eyes.distanceTo(center)
  if (distance > maxDistance) {
    throw new ActionError(
      `工作台距离 ${distance.toFixed(1)} 格，超过上限 ${maxDistance} 格（本阶段不会自己走过去）`,
      'table.too_far',
      422,
      { distance: round2(distance), crafting_table: coords },
    )
  }
  return block
}

function recipeSetsFor(bot, itemName, craftingTable = null) {
  const registry = bot.registry
  const entry = registry && registry.itemsByName ? registry.itemsByName[normalizeItemName(itemName)] : null
  if (!entry) return null
  const Recipe = recipeClassFor(bot)
  const all = Recipe.find(entry.id, null)
  // 没有工作台 → 只有 2×2 能在玩家背包里做；给了工作台 → 2×2 与 3×3 都能做
  const executable = craftingTable
    ? all
    : all.filter((recipe) => !recipe.requiresTable)
  const craftable =
    typeof bot.recipesFor === 'function' ? bot.recipesFor(entry.id, null, 1, craftingTable || null) : []
  const craftableIds = new Set(recipeIdsForList(craftable, registry))
  return { entry, all, twoByTwo: executable, craftableIds, craftingTable }
}

// ------------------------------------------------- Phase 4H: dropped items

// Phase 4K：找方块是纯查询（遍历已加载的 section），5s 足够
const FIND_BLOCKS_TIMEOUT_MS = Number.parseInt(
  process.env.MC_FIND_BLOCKS_TIMEOUT_MS || '5000',
  10,
)

// Phase 4K：找方块（只读；范围与条数都有硬上限，绝不允许"扫全世界"）
const FIND_BLOCKS_DEFAULT_DISTANCE = Number.parseInt(
  process.env.MC_FIND_BLOCKS_MAX_DISTANCE || '16',
  10,
)
const FIND_BLOCKS_DEFAULT_RESULTS = Number.parseInt(
  process.env.MC_FIND_BLOCKS_MAX_RESULTS || '8',
  10,
)
const FIND_BLOCKS_MAX_DISTANCE = Number.parseInt(
  process.env.MC_FIND_BLOCKS_HARD_MAX_DISTANCE || '32',
  10,
)
const FIND_BLOCKS_MAX_RESULTS = Number.parseInt(
  process.env.MC_FIND_BLOCKS_HARD_MAX_RESULTS || '16',
  10,
)
const FIND_BLOCKS_MAX_NAMES = 8

// Phase 4J：能力查询是纯计算（一次 blockAt + canDigBlock + digTime），5s 足够
const DIG_CAPABILITY_TIMEOUT_MS = Number.parseInt(
  process.env.MC_DIG_CAPABILITY_TIMEOUT_MS || '5000',
  10,
)
const DROPPED_ITEMS_TIMEOUT_MS = Number.parseInt(
  process.env.MC_DROPPED_ITEMS_TIMEOUT_MS || '5000',
  10,
)
const PICKUP_DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.MC_PICKUP_TIMEOUT_MS || '30000', 10)
const PICKUP_MAX_DISTANCE = Number.parseFloat(process.env.MC_PICKUP_MAX_DISTANCE || '16')
//: 进入这个半径就停导航、交给服务器收集（runtime 常量，第一版不暴露配置）
const PICKUP_RADIUS = 1.2
//: 一次最多把多少个掉落物给模型看（多出来的截断并在结果里说明）
const PICKUP_MAX_ITEMS = 32
//: 监督循环周期：实体还在不在 / 身份没变 / 物品没变 / 距离还行
const PICKUP_POLL_MS = 250
//: entityGone 之后再等多久背包到账（到点还没增加 → pickup_unconfirmed）
const PICKUP_INVENTORY_GRACE_MS = 1500

function pickupTimeoutMs() {
  const raw = Number.parseInt(process.env.MC_PICKUP_TIMEOUT_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : PICKUP_DEFAULT_TIMEOUT_MS
}

function pickupMaxDistance() {
  const raw = Number.parseFloat(process.env.MC_PICKUP_MAX_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : PICKUP_MAX_DISTANCE
}

//: 监督循环周期与"entityGone 后的背包宽限"每次读取（测试可以把它们调小）
function pickupPollMs() {
  const raw = Number.parseInt(process.env.MC_PICKUP_POLL_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : PICKUP_POLL_MS
}

function pickupInventoryGraceMs() {
  const raw = Number.parseInt(process.env.MC_PICKUP_INVENTORY_GRACE_MS || '', 10)
  return Number.isFinite(raw) && raw >= 0 ? raw : PICKUP_INVENTORY_GRACE_MS
}

/**
 * §五：掉落物实体在 entity metadata 里的 item 槽位。
 * 与 mineflayer 自己的判定公式一致（entities.js 的 itemDrop 分支），不写死数字。
 */
function droppedItemSlotIndex(bot) {
  const base = bot && bot.supportFeature && bot.supportFeature('itemsAreAlsoBlocks') ? 5 : 6
  return base + (bot && bot.supportFeature && bot.supportFeature('entityMetadataHasLong') ? 1 : 0)
}

/**
 * §五：**唯一**的"这是不是掉落物实体"判断（所有地方都只走这个 helper）。
 * 不假设 ``entity.name === 'item'`` 是唯一形态：mineflayer 自己同时接受 ``item_stack``。
 */
function isDroppedItemEntity(bot, entity) {
  if (!entity || typeof entity !== 'object') return false
  const raw = entity.name !== undefined && entity.name !== null ? entity.name : entity.displayName
  const name = String(raw || '').toLowerCase()
  return name === 'item' || name === 'item_stack'
}

/** 把 mineflayer 的原始物品槽解码成 ``{name, count}``（解不出来返回 null）。 */
function decodeRawItemStack(bot, raw) {
  if (!raw || typeof raw !== 'object') return null
  // 老版本明确给 present；1.21+ 用组件描述物品，**没有** present 字段（有 itemId 就是有物品）
  if (raw.present === false) return null
  if (!Number.isFinite(raw.itemId)) return null
  const entry = bot && bot.registry && bot.registry.items ? bot.registry.items[raw.itemId] : null
  if (!entry || !entry.name) return null
  return { name: normalizeItemName(entry.name), count: raw.itemCount || 1 }
}

/**
 * 掉落物的语义栈 ``{name, count}``（读不出来就返回 null，绝不猜）。
 *
 * 数据来源是 mineflayer 解析后的 ``entity.metadata``，它有两种形态：
 *   * 现代版本（1.20.2+）：**按 metadata key 索引的对象**，物品栈那一个的值类型是字符串
 *     ``'item_stack'``（见 mineflayer 自己的 `packet.metadata.some(m => m.type === 'item_stack')`）；
 *   * 老版本：同样是按 key 索引的对象，但类型是数值。
 * 所以这里先按物品表的 ``metadataKeys`` 找名字叫 item/item_stack 的那一项，
 * 找不到就扫描所有值取第一个能解码成物品栈的（Item 实体身上只有这一个栈）——
 * **完全不硬编码槽位数字**。
 */
function droppedItemStack(bot, entity) {
  if (!isDroppedItemEntity(bot, entity)) return null
  const metadata = entity.metadata
  const registryItem = bot && bot.registry && bot.registry.entitiesByName
    ? bot.registry.entitiesByName[entity.name]
    : null
  const keys = registryItem && Array.isArray(registryItem.metadataKeys)
    ? registryItem.metadataKeys
    : null
  if (metadata && typeof metadata === 'object') {
    if (keys) {
      const index = keys.findIndex((key) => key === 'item' || key === 'item_stack')
      if (index >= 0) {
        const stack = decodeRawItemStack(bot, metadata[index])
        if (stack) return stack
      }
    }
    for (const value of Object.values(metadata)) {
      const stack = decodeRawItemStack(bot, value)
      if (stack) return stack
    }
  }
  if (Array.isArray(metadata)) {
    // 原始形态（老版本/某些插件）：条目是 {key, type, value}
    const index = droppedItemSlotIndex(bot)
    const slot =
      metadata.find((entry) => entry && (entry.type === index || entry.type === 'item_stack')) ||
      metadata[index]
    const stack = decodeRawItemStack(bot, slot && slot.value)
    if (stack) return stack
  }
  if (entity.item && entity.item.name) {
    // 某些版本/插件会把栈直接挂在 entity.item 上（防御性回退，字段仍然是语义的）
    return { name: normalizeItemName(entity.item.name), count: entity.item.count || 1 }
  }
  return null
}

function entityPositionOf(entity) {
  const position = entity && entity.position
  if (!position) return null
  const { x, y, z } = position
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return null
  return { x, y, z }
}

/** 眼睛 → 实体位置的距离（与交互动作同口径：读的是 bot.entity.position 上方 1.65） */
function distanceToEntity(bot, entity) {
  const position = entityPositionOf(entity)
  if (!position || !bot || !bot.entity || !bot.entity.position) return null
  return round2(bot.entity.position.distanceTo(new Vec3(position.x, position.y, position.z)))
}

/**
 * §六/§九/§十：掉落物的**只读语义投影**（排序 + 上限）。
 * 只给 entity_id / item{name,count} / position / distance；
 * 不泄露 raw metadata / packet / 内部数字 id / entity object / UUID / velocity。
 */
function droppedItemsView(bot) {
  if (bot === null || bot === undefined) {
    return { ok: true, online: false, total: 0, truncated: false, items: [] }
  }
  const entities =
    bot.entities && typeof bot.entities === 'object' ? Object.values(bot.entities) : []
  const rows = []
  for (const entity of entities) {
    if (!isDroppedItemEntity(bot, entity)) continue
    const stack = droppedItemStack(bot, entity)
    const position = entityPositionOf(entity)
    // 读不到物品/位置（还在初始化、或数据不全）→ 宁可不列出来，也不给半个实体
    if (!stack || !position || !Number.isFinite(entity.id)) continue
    rows.push({
      entity_id: entity.id,
      item: { name: stack.name, count: stack.count },
      position: { x: round2(position.x), y: round2(position.y), z: round2(position.z) },
      distance: distanceToEntity(bot, entity),
    })
  }
  // §九：距离升序，其次 entity_id 升序（同一世界状态下输出稳定）
  rows.sort((a, b) => a.distance - b.distance || a.entity_id - b.entity_id)
  return {
    ok: true,
    online: true,
    total: rows.length,
    truncated: rows.length > PICKUP_MAX_ITEMS,
    items: rows.slice(0, PICKUP_MAX_ITEMS),
  }
}

/** §三十六：拾取监听器的挂载/摘除（幂等；cleanup 与 wait 都会调）。 */
function detachPickupListeners(bot, listeners) {
  if (!bot || !Array.isArray(listeners)) return
  for (const [event, handler] of listeners.splice(0, listeners.length)) {
    try {
      if (typeof bot.removeListener === 'function') bot.removeListener(event, handler)
      else if (typeof bot.off === 'function') bot.off(event, handler)
    } catch (error) {
      log('warn', 'pickup listener detach failed', { event, error: error.message })
    }
  }
}

// Phase 4H.1：move_to 的到达判定 —— 只用**重新读取的**实际位置算，绝不相信 Pathfinder 的 resolve。
//
// 距离口径与 mineflayer 的 GoalNear 完全一致：GoalNear 在构造时就把目标取整成方块格
// （``this.x = Math.floor(x)``），它的 ``isEnd`` 比的也是 ``bot.entity.position.floored()``；
// 所以这里同样比"罐头现在占的方块格 → 目标方块格"。原始浮点三维距离（含站在方块顶上的
// 高度差与脚下的小数偏移）另算一份，只做诊断，不参与门禁。
function moveArrivalView(bot, state) {
  const position = bot && bot.entity ? bot.entity.position : null
  const cell = position && typeof position.floored === 'function' ? position.floored() : null
  const targetCell = new Vec3(state.goal.x, state.goal.y, state.goal.z)
  return {
    position,
    cell,
    distance: cell ? cell.distanceTo(targetCell) : null,
    rawDistance: position ? position.distanceTo(state.target) : null,
    // GoalNear 自己的判据（§二/§八）：唯一权威的"到没到"
    goalSatisfied: Boolean(
      cell && typeof state.goal.isEnd === 'function' && state.goal.isEnd(cell),
    ),
  }
}

/** 到达的唯一硬门禁（§五/§二十一）：实际位置满足 GoalNear 半径，与"移动了多少格"无关。 */
function moveArrival(bot, state) {
  const view = moveArrivalView(bot, state)
  return {
    ...view,
    reached: Boolean(
      view.goalSatisfied && view.distance !== null && view.distance <= MOVE_TO_RADIUS,
    ),
  }
}

/** Phase 4H.1：move_to 生命周期监听器的幂等摘除（wait 与 cleanup 都会调）。 */
function detachPathListeners(bot, listeners) {
  if (!bot || !Array.isArray(listeners)) return
  for (const [event, handler] of listeners.splice(0, listeners.length)) {
    try {
      if (typeof bot.removeListener === 'function') bot.removeListener(event, handler)
      else if (typeof bot.off === 'function') bot.off(event, handler)
    } catch (error) {
      log('warn', 'move_to listener detach failed', { event, error: error.message })
    }
  }
}

/** 收掉导航意图（setGoal(null)）：到达/失败/取消都必须清，失败只记日志。 */
function releaseMoveGoal(bot) {
  try {
    if (bot && bot.pathfinder && typeof bot.pathfinder.setGoal === 'function') {
      bot.pathfinder.setGoal(null)
    }
  } catch (error) {
    log('warn', 'move_to setGoal(null) failed', { error: error.message })
  }
}

/**
 * 方块距离的两个口径（Phase 4J 建立，Phase 4K 起由 find_blocks 共用 —— §十：只有这一套算法）：
 *   * ``goal_near`` —— 罐头**占的方块格** → 目标方块格（GoalNear 口径）
 *   * ``raw`` —— 眼睛 → 方块中心的浮点距离（**与 minecraft_dig 的距离门禁同一个量**）
 */
function blockDistanceView(bot, position) {
  const self = bot && bot.entity && bot.entity.position ? bot.entity.position : null
  const center = position.offset(0.5, 0.5, 0.5)
  const eyes = self ? self.offset(0, 1.65, 0) : null
  return {
    goal_near: self ? round2(self.floored().distanceTo(position)) : null,
    raw: eyes ? round2(eyes.distanceTo(center)) : null,
  }
}

/**
 * Phase 4K：在当前已加载的世界里找**指定方块**的位置（只读；§十五：只回答"在哪里"，
 * 绝不回答"哪一个最适合挖" —— 不返回 recommended/best/optimal）。
 *
 * 事实来源是 Mineflayer 的 ``bot.findBlocks``（默认以罐头当前位置为起点，按距离排序）；
 * 绝不自己遍历 x±N / y±N / z±N，也绝不因为找不到就自动 move / equip / dig。
 */
function findBlocksView(bot, params) {
  if (typeof bot.findBlocks !== 'function') {
    // §二十一：运行时给不出这个查询能力时如实说（结构化错误，不假装空结果）
    throw new ActionError('当前运行时无法查询方块位置', 'block.query_unavailable', 500)
  }
  const registry = bot.registry || {}
  const blocksByName = registry.blocksByName || {}
  // §二十二：名字解析不出来必须**报错**，不能返回空结果（否则模型分不清"不存在"和"附近没有"）
  const unknown = params.block_names.filter((name) => !blocksByName[name])
  if (unknown.length > 0) {
    throw new ActionError(
      `不认识的方块名：${unknown.join('、')}`,
      'block.name_unknown',
      422,
      { unknown },
    )
  }
  const ids = params.block_names.map((name) => blocksByName[name].id)
  const maxDistance = params.max_distance
  const maxResults = params.max_results
  const point = bot.entity.position.floored()
  // 多要一个：用来判断"是不是被数量上限截断了"（findBlocks 自己会 slice 到 count）
  const found = bot.findBlocks({
    point,
    matching: ids,
    maxDistance,
    count: maxResults + 1,
  })
  const truncated = found.length > maxResults
  const positions = found.slice(0, maxResults)
  const matches = positions.map((position) => {
    const block = bot.blockAt(position)
    return {
      block: { name: block ? blockNameOf(block) : 'unknown' },
      position: { x: position.x, y: position.y, z: position.z },
      distance: blockDistanceView(bot, position),
    }
  })
  // §十一：稳定排序（goal_near → raw → x → y → z），绝不按 JS 对象枚举顺序
  matches.sort(
    (a, b) =>
      (a.distance.goal_near === null ? Infinity : a.distance.goal_near) -
        (b.distance.goal_near === null ? Infinity : b.distance.goal_near) ||
      (a.distance.raw === null ? Infinity : a.distance.raw) -
        (b.distance.raw === null ? Infinity : b.distance.raw) ||
      a.position.x - b.position.x ||
      a.position.y - b.position.y ||
      a.position.z - b.position.z,
  )
  return {
    ok: true,
    query: {
      block_names: [...params.block_names],
      max_distance: maxDistance,
      max_results: maxResults,
    },
    matches,
    truncated,
  }
}

// Phase 4J：capability 的 reason 只有这几个有限取值（§十六：绝不推测"工具等级不够"）
const DIG_CAPABILITY_REASONS = Object.freeze(['air', 'too_far', 'not_diggable'])

/**
 * Phase 4J：**只读**回答"当前站在这里、当前主手拿着这个物品时，这个方块能不能挖、大概多久"。
 *
 * 事实来源全部是 mineflayer 的**运行时**（绝不自己维护方块硬度/工具等级/最佳工具表）：
 *   ``bot.blockAt`` / ``bot.heldItem`` / ``bot.canDigBlock`` / ``bot.digTime``
 * 绝不改世界、不改背包、不装备、不切槽、不移动、不导航（§二/§十一/§十二/§十八）。
 *
 * 距离分成两个口径（§十五，沿用 Phase 4H.1）：
 *   * ``goal_near`` —— 罐头**占的方块格** → 目标方块格（GoalNear 口径）
 *   * ``raw`` —— 眼睛 → 方块中心的浮点距离（**与 minecraft_dig 的距离门禁同一个量**）
 */
function digCapabilityView(bot, params) {
  const position = new Vec3(params.x, params.y, params.z)
  const block = typeof bot.blockAt === 'function' ? bot.blockAt(position) : null
  if (!block) {
    // §七：那个位置没有方块（没加载 / 超出世界）→ 结构化错误，不是"能不能挖"的回答
    throw new ActionError(
      `那个位置没有方块（${params.x},${params.y},${params.z}）`,
      'block.unavailable',
      404,
    )
  }
  const blockName = blockNameOf(block) || 'unknown'
  const held = bot.heldItem && bot.heldItem.name ? bot.heldItem : null
  const heldItem = held ? { name: normalizeItemName(held.name), count: held.count } : null
  const distance = blockDistanceView(bot, position)
  const raw = distance.raw
  const view = {
    ok: true,
    position: { x: params.x, y: params.y, z: params.z },
    block: { name: blockName },
    held_item: heldItem,
    distance,
    can_dig: false,
    dig_time_ms: null,
    reason: null,
  }
  if (isAir(blockName)) {
    // §八：空气是正常数据，不是服务器错误
    return { ...view, reason: 'air' }
  }
  const maxDistance = digMaxDistance()
  if (raw !== null && raw > maxDistance) {
    // §九：复用 dig 的交互距离语义；**绝不**为了这个查询让罐头移动
    return { ...view, reason: 'too_far' }
  }
  const diggable = typeof bot.canDigBlock === 'function' ? Boolean(bot.canDigBlock(block)) : false
  if (!diggable) {
    // §十一/§十六：canDigBlock 说了算，不猜原因（工具不对？被保护？统一 not_diggable）
    return { ...view, reason: 'not_diggable' }
  }
  let digTime = null
  if (typeof bot.digTime === 'function') {
    try {
      const value = bot.digTime(block)
      // §十二：不可挖/算不出来时是 null（绝不返回负数，也不把 Infinity 传出去）
      if (Number.isFinite(value) && value >= 0) digTime = Math.round(value)
    } catch (error) {
      log('warn', 'dig_capability digTime failed', { error: error.message })
    }
  }
  return { ...view, can_dig: true, dig_time_ms: digTime }
}

/** 停掉导航意图（setGoal(null) + 清控制位），失败只记日志。 */
function releasePickupNavigation(bot) {
  try {
    if (bot && bot.pathfinder && typeof bot.pathfinder.setGoal === 'function') {
      bot.pathfinder.setGoal(null)
    }
  } catch (error) {
    log('warn', 'pickup setGoal(null) failed', { error: error.message })
  }
  try {
    if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
  } catch (error) {
    log('warn', 'pickup clearControlStates failed', { error: error.message })
  }
}

// Phase 3D：follow_player（动态跟随）
const FOLLOW_DEFAULT_DISTANCE = 2.5
const FOLLOW_MIN_DISTANCE = 1.5
const FOLLOW_MAX_DISTANCE = 6
const FOLLOW_TIMEOUT_MS = Number.parseInt(process.env.MC_FOLLOW_TIMEOUT_MS || '120000', 10)
const FOLLOW_MAX_CHASE_DISTANCE = Number.parseFloat(
  process.env.MC_FOLLOW_MAX_CHASE_DISTANCE || '64',
)
//: 目标丢失宽限期：允许实体短暂刷新，连续超过才 FAILED player_lost（每次调用读取，便于测试）
function followLostGraceMs() {
  const raw = Number.parseInt(process.env.MC_FOLLOW_LOST_GRACE_MS || '', 10)
  return Number.isFinite(raw) && raw > 0 ? raw : 3000
}

function followMaxChaseDistance() {
  const raw = Number.parseFloat(process.env.MC_FOLLOW_MAX_CHASE_DISTANCE || '')
  return Number.isFinite(raw) && raw > 0 ? raw : FOLLOW_MAX_CHASE_DISTANCE
}
const CONNECT_TIMEOUT_MS = Math.max(5, CONNECT_TIMEOUT_S) * 1000
const EVENT_RETRY_DELAYS_MS = [0, 1000, 2000, 4000, 8000, 16000]
const EVENT_QUEUE_MAX = 200
const CHAT_DEDUPE_TTL_MS = 1000
const HEALTH_LOG_INTERVAL_MS = 60000

// ----------------------------------------------------------------- state machine

const PHASES = [
  'DISCONNECTED',
  'CONNECTING',
  'AUTHENTICATING',
  'CONNECTED',
  'SPAWNING',
  'ONLINE',
  'DISCONNECTING',
  'ERROR',
]
const ACTIVE_PHASES = new Set(['CONNECTING', 'AUTHENTICATING', 'CONNECTED', 'SPAWNING', 'ONLINE'])
const CONNECTING_PHASES = new Set(['CONNECTING', 'AUTHENTICATING'])

const state = {
  phase: 'DISCONNECTED',
  sessionId: null,
  bot: null,
  host: null,
  port: null,
  username: null,
  authMode: null,
  dimension: null,
  position: null,
  health: null,
  lastError: null,
  kickedReason: null,
  connectedAt: null,
  startedAt: Date.now(),
  connectTimer: null,
  stopping: false,
}

function log(level, message, fields) {
  const line = { ts: new Date().toISOString(), level, scope: 'mc-runtime', message, ...fields }
  console.log(JSON.stringify(line))
}

// ------------------------------------------------------------------ event push

// 有界回调队列：CatooBot 暂时够不着时事件先排队，超出上限丢最旧的。
const eventQueue = []
let flushTimer = null
let flushing = false

function pushEvent(event, fields) {
  const payload = {
    event,
    session_id: state.sessionId,
    timestamp: Date.now() / 1000,
    ...fields,
  }
  if (eventQueue.length >= EVENT_QUEUE_MAX) {
    eventQueue.shift()
    log('warn', 'event queue full; dropped oldest event', { event })
  }
  eventQueue.push({ payload, attempts: 0, nextAt: 0 })
  scheduleFlush(0)
}

function scheduleFlush(delayMs) {
  if (flushTimer !== null) return
  flushTimer = setTimeout(() => {
    flushTimer = null
    void flushEvents()
  }, delayMs)
  if (typeof flushTimer.unref === 'function') flushTimer.unref()
}

async function flushEvents() {
  // 互斥：并发 flush 会把同一条事件发两遍、或把没发过的 shift 掉。
  if (flushing) {
    scheduleFlush(50)
    return
  }
  flushing = true
  try {
    while (eventQueue.length > 0) {
      const item = eventQueue[0]
      const now = Date.now()
      if (item.nextAt > now) {
        scheduleFlush(item.nextAt - now)
        return
      }
      if (!CALLBACK_URL) {
        // 没有回调地址（WebUI 未开或外部托管未配置）：只能丢弃，靠轮询兜底。
        eventQueue.shift()
        log('warn', 'callback url not configured; event dropped', {
          event: item.payload.event,
        })
        continue
      }
      try {
        const headers = { 'Content-Type': 'application/json' }
        if (CALLBACK_TOKEN) headers.Authorization = `Bearer ${CALLBACK_TOKEN}`
        const response = await fetch(CALLBACK_URL, {
          method: 'POST',
          headers,
          body: JSON.stringify(item.payload),
          signal: AbortSignal.timeout(5000),
        })
        if (!response.ok) throw new Error(`callback answered ${response.status}`)
        eventQueue.shift()
        item.attempts = 0
      } catch (error) {
        item.attempts += 1
        if (item.attempts >= EVENT_RETRY_DELAYS_MS.length) {
          eventQueue.shift()
          log('error', 'event delivery failed permanently; dropped', {
            event: item.payload.event,
            error: String(error && error.message ? error.message : error),
          })
          continue
        }
        const retryDelay = EVENT_RETRY_DELAYS_MS[item.attempts]
        item.nextAt = Date.now() + retryDelay
        scheduleFlush(retryDelay)
        return
      }
    }
  } finally {
    flushing = false
  }
  // 排队期间又有新事件进来 → 再跑一轮。
  if (eventQueue.length > 0) scheduleFlush(0)
}

// ----------------------------------------------------------------- auth（本地文件）

function readAuth() {
  // 认证信息只在本地：auth.json 不进 Git、不经 HTTP 传输。读不到就用
  // offline 默认身份，让用户第一眼就能连上 offline 服务器。
  try {
    const raw = fs.readFileSync(AUTH_FILE, 'utf8')
    const parsed = JSON.parse(raw)
    const mode = parsed.mode === 'microsoft' ? 'microsoft' : 'offline'
    if (mode === 'microsoft') {
      return {
        mode,
        email: String(parsed.email || parsed.username || ''),
        password: String(parsed.password || ''),
      }
    }
    return { mode, username: String(parsed.username || 'GuanTou') }
  } catch {
    return { mode: 'offline', username: 'GuanTou' }
  }
}

// ------------------------------------------------------------------ world perception

// Phase 2：Raw World Snapshot（只读「眼睛」；没有任何移动/挖掘/放置能力）。
// 所有空间对象同时保留世界坐标与相对量，bearing / relative_direction 由这里计算，
// 绝不让 LLM 从坐标自行推断。

const RELATIVE_DIRECTIONS = [
  'front',
  'front_left',
  'front_right',
  'left',
  'right',
  'back',
  'back_left',
  'back_right',
  'above',
  'below',
]
const SECTORS_CLOCKWISE = [
  'front',
  'front_right',
  'right',
  'back_right',
  'back',
  'back_left',
  'left',
  'front_left',
]
const COMPASS = [
  'north',
  'north_east',
  'east',
  'south_east',
  'south',
  'south_west',
  'west',
  'north_west',
]

// 值得关注的方块（POI）：功能方块 / 光源 / 传送门 / 矿石等。
const POI_EXACT = new Set([
  'crafting_table',
  'furnace',
  'blast_furnace',
  'smoker',
  'chest',
  'trapped_chest',
  'ender_chest',
  'barrel',
  'anvil',
  'chipped_anvil',
  'damaged_anvil',
  'grindstone',
  'enchanting_table',
  'brewing_stand',
  'cauldron',
  'lectern',
  'loom',
  'smithing_table',
  'cartography_table',
  'stonecutter',
  'fletching_table',
  'bell',
  'jukebox',
  'note_block',
  'beacon',
  'conduit',
  'respawn_anchor',
  'lodestone',
  'hopper',
  'dropper',
  'dispenser',
  'observer',
  'comparator',
  'repeater',
  'daylight_detector',
  'bookshelf',
  'torch',
  'soul_torch',
  'redstone_torch',
  'lantern',
  'soul_lantern',
  'glowstone',
  'sea_lantern',
  'shroomlight',
  'end_portal_frame',
  'nether_portal',
  'end_portal',
  'spawner',
])
const POI_SUFFIXES = ['_bed', '_door', '_sign', '_banner', '_ore', '_shulker_box', '_candle']

function isInterestingBlock(name) {
  if (POI_EXACT.has(name)) return true
  for (const suffix of POI_SUFFIXES) {
    if (name.endsWith(suffix)) return true
  }
  return false
}

function facingVector(yawRad) {
  // 协议约定：yaw=0 → +Z（south），顺时针增大（90° → -X/west）。
  return { x: -Math.sin(yawRad), z: Math.cos(yawRad) }
}

function bearingTo(facing, dx, dz) {
  // 相对朝向的方位角（度）：0=正前方，正值=右侧，范围 [-180, 180]。
  const cross = facing.x * dz - facing.z * dx
  const dot = facing.x * dx + facing.z * dz
  return Math.round(Math.atan2(cross, dot) * (180 / Math.PI) * 10) / 10
}

function relativeDirection(bearingDeg, dy, horizontalDist) {
  if (horizontalDist < 2) {
    if (dy >= 1.5) return 'above'
    if (dy <= -1.5) return 'below'
    return 'front'
  }
  const normalized = (((bearingDeg + 22.5) % 360) + 360) % 360
  return SECTORS_CLOCKWISE[Math.floor(normalized / 45)]
}

function compassDirection(dx, dz) {
  // 世界方位：north=-Z，east=+X。0=N，顺时针。
  const deg = (((Math.atan2(dx, -dz) * 180) / Math.PI) + 360) % 360
  return COMPASS[Math.round(deg / 45) % 8]
}

function spatialFields(bot, position) {
  const self = bot.entity.position
  const dx = position.x - self.x
  const dy = position.y - self.y
  const dz = position.z - self.z
  const horizontal = Math.sqrt(dx * dx + dz * dz)
  const facing = facingVector(bot.entity.yaw)
  const bearing = bearingTo(facing, dx, dz)
  return {
    dx: Math.round(dx * 10) / 10,
    dy: Math.round(dy * 10) / 10,
    dz: Math.round(dz * 10) / 10,
    distance: Math.round(Math.sqrt(dx * dx + dy * dy + dz * dz) * 10) / 10,
    bearing,
    relative_direction: relativeDirection(bearing, dy, horizontal),
    compass: compassDirection(dx, dz),
  }
}

function isAir(name) {
  return name === 'air' || name === 'cave_air' || name === 'void_air'
}

function timePhase(ticks) {
  if (ticks === null || ticks === undefined) return null
  const t = ((Number(ticks) % 24000) + 24000) % 24000
  if (t < 12000) return 'day'
  if (t < 13000) return 'sunset'
  if (t < 23000) return 'night'
  return 'sunrise'
}

function columnTop(bot, x, z, yTop, yBottom) {
  for (let y = yTop; y >= yBottom; y--) {
    const block = bot.blockAt(new Vec3(x, y, z))
    if (block && !isAir(block.name)) return block
  }
  return null
}

function describeBlock(bot, block) {
  const position = block.position
  const fields = spatialFields(bot, { x: position.x + 0.5, y: position.y, z: position.z + 0.5 })
  return {
    name: block.name,
    rel: { dx: fields.dx, dy: fields.dy, dz: fields.dz },
    pos: { x: position.x, y: position.y, z: position.z },
    distance: fields.distance,
    bearing: fields.bearing,
    relative_direction: fields.relative_direction,
    compass: fields.compass,
  }
}

function scanColumns(bot, radius, step, yUp, yDown, cap) {
  // 以罐头为中心的柱面表层扫描：每根柱子取最高非空气方块，天然剔除大量空气。
  const columns = []
  const self = bot.entity.position
  for (let dz = -radius; dz <= radius; dz += step) {
    for (let dx = -radius; dx <= radius; dx += step) {
      const block = columnTop(bot, self.x + dx, self.z + dz, self.y + yUp, self.y - yDown)
      if (block) {
        columns.push(describeBlock(bot, block))
        if (columns.length >= cap) return columns
      }
    }
  }
  return columns
}

function scanInteresting(bot, radius, yBand, cap) {
  // 罐头附近的「值得关注」方块（功能方块/光源/矿石…），立体小范围扫描。
  const found = []
  const seen = new Set()
  const self = bot.entity.position
  for (let dy = yBand; dy >= -yBand; dy--) {
    for (let dz = -radius; dz <= radius; dz++) {
      for (let dx = -radius; dx <= radius; dx++) {
        const block = bot.blockAt(new Vec3(self.x + dx, self.y + dy, self.z + dz))
        if (!block || !isInterestingBlock(block.name)) continue
        const key = `${block.position.x},${block.position.y},${block.position.z}`
        if (seen.has(key)) continue
        seen.add(key)
        found.push(describeBlock(bot, block))
        if (found.length >= cap) return found
      }
    }
  }
  return found
}

function buildWorldSnapshot(bot, layers) {
  const self = bot.entity.position
  const selfFields = {
    position: { x: Math.round(self.x * 100) / 100, y: Math.round(self.y * 100) / 100, z: Math.round(self.z * 100) / 100 },
    yaw: Math.round((((bot.entity.yaw * 180) / Math.PI) % 360 + 360) % 360),
    pitch: Math.round(bot.entity.pitch * (180 / Math.PI) * 10) / 10,
    dimension: bot.game?.dimension ?? null,
    health: typeof bot.health === 'number' ? bot.health : null,
    food: typeof bot.food === 'number' ? bot.food : null,
    game_mode: bot.game?.gameMode ?? null,
    held_item: bot.heldItem?.name ?? null,
  }

  const players = Object.values(bot.players)
    .filter((player) => player.entity && player.username !== bot.username)
    .map((player) => {
      const fields = spatialFields(bot, player.entity.position)
      const position = player.entity.position
      return {
        username: player.username,
        // Phase 5C：canonical identity —— mineflayer 给的玩家 UUID（离线服也有稳定 UUID）。
        // username 只用于显示/说话，绝不作为身份键（改名不该换人）。
        uuid: player.uuid ?? null,
        pos: { x: Math.round(position.x * 10) / 10, y: Math.round(position.y * 10) / 10, z: Math.round(position.z * 10) / 10 },
        ...fields,
      }
    })
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 20)

  const entities = Object.values(bot.entities)
    .filter((entity) => {
      if (!entity || !entity.position || entity.type === 'player') return false
      return Boolean(entity.name || entity.displayName)
    })
    .map((entity) => {
      const fields = spatialFields(bot, entity.position)
      return {
        type: entity.name || entity.displayName || entity.type,
        kind: entity.kind ?? null,
        pos: { x: Math.round(entity.position.x * 10) / 10, y: Math.round(entity.position.y * 10) / 10, z: Math.round(entity.position.z * 10) / 10 },
        ...fields,
      }
    })
    .sort((a, b) => a.distance - b.distance)
    .slice(0, 40)

  const feetBlock = bot.blockAt(self)
  const environment = {
    biome: feetBlock?.biome?.name ?? null,
    time_of_day_ticks: bot.time ? bot.time.timeOfDay : null,
    time_phase: bot.time ? timePhase(bot.time.timeOfDay) : null,
    weather: bot.isRaining ? (bot.thunderState ? 'thunder' : 'rain') : 'clear',
    light:
      feetBlock && (feetBlock.skyLight !== undefined || feetBlock.blockLight !== undefined)
        ? Math.max(feetBlock.skyLight ?? 0, feetBlock.blockLight ?? 0)
        : null,
    dimension: bot.game?.dimension ?? null,
  }

  const blocks = {}
  if (layers.has('near')) {
    blocks.near = { radius: 6, step: 1, columns: scanColumns(bot, 6, 1, 8, 8, 220) }
  }
  if (layers.has('local')) {
    blocks.local = { radius: 32, step: 8, columns: scanColumns(bot, 32, 8, 16, 16, 120) }
    blocks.interesting = scanInteresting(bot, 10, 3, 40)
  }
  if (layers.has('extended')) {
    const points = []
    for (const distance of [48, 96]) {
      for (let i = 0; i < 8; i++) {
        const angle = (i * Math.PI) / 4
        const x = Math.round(self.x + Math.cos(angle) * distance)
        const z = Math.round(self.z + Math.sin(angle) * distance)
        const block = columnTop(bot, x, z, self.y + 24, self.y - 24)
        if (block) {
          points.push({ ...describeBlock(bot, block), biome: block.biome?.name ?? null })
        }
      }
    }
    blocks.extended = { radius: 96, points }
  }

  return {
    self: selfFields,
    players,
    entities,
    environment,
    blocks,
  }
}

function parseLayers(query) {
  const raw = String(query || '').trim()
  if (!raw) return new Set(['near', 'local', 'extended'])
  const allowed = new Set(['near', 'local', 'extended'])
  return new Set(raw.split(',').map((part) => part.trim()).filter((part) => allowed.has(part)))
}

// ------------------------------------------------------------------ action runtime（Phase 3B/3C）

function round2(value) {
  return Math.round(value * 100) / 100
}

/** Phase 3B §六 / Phase 3C §六：共享的世界坐标校验（有限数字 + 世界边界）。 */
/**
 * Phase 4J §四：方块坐标必须是**整数**（capability 查的是"真实位置的方块"，
 * 浮点坐标会让调用者有机会指向格子里面的一个想象位置）。
 */
function validateBlockCoords(params) {
  const coords = {}
  for (const name of ['x', 'y', 'z']) {
    const value = params[name]
    if (typeof value !== 'number' || !Number.isInteger(value)) {
      throw new ActionError(`坐标 ${name} 必须是整数（方块坐标没有小数）`, 'action.invalid', 400)
    }
    coords[name] = value
  }
  if (Math.abs(coords.x) > 3.0e7 || Math.abs(coords.z) > 3.0e7 || coords.y < -512 || coords.y > 2048) {
    throw new ActionError('坐标超出 Minecraft 世界边界', 'action.invalid', 400)
  }
  return coords
}

function validateWorldCoords(params) {
  const coords = {}
  for (const name of ['x', 'y', 'z']) {
    const value = params[name]
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      throw new ActionError(`坐标 ${name} 必须是有限数字`, 'action.invalid', 400)
    }
    coords[name] = value
  }
  if (Math.abs(coords.x) > 3.0e7 || Math.abs(coords.z) > 3.0e7 || coords.y < -512 || coords.y > 2048) {
    throw new ActionError('坐标超出 Minecraft 世界边界', 'action.invalid', 400)
  }
  return coords
}

/** Phase 3C §三 / 3D §二：非破坏性 Movement 配置（禁止挖/放，目标不可达 → NO_PATH）。
 *
 * ``allow1by1towers=false`` 是 Phase 3D 的显式硬化：该能力需要放方块才能爬 1×1 高塔，
 * 不能依赖「scafoldingBlocks 为空所以碰巧不能搭塔」——现在所有导航动作都明确不允许修改世界。 */
function configureMovements(movements) {
  movements.canDig = false // 绝不为了到达目标挖方块
  movements.scafoldingBlocks = [] // 绝不搭桥/搭塔（默认值含泥土/圆石，会主动放方块）
  movements.allow1by1towers = false // 显式：不放方块搭 1×1 塔（普通 1 格跳跃不受影响）
  movements.canOpenDoors = false // 保守默认：不开门
  return movements
}

/** 当前 Pathfinder 诊断（goal 类型/目标/跟随距离/是否在移动）——只读，供 status 与 WebUI。
 *
 * Phase 3D §十二：跟随期间必须能看出「在跟谁、距离多少」——GoalFollow 走
 * username 分支；GoalNear（move_to）走坐标分支。 */
function pathfinderStatus() {
  const bot = state.bot
  const pf = bot && bot.pathfinder
  if (!pf) return { goal: null, target: null, distance: null, moving: false }
  const goal = pf.goal || null
  let target = null
  let distance = null
  if (goal) {
    const entity = goal.entity || null
    if (entity && entity.position) {
      // GoalFollow：目标是活 entity（玩家移动时 pathfinder 自动重规划）
      target = {
        username: entity.username ?? entity.name ?? null,
        x: round2(entity.position.x),
        y: round2(entity.position.y),
        z: round2(entity.position.z),
      }
    } else if (Number.isFinite(goal.x) && Number.isFinite(goal.y) && Number.isFinite(goal.z)) {
      target = { x: round2(goal.x), y: round2(goal.y), z: round2(goal.z) }
    }
    if (Number.isFinite(goal.rangeSq)) distance = round2(Math.sqrt(goal.rangeSq))
  }
  return {
    goal: goal ? goal.constructor.name : null,
    target,
    distance,
    moving: Boolean(typeof pf.isMoving === 'function' && pf.isMoving()),
  }
}

const ACTION_REGISTRY = {
    look_at: {
      exclusive: true,
      timeout_ms: 5000,
      risk: 'SAFE',
      validate(params) {
        return validateWorldCoords(params)
      },
      async run(bot, params) {
        // 上层永远不处理 yaw/pitch 数学：直接交给 mineflayer 的 lookAt。
        await bot.lookAt(new Vec3(params.x, params.y, params.z), true)
        // 状态回报：完成瞬间的实际朝向（服务器可能在之后回写，这里如实记录动作结果）
        return {
          yaw: Math.round(((bot.entity.yaw * 180) / Math.PI) * 10) / 10,
          pitch: Math.round(((bot.entity.pitch * 180) / Math.PI) * 10) / 10,
        }
      },
      cleanup(bot) {
        // 超时兜底：look_at 不会移动角色，但清掉控制位是最便宜的坏状态保险。
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    chat: {
      exclusive: false, // 唯一允许并存的动作（§八）
      timeout_ms: 5000,
      risk: 'SAFE',
      offline_code: 'chat.not_online', // 保持 Phase 1 HTTP 兼容
      validate(params) {
        const message = String(params.message ?? '')
        if (!message.trim()) throw new ActionError('message 不能为空', 'chat.empty', 400)
        if (message.length > CHAT_MAX_CHARS) {
          throw new ActionError(`message 超过 ${CHAT_MAX_CHARS} 字符上限`, 'chat.too_long', 400)
        }
        return { message }
      },
      async run(bot, params) {
        bot.chat(params.message)
      },
    },
    move_to: {
      // Phase 3C：非破坏性导航（LOW；不挖不放，目标不可达 → NO_PATH）
      // Phase 3E：改为持续型（detached）——导航要几十秒，绝不能阻塞调用方；
      // 启动即 RUNNING，终点/失败经 action 事件送达（与 follow_player 同一套语义）。
      exclusive: true,
      timeout_ms: MOVE_TIMEOUT_MS,
      risk: 'LOW',
      detached: true,
      validate(params) {
        const coords = validateWorldCoords(params)
        // 最大移动距离（相对当前玩家位置；第一版 64 格，可配置）——不允许多千格长距离
        const bot = state.bot
        const position = bot && bot.entity ? bot.entity.position : null
        if (position) {
          const dx = coords.x - position.x
          const dy = coords.y - position.y
          const dz = coords.z - position.z
          const distance = Math.sqrt(dx * dx + dy * dy + dz * dz)
          if (distance > MOVE_MAX_DISTANCE) {
            throw new ActionError(
              `目标距当前位置 ${distance.toFixed(1)} 格，超过 move_to 上限 ${MOVE_MAX_DISTANCE} 格`,
              'action.invalid',
              400,
            )
          }
        }
        return coords
      },
      async start(bot, params) {
        // Phase 4H.1：启动阶段仍然无副作用 —— 只把目标与 Goal 对象建好；真正的 setGoal
        // 由 wait 完成（§七：**先挂监听再 setGoal**，否则第一个 goal_reached / path_update
        // 会丢事件，导航就再也不会落终态）。
        return {
          target: new Vec3(params.x, params.y, params.z),
          goal: new goals.GoalNear(params.x, params.y, params.z, MOVE_TO_RADIUS),
        }
      },
      wait(bot, params, token, state, controller) {
        // Phase 4H.1：**不再用 ``bot.pathfinder.goto()``**。mineflayer-pathfinder 2.4.5 的
        // goto 在"空路径"上会静默 resolve（``if (results.path.length === 0) cleanup()`` 排在
        // noPath 判断**之前**），于是"根本没找到路 / 根本没走"会被报成成功 —— 真机实测
        // completed 时罐头还在 28 格外。现在由 CatooBot 自己挂 Pathfinder 生命周期监听，
        // 并且**自己用重新读取的实际位置**决定什么时候才算真的到达（§二/§五/§八/§十五）。
        return new Promise((resolvePromise, rejectPromise) => {
          let settled = false
          const listeners = []
          const on = (event, handler) => {
            if (typeof bot.on !== 'function') return
            bot.on(event, handler)
            listeners.push([event, handler])
          }
          // 让 cleanup（stop / 超时 / 断开）也能摘掉这些监听器：复用 ActionRuntime 的
          // controller 与它已有的 cleaned 标记，不另建第二套 cleanup 状态（§二十四）。
          if (controller) controller.moveListeners = listeners

          /** 失败 detail（§二十）：目标 / 实际 / 距离 / 半径都如实带出去。 */
          const detail = (reason) => {
            const view = moveArrival(bot, state)
            return {
              target: { x: params.x, y: params.y, z: params.z },
              actual: view.position
                ? {
                    x: round2(view.position.x),
                    y: round2(view.position.y),
                    z: round2(view.position.z),
                  }
                : null,
              distance_to_target: view.distance === null ? null : round2(view.distance),
              raw_distance_to_target: view.rawDistance === null ? null : round2(view.rawDistance),
              radius: MOVE_TO_RADIUS,
              goal_reached: view.goalSatisfied,
              reason,
            }
          }
          const finish = (error, value) => {
            if (settled) return
            settled = true
            detachPathListeners(bot, listeners)
            // SUCCEEDED / FAILED 都不会触发 ActionRuntime 的 cleanup（既定契约），
            // 所以到达与失败都必须自己收掉导航意图（Phase 4H 的教训）。
            releaseMoveGoal(bot)
            if (error) rejectPromise(error)
            else resolvePromise(value)
          }
          const cancelled = () => Boolean(token && token.cancelled)
          const dead = () => settled || cancelled()
          const succeed = () => {
            // §十九：距离必须来自**到达判定瞬间重新读取**的实际位置（不是 Pathfinder 的预测）
            const view = moveArrival(bot, state)
            const position = view.position
            finish(null, {
              target: { x: params.x, y: params.y, z: params.z },
              final_position: position
                ? { x: round2(position.x), y: round2(position.y), z: round2(position.z) }
                : null,
              distance_to_target: view.distance === null ? null : round2(view.distance),
              raw_distance_to_target: view.rawDistance === null ? null : round2(view.rawDistance),
            })
          }
          const notReached = (reason, message) => {
            const info = detail(reason)
            finish(
              new ActionError(
                `${message}（距目标 ${info.distance_to_target} 格，半径 ${MOVE_TO_RADIUS}）`,
                'path.not_reached',
                500,
                info,
              ),
            )
          }

          // §八：**即使收到 goal_reached 也要二次验证** —— 重新读位置，不采信 Pathfinder 的说法
          on('goal_reached', () => {
            if (dead()) return
            if (moveArrival(bot, state).reached) succeed()
            else {
              notReached(
                'goal_reached_without_arrival',
                'Pathfinder 报告到达了，但罐头实际位置不在到达半径内',
              )
            }
          })
          on('path_update', (results) => {
            if (dead()) return
            const status = String((results && results.status) || '')
            const path = (results && results.path) || []
            if (status === 'noPath') {
              // §十一：空路径 + noPath 也必须如实失败（2.4.5 的 goto 正是在这里静默成功的）
              finish(
                new ActionError(
                  '无法找到到达目标的非破坏性路径',
                  'path.not_found',
                  500,
                  detail('no_path'),
                ),
              )
              return
            }
            if (status === 'timeout') {
              // §十三：路径搜索超时 → FAILED（清 Goal），绝不 SUCCEEDED
              finish(
                new ActionError(
                  '路径计算超时（非破坏性）',
                  'path.not_found',
                  500,
                  detail('path_search_timeout'),
                ),
              )
              return
            }
            // §十二：partial 只是"先走近一点"，继续等后续事件（整体 30s 超时兜底）
            if (status === 'partial') return
            if (path.length === 0) {
              // §十：path=[] + success 正是 2.4.5 假成功的形状 —— 只有真的在半径内才算到达
              if (moveArrival(bot, state).reached) succeed()
              else {
                notReached(
                  'empty_path_without_arrival',
                  'Pathfinder 结束了但没有派生出任何路径，且罐头不在到达半径内',
                )
              }
            }
          })
          on('path_stop', () => {
            // §十五：token 已排除（不是我们取消的）—— 说明底层导航自己停了 → 验证位置
            if (dead()) return
            if (moveArrival(bot, state).reached) succeed()
            else notReached('path_stopped_short', '导航在到达目标之前停住了')
          })
          on('goal_updated', (newGoal) => {
            if (dead()) return
            if (newGoal !== state.goal) {
              // §十四：Goal 被换成别的（防御分支；独占路径下正常不会发生）→ 立刻失败
              finish(
                new ActionError(
                  '导航目标被换成了别的目标',
                  'goal.changed',
                  409,
                  detail('goal_changed'),
                ),
              )
            }
          })

          // §七：监听器全部就位**之后**才 setGoal
          bot.pathfinder.setGoal(state.goal)
        })
      },
      cleanup(bot, controller) {
        // Phase 3C §十一/§十二：先硬清 Goal（真正停止导航），再清控制位；
        // 顺序保证「Minecraft 已停止执行导航后才对外报 CANCELLED」。
        // Phase 4H.1：同时摘掉生命周期监听器（挂在 controller.moveListeners 上），
        // 复用 ActionRuntime 的 cleaned 标记，cleanup 仍然恰好一次。
        detachPathListeners(bot, controller ? controller.moveListeners : null)
        releaseMoveGoal(bot)
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    dig: {
      // Phase 4B：破坏**一个明确指定的方块**（MEDIUM；真实修改世界 → exclusive + STOP + timeout）
      // 只做一件事：把用户确认过的那个方块挖掉。不找矿、不换目标、不导航、不换工具、不捡掉落。
      exclusive: true,
      timeout_ms: DIG_DEFAULT_TIMEOUT_MS,
      risk: 'MEDIUM',
      detached: true, // 挖掘可能持续数秒~数十秒：启动即 RUNNING，终态经事件送达
      validate(params) {
        const coords = validateWorldCoords(params)
        const expected = params.expected_block
        if (typeof expected !== 'string' || !expected.trim()) {
          throw new ActionError('expected_block 不能为空', 'block.invalid', 400)
        }
        if (expected.length > 64) {
          throw new ActionError('expected_block 过长', 'block.invalid', 400)
        }
        // Phase 4I（§四/§五）：**可选** expected_tool —— 给了就是"执行瞬间主手必须拿着它"的
        // 身份硬约束（不是"帮我去找一把石镐"）。不给 = Phase 4B 原行为，一个字节都不变。
        const rawTool = params.expected_tool
        if (rawTool === undefined || rawTool === null || rawTool === '') {
          return { ...coords, expected_block: expected.trim(), expected_tool: null }
        }
        if (typeof rawTool !== 'string') {
          throw new ActionError('expected_tool 必须是字符串', 'item.invalid', 400)
        }
        if (!rawTool.trim()) {
          throw new ActionError('expected_tool 不能只有空白', 'item.invalid', 400)
        }
        if (rawTool.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('expected_tool 过长', 'item.invalid', 400)
        }
        return {
          ...coords,
          expected_block: expected.trim(),
          expected_tool: rawTool.trim(),
        }
      },
      async start(bot, params) {
        // §十三-§十七：真正的执行前校验（同步反馈）——确认是授权，不代替校验。
        // 在线门由 ActionRuntime.execute 统一把守（未在线根本到不了这里）。
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const position = new Vec3(params.x, params.y, params.z)
        const block = bot.blockAt(position)
        if (!block || isAir(block.name)) {
          throw new ActionError(
            `目标位置没有方块（${params.x},${params.y},${params.z}）`,
            'block.not_found',
            404,
          )
        }
        if (block.name !== params.expected_block) {
          // §十四：用户确认的是「这个位置的这个方块」，世界变了就必须拒绝
          throw new ActionError(
            `方块已经变了：期望 ${params.expected_block}，实际 ${block.name}`,
            'block.changed',
            409,
            { expected: params.expected_block, actual: block.name },
          )
        }
        // Phase 4I（§十/§十一/§十二/§二十七）：给了 expected_tool 就要求**此刻**主手确实拿着它。
        // 每次都在 start 里**重新读** bot.heldItem（绝不缓存上一轮 inventory 的结论），
        // 而且**绝不自动换工具 / 切 hotbar / 从背包里找一把** —— 身份不对就如实失败。
        let toolActual = null
        const toolExpected =
          params.expected_tool === undefined || params.expected_tool === null
            ? null
            : normalizeItemName(params.expected_tool)
        if (toolExpected !== null) {
          const heldTool = bot.heldItem
          if (!heldTool || !heldTool.name) {
            throw new ActionError(
              '主手没有拿任何物品（不会自动换工具）',
              'held.item_missing',
              400,
              { expected: toolExpected, actual: null },
            )
          }
          toolActual = normalizeItemName(heldTool.name)
          if (toolActual !== toolExpected) {
            throw new ActionError(
              `主手拿的是 ${toolActual}，不是你要求的 ${toolExpected}（不会自动换工具）`,
              'held.item_changed',
              409,
              { expected: toolExpected, actual: toolActual },
            )
          }
          if (!(heldTool.count > 0)) {
            throw new ActionError('主手物品数量为 0', 'held.item_missing', 400, {
              expected: toolExpected,
              actual: toolActual,
            })
          }
        }

        const center = position.offset(0.5, 0.5, 0.5)
        const eyes = bot.entity.position.offset(0, 1.65, 0)
        const distance = eyes.distanceTo(center)
        const maxDistance = digMaxDistance()
        if (distance > maxDistance) {
          throw new ActionError(
            `目标方块距离 ${distance.toFixed(1)} 格，超过上限 ${maxDistance} 格（本阶段不会自己走过去）`,
            'block.too_far',
            400,
          )
        }
        if (typeof bot.canDigBlock === 'function' && !bot.canDigBlock(block)) {
          // 挖不动（工具不对/被保护）：不换工具、不找角度、不走近——如实失败
          throw new ActionError('当前状态下挖不动这个方块', 'block.not_diggable', 400)
        }
        return {
          block,
          position,
          blockName: block.name,
          // Phase 4I：执行前那一刻的工具身份（expected 是用户的要求，actual 是实际主手）
          toolExpected,
          toolActual,
        }
      },
      async wait(bot, params, token, state) {
        try {
          // forceLook=true：由 Mineflayer 负责朝向（Python 层绝不碰 yaw/pitch）
          await bot.dig(state.block, true)
        } catch (error) {
          if (token && token.cancelled) throw new ActionCancelled(token.reason)
          const message = String(error && error.message ? error.message : error)
          if (/digging aborted|Digging aborted/i.test(message)) {
            // §三十一：被中断（stop/disconnect/超时）由上面的 cancelled 分支处理；
            // 这里是"没人叫停但挖掘被服务器打断" → 稳定失败码
            throw new ActionError(
              '挖掘被中断（方块可能已经消失或服务器拒绝）',
              'block.dig_aborted',
              500,
            )
          }
          throw new ActionError(`挖掘失败：${message}`, 'action.failed', 500)
        }
        // §二十二/§二十三：不信 Promise —— 重新读一次方块，确认真的没了
        const after = bot.blockAt(state.position)
        const afterName = after ? after.name : 'air'
        if (after && after.name === state.blockName) {
          throw new ActionError('方块仍在原位，未能确认破坏结果', 'block.break_unconfirmed', 500)
        }
        // Phase 4I §十九：动作结束后再读一次主手（**不做成功硬门** —— 硬门永远是
        // block_after != block_before；工具数量变化只是事实记录，例如镐子挖坏了）
        const heldAfter = bot.heldItem
        return {
          position: { x: params.x, y: params.y, z: params.z },
          block_before: state.blockName,
          block_after: afterName,
          tool_expected: state.toolExpected === undefined ? null : state.toolExpected,
          tool_actual: state.toolActual === undefined ? null : state.toolActual,
          tool_actual_after:
            heldAfter && heldAfter.name
              ? { name: normalizeItemName(heldAfter.name), count: heldAfter.count }
              : null,
        }
      },
      cleanup(bot) {
        // §二十：取消/超时/断开/退出都必须真的停止挖掘（至多一次，由 ActionRuntime 保证）
        if (bot && typeof bot.stopDigging === 'function') {
          try {
            bot.stopDigging()
          } catch (error) {
            log('warn', 'dig cleanup stopDigging failed', { error: error.message })
          }
        }
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    place: {
      // Phase 4C：放置**一个**明确指定的方块（MEDIUM；真实修改世界 → exclusive + STOP + timeout）
      // 对称于 dig：手里有什么就只能放什么，目标/方向/手持物品都必须明确（§二）。
      // 不导航、不找放置面、不换 hotbar、不 equip、不补货、不连续放。
      exclusive: true,
      timeout_ms: PLACE_DEFAULT_TIMEOUT_MS,
      risk: 'MEDIUM',
      detached: true, // 与 dig 同款：启动即 RUNNING，终态经事件送达
      validate(params) {
        // §十三：方块坐标必须是**整数**（100.5 这种直接拒绝）
        const coords = {}
        for (const name of ['x', 'y', 'z']) {
          const value = params[name]
          if (typeof value !== 'number' || !Number.isFinite(value) || !Number.isInteger(value)) {
            throw new ActionError(`坐标 ${name} 必须是整数`, 'action.invalid', 400)
          }
          coords[name] = value
        }
        if (Math.abs(coords.x) > 3.0e7 || Math.abs(coords.z) > 3.0e7 || coords.y < -512 || coords.y > 2048) {
          throw new ActionError('坐标超出 Minecraft 世界边界', 'action.invalid', 400)
        }
        // §八：face 只允许六个值
        const face = String(params.face ?? '').trim().toLowerCase()
        if (!Object.prototype.hasOwnProperty.call(PLACE_FACES, face)) {
          throw new ActionError(
            `face 必须是 ${Object.keys(PLACE_FACES).join('/')} 之一`,
            'face.invalid',
            400,
          )
        }
        // §九：expected_item 是"当前主手必须拿着这个物品"的硬约束
        const expected = params.expected_item
        if (typeof expected !== 'string' || !expected.trim()) {
          throw new ActionError('expected_item 不能为空', 'item.invalid', 400)
        }
        if (expected.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('expected_item 过长', 'item.invalid', 400)
        }
        return { ...coords, face, expected_item: expected.trim() }
      },
      async start(bot, params) {
        // §十六：解析 target / face / reference → 实时读取 heldItem / referenceBlock / targetBlock → 校验
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const faceVector = PLACE_FACES[params.face]
        const target = new Vec3(params.x, params.y, params.z)
        const reference = target.minus(faceVector)

        // §九：主手必须真的拿着 expected_item（不 equip、不切 hotbar）
        const held = bot.heldItem
        if (!held || !held.name) {
          throw new ActionError('主手没有拿任何物品（不会自动装备）', 'held.item_missing', 400)
        }
        const heldName = normalizeItemName(held.name)
        if (heldName !== normalizeItemName(params.expected_item)) {
          throw new ActionError(
            `主手拿的是 ${held.name}，不是你确认的 ${params.expected_item}`,
            'held.item_changed',
            409,
            { expected: normalizeItemName(params.expected_item), actual: heldName },
          )
        }
        if (!(held.count > 0)) {
          throw new ActionError('主手物品数量为 0', 'held.item_missing', 400)
        }

        const targetBlock = bot.blockAt(target)
        const referenceBlock = bot.blockAt(reference)
        if (targetBlock === null || referenceBlock === null) {
          throw new ActionError(
            `目标区域没有加载（${params.x},${params.y},${params.z}）`,
            'block.unavailable',
            404,
          )
        }
        // §十：第一版只允许放到空气格（不碰 replaceable 语义）
        if (!isAir(targetBlock.name)) {
          throw new ActionError(
            `目标位置已经有方块（${targetBlock.name}），本阶段只往空气里放`,
            'target.occupied',
            409,
            { actual: targetBlock.name },
          )
        }
        if (isAir(referenceBlock.name)) {
          throw new ActionError('参考方块位置是空气，没有可依附的面', 'reference.missing', 404)
        }
        // §十二：距离（眼睛 → 目标方块中心，与 dig 同口径）
        const center = target.offset(0.5, 0.5, 0.5)
        const eyes = bot.entity.position.offset(0, 1.65, 0)
        const distance = eyes.distanceTo(center)
        const maxDistance = placeMaxDistance()
        if (distance > maxDistance) {
          throw new ActionError(
            `目标位置距离 ${distance.toFixed(1)} 格，超过上限 ${maxDistance} 格（本阶段不会自己走过去）`,
            'block.too_far',
            400,
          )
        }
        // §十六 Step 5：本次 action 的不可变快照
        return {
          target,
          reference,
          referenceBlock,
          faceVector,
          face: params.face,
          expected_item: normalizeItemName(params.expected_item),
          item_before: { name: heldName, count: held.count || 0 },
          block_before: targetBlock.name,
          reference_before: referenceBlock.name,
        }
      },
      async wait(bot, params, token, state) {
        try {
          // §十七：交给 Mineflayer（reference block + face vector），不自造右键/packet
          await bot.placeBlock(state.referenceBlock, state.faceVector)
        } catch (error) {
          if (token && token.cancelled) throw new ActionCancelled(token.reason)
          const message = String(error && error.message ? error.message : error)
          throw new ActionError(`放置失败：${message}`, 'action.failed', 500)
        }
        // §十八/§十九：不把 Promise resolve 当世界真相 —— 重新读一次目标
        const after = bot.blockAt(state.target)
        if (after === null) {
          throw new ActionError('放置后读不到目标位置', 'block.place_unconfirmed', 500)
        }
        if (isAir(after.name)) {
          throw new ActionError('方块没有被放上去（服务器未确认）', 'block.place_unconfirmed', 500)
        }
        if (normalizeItemName(after.name) !== state.expected_item) {
          throw new ActionError(
            `放上去的是 ${after.name}，不是确认的 ${state.expected_item}`,
            'block.place_unconfirmed',
            500,
            { expected: state.expected_item, actual: normalizeItemName(after.name) },
          )
        }
        // §二十：物品数量只如实上报，不硬编码 -1（creative/modded 都不同）
        const heldAfter = bot.heldItem
        const heldAfterName = heldAfter && heldAfter.name ? normalizeItemName(heldAfter.name) : ''
        return {
          position: { x: params.x, y: params.y, z: params.z },
          block_before: state.block_before,
          block_after: normalizeItemName(after.name),
          reference_block: state.reference_before,
          face: state.face,
          item_before: state.item_before,
          item_after_count:
            heldAfterName === state.item_before.name ? (heldAfter.count || 0) : 0,
        }
      },
      cleanup(bot) {
        // §二十一：place 是短动作，没有 stopPlacing —— 清控制位即可（至多一次由 ActionRuntime 保证）
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    equip: {
      // Phase 4D：把背包里**明确指定**的物品拿到主手（MEDIUM；改手持状态 → exclusive + 确认）
      // 只支持 destination=hand；不碰盔甲/副手；不自动换槽、不自动挑"更方便"的 stack。
      exclusive: true,
      timeout_ms: EQUIP_DEFAULT_TIMEOUT_MS,
      risk: 'MEDIUM',
      detached: true,
      validate(params) {
        const item = params.item
        if (typeof item !== 'string' || !item.trim()) {
          throw new ActionError('item 不能为空', 'item.invalid', 400)
        }
        if (item.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('item 过长', 'item.invalid', 400)
        }
        return { item: normalizeItemName(item) }
      },
      async start(bot, params) {
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        // §七：执行前重新读取（不看缓存）；已经拿着就直接给成功语义（但要实时确认）
        const held = bot.heldItem
        if (held && held.name && normalizeItemName(held.name) === params.item && (held.count || 0) > 0) {
          return {
            already_equipped: true,
            item: params.item,
            held_item: { name: normalizeItemName(held.name), count: held.count || 0 },
            source_slot: null,
          }
        }
        const found = findInventoryItem(bot, params.item)
        if (!found) {
          throw new ActionError(`背包里没有 ${params.item}`, 'item.not_found', 404)
        }
        return {
          already_equipped: false,
          item: params.item,
          item_type: found.item.type,
          source_slot: found.slot,
          item_before: { name: normalizeItemName(found.item.name), count: found.item.count || 0 },
        }
      },
      async wait(bot, params, token, state) {
        try {
          if (state.already_equipped) return { ...state, destination: 'hand' }
          // §五/§六：Mineflayer 原生 equip（Item 对象 + destination="hand"）
          await bot.equip(state.item_type, 'hand')
        } catch (error) {
          if (token && token.cancelled) throw new ActionCancelled(token.reason)
          const message = String(error && error.message ? error.message : error)
          throw new ActionError(`装备失败：${message}`, 'action.failed', 500)
        }
        // §八：equip resolve 不算事实 —— 重新读 heldItem
        const held = bot.heldItem
        const actual = held && held.name ? normalizeItemName(held.name) : ''
        if (!held || actual !== state.item || !((held.count || 0) > 0)) {
          throw new ActionError(
            `装备后主手不是 ${state.item}`,
            'equip.unconfirmed',
            500,
            { expected: state.item, actual: actual || 'empty' },
          )
        }
        return {
          item: state.item,
          destination: 'hand',
          source_slot: state.source_slot,
          held_item: { name: actual, count: held.count || 0 },
          already_equipped: false,
        }
      },
      cleanup(bot) {
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    inventory_move: {
      // Phase 4D：把一个**明确槽位**上的指定物品移动指定数量到另一个**明确槽位**（MEDIUM）
      // 一个物品、一个 source、一个 destination、一个 count；绝不隐式交换、绝不批量整理。
      exclusive: true,
      timeout_ms: MOVE_DEFAULT_TIMEOUT_MS,
      risk: 'MEDIUM',
      detached: true,
      validate(params) {
        const source = requireInventorySlot(params.source_slot, 'source_slot', state.bot)
        const destination = requireInventorySlot(params.destination_slot, 'destination_slot', state.bot)
        if (source === destination) {
          throw new ActionError('source_slot 与 destination_slot 不能相同', 'slot.invalid', 400)
        }
        const item = params.item
        if (typeof item !== 'string' || !item.trim()) {
          throw new ActionError('item 不能为空', 'item.invalid', 400)
        }
        const count = params.count
        if (typeof count !== 'number' || !Number.isFinite(count) || !Number.isInteger(count) || count < 1) {
          throw new ActionError('count 必须是 >= 1 的整数', 'item.invalid', 400)
        }
        return { source_slot: source, destination_slot: destination, item: normalizeItemName(item), count }
      },
      async start(bot, params) {
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        // §十三：执行前读真实 inventory，逐项校验 source / item / count / destination
        const slots = bot.inventory?.slots || []
        const sourceItem = slots[params.source_slot]
        if (!sourceItem || !sourceItem.name) {
          throw new ActionError(`source_slot ${params.source_slot} 是空的`, 'item.not_found', 404)
        }
        const sourceName = normalizeItemName(sourceItem.name)
        if (sourceName !== params.item) {
          throw new ActionError(
            `source_slot ${params.source_slot} 上是 ${sourceName}，不是 ${params.item}`,
            'item.changed',
            409,
            { expected: params.item, actual: sourceName },
          )
        }
        if ((sourceItem.count || 0) < params.count) {
          throw new ActionError(
            `source_slot ${params.source_slot} 只有 ${sourceItem.count || 0} 个，不够 ${params.count} 个`,
            'item.count_insufficient',
            409,
            { available: sourceItem.count || 0, requested: params.count },
          )
        }
        const destItem = slots[params.destination_slot]
        if (destItem && destItem.name) {
          // §十四：目标非空且不是"同名可堆叠" → 拒绝（绝不隐式交换）
          const destName = normalizeItemName(destItem.name)
          const stackable =
            destName === sourceName && (destItem.count || 0) < (destItem.stackSize || 64)
          if (!stackable) {
            throw new ActionError(
              `destination_slot ${params.destination_slot} 已经被 ${destName} 占用`,
              'destination.occupied',
              409,
              { actual: destName },
            )
          }
        }
        return {
          source_slot: params.source_slot,
          destination_slot: params.destination_slot,
          item: params.item,
          requested_count: params.count,
          item_type: sourceItem.type,
          source_before: { name: sourceName, count: sourceItem.count || 0 },
          destination_before:
            destItem && destItem.name
              ? { name: normalizeItemName(destItem.name), count: destItem.count || 0 }
              : null,
        }
      },
      async wait(bot, params, token, state) {
        try {
          // §十五：Mineflayer 原生 transfer —— 把 source/destination 都钉死在单个槽位上
          await bot.transfer({
            window: bot.inventory,
            itemType: state.item_type,
            count: state.requested_count,
            sourceStart: state.source_slot,
            sourceEnd: state.source_slot + 1,
            destStart: state.destination_slot,
            destEnd: state.destination_slot + 1,
          })
        } catch (error) {
          if (token && token.cancelled) throw new ActionCancelled(token.reason)
          const message = String(error && error.message ? error.message : error)
          if (/destination full/i.test(message)) {
            throw new ActionError('目标槽位放不下', 'destination.occupied', 409)
          }
          throw new ActionError(`移动物品失败：${message}`, 'action.failed', 500)
        }
        // §十六：重新读 source / destination，按**真实状态**判定，不硬编码 +count
        const slots = bot.inventory?.slots || []
        const sourceAfter = slots[state.source_slot]
        const destAfter = slots[state.destination_slot]
        const sourceName = sourceAfter && sourceAfter.name ? normalizeItemName(sourceAfter.name) : ''
        const destName = destAfter && destAfter.name ? normalizeItemName(destAfter.name) : ''
        const movedOut = state.source_before.count - (sourceName === state.item ? sourceAfter.count || 0 : 0)
        const destGained =
          (destName === state.item ? destAfter.count || 0 : 0) -
          (state.destination_before && state.destination_before.name === state.item
            ? state.destination_before.count
            : 0)
        if (movedOut < state.requested_count || destGained <= 0) {
          throw new ActionError(
            `移动后状态不对（source 减少 ${movedOut}，destination 增加 ${destGained}）`,
            'move.unconfirmed',
            500,
            {
              source_after: sourceName ? { name: sourceName, count: sourceAfter.count || 0 } : null,
              destination_after: destName ? { name: destName, count: destAfter.count || 0 } : null,
            },
          )
        }
        return {
          item: state.item,
          source_slot: state.source_slot,
          destination_slot: state.destination_slot,
          requested_count: state.requested_count,
          source_after: sourceName ? { name: sourceName, count: sourceAfter.count || 0 } : null,
          destination_after: destName ? { name: destName, count: destAfter.count || 0 } : null,
        }
      },
      cleanup(bot) {
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    container_inspect: {
      // Phase 4E：读一个 Chest / Barrel 的**真实内容**（SAFE 只读，但 **exclusive**：
      // 打开真实窗口是有生命周期的客户端状态，不能和其他前台动作并发）。
      // 内部固定 open → read → close；无论成功失败都 close（cleanup 再兜一层）。
      exclusive: true,
      timeout_ms: containerTimeoutMs(),
      risk: 'SAFE',
      validate(params) {
        return validateBlockCoords(params)
      },
      async run(bot, params, token, controller) {
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const block = requireContainerBlock(bot, params)
        requireContainerDistance(bot, block)
        const window = await openContainerWindow(bot, block, token, controller)
        try {
          const size = requireSingleContainerWindow(window)
          const snapshot = {
            ok: true,
            container: {
              type: blockNameOf(block),
              label: containerTypeLabel(block.name),
              position: { x: params.x, y: params.y, z: params.z },
              size,
            },
            slots: readContainerSlots(window, size),
          }
          // §十三/§三十一：先关窗再报成功；关不上要如实说，绝不把"看过了"当成一切正常
          const closed = closeAndForget(bot, controller, window)
          if (!closed.ok) {
            throw new ActionError(`关闭容器失败：${closed.error}`, 'container.close_failed', 500, {
              snapshot,
            })
          }
          return snapshot
        } catch (error) {
          closeTrackedWindow(bot, controller)
          throw error
        }
      },
      cleanup(bot, controller) {
        closeTrackedWindow(bot, controller)
      },
    },
    container_transfer: {
      // Phase 4E：一个物品在「容器槽 ↔ 自己背包槽」之间移动**一次**（MEDIUM；exclusive + 确认）。
      // 一个方向、一个 container_slot、一个 inventory_slot、一个 item、一个 count；
      // 目标被占用就拒绝（绝不交换 / 绝不换槽）；报成功之前必须关窗，结果以真实重读为准。
      exclusive: true,
      timeout_ms: containerTimeoutMs(),
      risk: 'MEDIUM',
      detached: true,
      validate(params) {
        const coords = validateBlockCoords(params)
        const direction = String(params.direction ?? '')
          .trim()
          .toLowerCase()
        if (!CONTAINER_DIRECTIONS.includes(direction)) {
          throw new ActionError(
            `direction 必须是 ${CONTAINER_DIRECTIONS.join(' / ')}（拿出去 withdraw / 放进去 deposit）`,
            'action.invalid',
            400,
          )
        }
        const containerSlot = params.container_slot
        if (
          typeof containerSlot !== 'number' ||
          !Number.isInteger(containerSlot) ||
          containerSlot < 0
        ) {
          throw new ActionError('container_slot 必须是 >= 0 的整数', 'slot.invalid', 400)
        }
        const inventorySlot = requireInventorySlot(params.inventory_slot, 'inventory_slot', state.bot)
        const item = params.item
        if (typeof item !== 'string' || !item.trim()) {
          throw new ActionError('item 不能为空', 'item.invalid', 400)
        }
        if (item.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('item 过长', 'item.invalid', 400)
        }
        const count = params.count
        if (typeof count !== 'number' || !Number.isInteger(count) || count < 1) {
          throw new ActionError('count 必须是 >= 1 的整数', 'item.invalid', 400)
        }
        return {
          ...coords,
          direction,
          container_slot: containerSlot,
          inventory_slot: inventorySlot,
          item: normalizeItemName(item),
          count,
        }
      },
      async start(bot, params, token, controller) {
        // §十六/§三十四：确认只是授权 —— 真正执行前 open → reread → validate 必须完整跑一遍。
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const block = requireContainerBlock(bot, params)
        requireContainerDistance(bot, block)
        const window = await openContainerWindow(bot, block, token, controller)
        try {
          const size = requireSingleContainerWindow(window)
          if (params.container_slot >= size) {
            throw new ActionError(`container_slot 超出容器范围（0~${size - 1}）`, 'slot.invalid', 400, {
              container_slots: size,
            })
          }
          const inventoryWindowSlot = windowSlotForInventorySlot(window, bot, params.inventory_slot)
          const containerBefore = readWindowSlot(window, params.container_slot)
          const inventoryBefore = readWindowSlot(window, inventoryWindowSlot)
          const sourceBefore = params.direction === 'withdraw' ? containerBefore : inventoryBefore
          const destinationBefore =
            params.direction === 'withdraw' ? inventoryBefore : containerBefore
          requireContainerTransferSource(sourceBefore, params)
          requireContainerTransferDestination(bot, destinationBefore, params)
          return {
            window,
            item_type: sourceBefore.type,
            container_type: blockNameOf(block),
            container_before: describeItem(containerBefore),
            inventory_before: describeItem(inventoryBefore),
            source_slot:
              params.direction === 'withdraw' ? params.container_slot : inventoryWindowSlot,
            destination_slot:
              params.direction === 'withdraw' ? inventoryWindowSlot : params.container_slot,
            // before 一律存**快照副本**（不是 window.slots 里的活对象）：搬运后要拿它算
            // "真实变化量"，活对象可能被窗口更新就地改写，那样差值会永远算成 0。
            source_before: describeItem(sourceBefore),
            destination_before: describeItem(destinationBefore),
            inventory_window_slot: inventoryWindowSlot,
          }
        } catch (error) {
          // start 抛错不会走 cleanup（FAILED 不强制清理）→ 自己关掉再抛
          closeTrackedWindow(bot, controller)
          throw error
        }
      },
      async wait(bot, params, token, state) {
        const window = state.window
        if (!window || (bot.currentWindow !== undefined && bot.currentWindow !== window)) {
          throw new ActionError('容器窗口已经关闭（可能被服务器或玩家关掉了）', 'container.closed', 409)
        }
        let failure = null
        let result = null
        try {
          // §二十五：Mineflayer 原生 transfer —— source / destination 都**钉死在单槽**
          await bot.transfer({
            window,
            itemType: state.item_type,
            count: params.count,
            sourceStart: state.source_slot,
            sourceEnd: state.source_slot + 1,
            destStart: state.destination_slot,
            destEnd: state.destination_slot + 1,
          })
        } catch (error) {
          if (token && token.cancelled) {
            // 取消/超时：先关窗（cleanup 会再兜一次，幂等），再按取消语义收尾
            closeAndForget(bot, null, window)
            throw new ActionCancelled(token.reason)
          }
          failure = classifyContainerTransferError(error)
        }
        if (!failure) {
          // §二十七-§二十九：不信 transfer 的 resolve —— 重新读 container + inventory 两侧
          const containerAfter = readWindowSlot(window, params.container_slot)
          const inventoryAfter = readWindowSlot(window, state.inventory_window_slot)
          const sourceAfter = params.direction === 'withdraw' ? containerAfter : inventoryAfter
          const destinationAfter = params.direction === 'withdraw' ? inventoryAfter : containerAfter
          const movedOut =
            itemCountOf(state.source_before, params.item) - itemCountOf(sourceAfter, params.item)
          const gainedIn =
            itemCountOf(destinationAfter, params.item) -
            itemCountOf(state.destination_before, params.item)
          result = {
            direction: params.direction,
            position: { x: params.x, y: params.y, z: params.z },
            container_type: state.container_type,
            item: params.item,
            count: params.count,
            container_slot: params.container_slot,
            inventory_slot: params.inventory_slot,
            container_before: state.container_before,
            container_after: describeItem(containerAfter),
            inventory_before: state.inventory_before,
            inventory_after: describeItem(inventoryAfter),
            moved_out: movedOut,
            gained_in: gainedIn,
          }
          if (movedOut < params.count || gainedIn <= 0) {
            failure = new ActionError(
              `搬运后状态不对（移出 ${movedOut}，目标增加 ${gainedIn}）`,
              'container.transfer_unconfirmed',
              500,
              {
                container_after: result.container_after,
                inventory_after: result.inventory_after,
              },
            )
          }
        }
        // §三十一：先关窗，再落终态；关不上要如实报（世界状态已变，绝不吞掉）
        const closed = closeAndForget(bot, null, window)
        if (failure) {
          if (!closed.ok && failure instanceof ActionError) {
            throw new ActionError(failure.message, failure.code, failure.status, {
              ...(failure.detail || {}),
              close_error: closed.error,
            })
          }
          throw failure
        }
        if (!closed.ok) {
          throw new ActionError(`关闭容器失败：${closed.error}`, 'container.close_failed', 500, {
            result,
          })
        }
        return result
      },
      cleanup(bot, controller) {
        closeTrackedWindow(bot, controller)
      },
    },
    recipe_lookup: {
      // Phase 4F：查"当前背包能做的 2×2 配方"（SAFE 只读；**非独占** ——
      // 纯读取，可与导航/背包读并行，但必须在线）。
      exclusive: false,
      timeout_ms: recipeLookupTimeoutMs(),
      risk: 'SAFE',
      validate(params) {
        const item = params.item
        if (typeof item !== 'string' || !item.trim()) {
          throw new ActionError('item 不能为空', 'item.invalid', 400)
        }
        if (item.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('item 过长', 'item.invalid', 400)
        }
        return { item: normalizeItemName(item), crafting_table: validateCraftingTableParam(params.crafting_table) }
      },
      async run(bot, params) {
        if (bot === null || bot === undefined || bot.registry === undefined) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const registry = bot.registry
        // §六/§七：给了工作台就**实时**验证方块（缺失/非工作台/太远都不继续）
        const tableBlock = requireCraftingTableBlock(bot, params.crafting_table)
        const sets = recipeSetsFor(bot, params.item, tableBlock)
        if (!sets || sets.all.length === 0) {
          // 物品不存在 / 这个版本里没有它的配方：如实回答"没有配方"，不猜
          return { ok: true, item: params.item, status: 'recipe_not_found', total: 0, recipes: [] }
        }
        if (sets.twoByTwo.length === 0) {
          // 只有工作台配方（而且这次没给工作台坐标）—— 绝不自动去找工作台
          return {
            ok: true,
            item: params.item,
            status: 'crafting_table_required',
            total: 0,
            recipes: [],
          }
        }
        const entries = sets.twoByTwo
          .map((recipe) => {
            const described = describeRecipe(recipe, registry, false)
            described.available = sets.craftableIds.has(described.recipe_id)
            return described
          })
          .sort(
            (a, b) =>
              Number(b.available) - Number(a.available) ||
              a.recipe_id.localeCompare(b.recipe_id),
          )
        const available = entries.filter((entry) => entry.available).length
        return {
          ok: true,
          item: params.item,
          // §十一：指定了工作台就把坐标带回给模型（没指定则不带这个字段）
          ...(params.crafting_table ? { crafting_table: params.crafting_table } : {}),
          status: available > 0 ? 'available' : 'insufficient_material',
          total: entries.length,
          recipes: entries.slice(0, RECIPE_MAX_ENTRIES),
        }
      },
    },
    craft: {
      // Phase 4F：执行**一次** 2×2 配方（MEDIUM；合成期间 inventory 正在变 →
      // exclusive + 确认）。一次一个 recipe，不做 recipe chain、不自动准备材料。
      exclusive: true,
      timeout_ms: CRAFT_DEFAULT_TIMEOUT_MS,
      risk: 'MEDIUM',
      detached: true,
      validate(params) {
        const recipeId = params.recipe_id
        if (typeof recipeId !== 'string' || !recipeId.trim()) {
          throw new ActionError('recipe_id 不能为空（先用 minecraft_recipe_lookup 拿到它）', 'recipe.invalid', 400)
        }
        if (recipeId.length > CRAFT_MAX_RECIPE_ID_CHARS) {
          throw new ActionError('recipe_id 过长', 'recipe.invalid', 400)
        }
        return {
          recipe_id: recipeId.trim(),
          crafting_table: validateCraftingTableParam(params.crafting_table),
        }
      },
      async start(bot, params) {
        // §十四/§十五/§十七：确认只是授权 —— 执行前必须用**当前**配方表、**当前**工作台
        // 与**当前**背包重新解析（绝不用旧 Recipe 对象）
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const registry = bot.registry
        const tableBlock = requireCraftingTableBlock(bot, params.crafting_table)
        const itemName = itemNameFromRecipeId(params.recipe_id)
        const sets = itemName ? recipeSetsFor(bot, itemName, tableBlock) : null
        if (!sets || sets.all.length === 0) {
          throw new ActionError(`找不到这个配方（${params.recipe_id}）`, 'recipe.not_found', 404)
        }
        const ids = recipeIdsForList(sets.all, registry)
        const index = ids.indexOf(params.recipe_id)
        if (index < 0) {
          throw new ActionError('这个配方已经变了（材料或形状不一致）', 'recipe.changed', 409)
        }
        const recipe = sets.all[index]
        if (recipe.requiresTable && !tableBlock) {
          throw new ActionError(
            '这个配方需要工作台（把工作台坐标给我，或者换一个 2×2 能做的配方）',
            'recipe.unavailable',
            409,
          )
        }
        const craftable =
          typeof bot.recipesFor === 'function'
            ? bot.recipesFor(sets.entry.id, null, 1, tableBlock || null)
            : []
        const craftableIds = new Set(recipeIdsForList(craftable, registry))
        if (!craftableIds.has(params.recipe_id)) {
          // §十六/§十七：绝不自动开箱/移动/挖矿/先做中间材料 —— 直接如实失败
          const result = describeRecipeResult(recipe, registry)
          const ingredients = describeRecipeIngredients(recipe, registry)
          const have = inventoryCountsFor(bot, ingredients.map((entry) => entry.name))
          const missing = ingredients
            .filter((entry) => (have[entry.name] || 0) < entry.count)
            .map((entry) => ({
              name: entry.name,
              need: entry.count,
              have: have[entry.name] || 0,
            }))
          throw new ActionError(
            `材料不够：${missing
              .map((entry) => `${entry.name} 需要 ${entry.need} 个、只有 ${entry.have} 个`)
              .join('；')}（本阶段不会自己去准备材料）`,
            'material.insufficient',
            409,
            { result, ingredients, missing },
          )
        }
        const result = describeRecipeResult(recipe, registry)
        const ingredients = describeRecipeIngredients(recipe, registry)
        return {
          // 内部状态（只给 wait 用，绝不进事件）：全部是语义快照，不存 live Item
          recipe,
          table: tableBlock,
          crafting_table: params.crafting_table || null,
          recipe_id: params.recipe_id,
          item: result.name,
          count_per_craft: result.count_per_craft,
          ingredients,
          before: {
            result_count: countInventoryItem(bot, result.name),
            ingredient_counts: inventoryCountsFor(bot, ingredients.map((entry) => entry.name)),
          },
        }
      },
      async wait(bot, params, token, state) {
        try {
          // §十九：给了工作台就传给 bot.craft（3×3）；没给就是玩家自身 2×2。
          // 一次只执行一次配方（count 固定为 1）。
          await bot.craft(state.recipe, 1, state.table || null)
        } catch (error) {
          if (token && token.cancelled) throw new ActionCancelled(token.reason)
          const message = String(error && error.message ? error.message : error)
          if (/craftingTable/i.test(message)) {
            throw new ActionError(
              '这个配方需要工作台（把工作台坐标给我，或者换一个 2×2 能做的配方）',
              'recipe.unavailable',
              409,
            )
          }
          throw new ActionError(`合成失败：${message}`, 'craft.failed', 500)
        }
        if (token && token.cancelled) {
          // 已经做完了但同时收到 STOP：按 ActionRuntime 的 race 规则只允许一个终态
          throw new ActionCancelled(token.reason)
        }
        // §二十二/§二十三：不信任 bot.craft 的 resolve —— 重新读 inventory 两侧都核验
        const after = {
          result_count: countInventoryItem(bot, state.item),
          ingredient_counts: inventoryCountsFor(
            bot,
            state.ingredients.map((entry) => entry.name),
          ),
        }
        const craftedGain = after.result_count - state.before.result_count
        const ingredients = state.ingredients.map((entry) => {
          const before = state.before.ingredient_counts[entry.name] || 0
          const now = after.ingredient_counts[entry.name] || 0
          return { name: entry.name, expected: entry.count, consumed: before - now }
        })
        const consumedEnough = ingredients.every((entry) => entry.consumed >= entry.expected)
        if (craftedGain < state.count_per_craft || !consumedEnough) {
          throw new ActionError(
            `合成结果和预期不一致（产物 +${craftedGain}，材料消耗 ${ingredients
              .map((entry) => `${entry.name} -${entry.consumed}`)
              .join('、')}）`,
            'craft.unconfirmed',
            500,
            { before: state.before, after, ingredients },
          )
        }
        return {
          recipe_id: state.recipe_id,
          // 用了工作台就如实带回坐标（2×2 时是 null）
          crafting_table: state.crafting_table || null,
          item: state.item,
          result: {
            name: state.item,
            count_per_craft: state.count_per_craft,
            crafted_count: craftedGain,
          },
          ingredients: ingredients.map((entry) => ({ name: entry.name, consumed: entry.consumed })),
          before: state.before,
          after,
        }
      },
      cleanup(bot) {
        // 2×2 合成不打开任何窗口（用的是玩家自己的 inventory window）→ 没有额外状态要收，
        // 只保留与其他短动作一致的移动控制位兜底（至多一次由 ActionRuntime 保证）。
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    dropped_items: {
      // Phase 4H：读"附近有哪些掉落物实体"（SAFE 只读；**非独占** —— 纯读取，
      // 可与导航/挖/放/合成并行，但必须在线）。
      exclusive: false,
      timeout_ms: DROPPED_ITEMS_TIMEOUT_MS,
      risk: 'SAFE',
      validate() {
        return {}
      },
      async run(bot) {
        if (bot === null || bot === undefined) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        return droppedItemsView(bot)
      },
    },
    dig_capability: {
      // Phase 4J：读"当前状态下这个方块能不能挖、大概要多久"（SAFE 只读；**非独占**）。
      // 纯查询：不改世界、不改背包、不装备、不切槽、不移动、不导航 —— 与 inventory /
      // dropped_items 是同一条路（只读动作，不需要确认、不会忙）。
      exclusive: false,
      timeout_ms: DIG_CAPABILITY_TIMEOUT_MS,
      risk: 'SAFE',
      validate(params) {
        return validateBlockCoords(params)
      },
      async run(bot, params) {
        if (bot === null || bot === undefined) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        return digCapabilityView(bot, params)
      },
    },
    find_blocks: {
      // Phase 4K：找"附近有哪些指定方块"（SAFE 只读；**非独占**）。只回答位置：
      // 不移动、不装备、不挖、不拾取，也不给任何"推荐/最佳"（那要另问 capability）。
      exclusive: false,
      timeout_ms: FIND_BLOCKS_TIMEOUT_MS,
      risk: 'SAFE',
      validate(params) {
        const names = params.block_names
        if (!Array.isArray(names) || names.length === 0) {
          throw new ActionError('block_names 不能为空（要一个方块名数组）', 'action.invalid', 400)
        }
        if (names.length > FIND_BLOCKS_MAX_NAMES) {
          throw new ActionError(
            `block_names 最多 ${FIND_BLOCKS_MAX_NAMES} 个（本阶段不做批量扫描）`,
            'action.invalid',
            400,
          )
        }
        const cleaned = []
        for (const raw of names) {
          if (typeof raw !== 'string' || !raw.trim()) {
            throw new ActionError('block_names 里每一项都必须是非空字符串', 'action.invalid', 400)
          }
          const name = normalizeBlockName(raw)
          if (!name) {
            throw new ActionError('block_names 里每一项都必须是非空字符串', 'action.invalid', 400)
          }
          if (!cleaned.includes(name)) cleaned.push(name)
        }
        const rawDistance = params.max_distance
        let maxDistance = FIND_BLOCKS_DEFAULT_DISTANCE
        if (rawDistance !== undefined && rawDistance !== null && rawDistance !== '') {
          if (typeof rawDistance !== 'number' || !Number.isInteger(rawDistance)) {
            throw new ActionError('max_distance 必须是整数', 'action.invalid', 400)
          }
          if (rawDistance < 1 || rawDistance > FIND_BLOCKS_MAX_DISTANCE) {
            throw new ActionError(
              `max_distance 必须在 1~${FIND_BLOCKS_MAX_DISTANCE} 之间（不允许大范围全局扫描）`,
              'action.invalid',
              400,
            )
          }
          maxDistance = rawDistance
        }
        const rawResults = params.max_results
        let maxResults = FIND_BLOCKS_DEFAULT_RESULTS
        if (rawResults !== undefined && rawResults !== null && rawResults !== '') {
          if (typeof rawResults !== 'number' || !Number.isInteger(rawResults)) {
            throw new ActionError('max_results 必须是整数', 'action.invalid', 400)
          }
          if (rawResults < 1 || rawResults > FIND_BLOCKS_MAX_RESULTS) {
            throw new ActionError(
              `max_results 必须在 1~${FIND_BLOCKS_MAX_RESULTS} 之间`,
              'action.invalid',
              400,
            )
          }
          maxResults = rawResults
        }
        return { block_names: cleaned, max_distance: maxDistance, max_results: maxResults }
      },
      async run(bot, params) {
        if (bot === null || bot === undefined || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        return findBlocksView(bot, params)
      },
    },
    pickup_item: {
      // Phase 4H：拾取**一个明确指定**的掉落物实体（MEDIUM：改背包 + bot 会主动移动）。
      // 内部自己管 pathfinding + 目标实体 + 收集等待，绝不嵌套 move_to（那会 action.busy）。
      exclusive: true,
      timeout_ms: pickupTimeoutMs(),
      risk: 'MEDIUM',
      detached: true,
      validate(params) {
        const entityId = params.entity_id
        if (typeof entityId !== 'number' || !Number.isInteger(entityId) || entityId < 0) {
          throw new ActionError('entity_id 必须是 >= 0 的整数（用 minecraft_dropped_items 拿）', 'action.invalid', 400)
        }
        const expected = params.expected_item
        if (typeof expected !== 'string' || !expected.trim()) {
          throw new ActionError('expected_item 不能为空（第二层身份校验）', 'action.invalid', 400)
        }
        if (expected.length > MAX_PLACE_ITEM_CHARS) {
          throw new ActionError('expected_item 过长', 'action.invalid', 400)
        }
        return { entity_id: entityId, expected_item: normalizeItemName(expected) }
      },
      async start(bot, params, token, controller) {
        // §十三：执行前必须重新确认「实体还在 / 还是掉落物 / id 对得上 / 物品一致 / 距离还行」
        if (bot === null || bot.entity === null) {
          throw new ActionError('罐头还没有进入世界', 'action.not_online', 400)
        }
        const entities = bot.entities || {}
        const targetEntity = entities[params.entity_id]
        if (!targetEntity) {
          throw new ActionError(
            `附近找不到实体 #${params.entity_id}（可能已经被捡走或消失了）`,
            'item_entity.not_found',
            404,
          )
        }
        if (!isDroppedItemEntity(bot, targetEntity)) {
          throw new ActionError(
            `实体 #${params.entity_id} 不是一个掉落物（本工具只捡掉落物）`,
            'item_entity.invalid',
            422,
            { entity_id: params.entity_id },
          )
        }
        const stack = droppedItemStack(bot, targetEntity)
        if (!stack) {
          throw new ActionError(
            `读不到实体 #${params.entity_id} 上的物品（数据还没到）`,
            'item_entity.invalid',
            422,
            { entity_id: params.entity_id },
          )
        }
        if (stack.name !== params.expected_item) {
          throw new ActionError(
            `实体 #${params.entity_id} 上是 ${stack.name}，不是 ${params.expected_item}`,
            'item_entity.changed',
            409,
            { expected: params.expected_item, actual: stack.name },
          )
        }
        const distance = distanceToEntity(bot, targetEntity)
        const maxDistance = pickupMaxDistance()
        if (distance !== null && distance > maxDistance) {
          throw new ActionError(
            `掉落物距离 ${distance} 格，超过上限 ${maxDistance} 格（本阶段不会追太远）`,
            'pickup.target_too_far',
            422,
            { distance, max_distance: maxDistance },
          )
        }
        // §三十六：监听收集 / 实体消失（cleanup 与 wait 都会摘，幂等）
        const listeners = []
        const collectedBy = { bot: false, other: false }
        const scope = { collectedBy, gone: false, goneAt: 0, listeners }
        const onCollect = (collector, collected) => {
          if (collected !== targetEntity) return
          if (collector === bot.entity) collectedBy.bot = true
          else collectedBy.other = true
        }
        const onGone = (entity) => {
          if (entity !== targetEntity) return
          scope.gone = true
          scope.goneAt = Date.now()
        }
        if (typeof bot.on === 'function') {
          bot.on('playerCollect', onCollect)
          bot.on('entityGone', onGone)
          listeners.push(['playerCollect', onCollect], ['entityGone', onGone])
        }
        if (controller) controller.pickupListeners = listeners
        // §二十二/§二十四：官方动态 Goal（跟着活 entity 走），不嵌套 move_to
        bot.pathfinder.setGoal(new goals.GoalFollow(targetEntity, PICKUP_RADIUS), true)
        log('info', 'pickup started', {
          entity_id: params.entity_id,
          item: stack.name,
          distance,
        })
        return {
          // 内部状态（只给 wait 用，绝不进事件）：持有**实体对象引用**做身份绑定
          targetEntity,
          scope,
          entity_id: params.entity_id,
          expected_item: params.expected_item,
          count_before: stack.count,
          distance_start: distance,
          distance_collected: null,
          inventory_before: countInventoryItem(bot, params.expected_item),
          navigating: true,
        }
      },
      wait(bot, params, token, state) {
        const scope = state.scope
        const maxDistance = pickupMaxDistance()
        return new Promise((resolvePromise, rejectPromise) => {
          let settled = false
          const finish = (error, value) => {
            if (settled) return
            settled = true
            clearInterval(timer)
            detachPickupListeners(bot, scope.listeners)
            if (error) rejectPromise(error)
            else resolvePromise(value)
          }
          const fail = (message, code, status, detail) => {
            // 失败时自己收导航（绝不留残余 Goal）
            releasePickupNavigation(bot)
            finish(new ActionError(message, code, status, detail || null))
          }
          const timer = setInterval(() => {
            if (token && token.cancelled) {
              finish(null)
              return
            }
            const current = bot.entities ? bot.entities[state.entity_id] : null
            const replaced = current !== state.targetEntity
            const sawEvent = scope.gone || scope.collectedBy.bot || scope.collectedBy.other
            // §二十五：身份绑定 —— id 被重新分配/实体被替换时**绝不**自动改绑
            // （"被收集/消失"不算替换：那种情况交给下面的收集确认去判）
            if (replaced && !sawEvent) {
              fail(
                `实体 #${state.entity_id} 已经被替换成了别的实体（不自动改绑）`,
                'target.replaced',
                409,
                { entity_id: state.entity_id },
              )
              return
            }
            // §三十五：物品被换掉 → 立即失败
            const stack = droppedItemStack(bot, state.targetEntity)
            if (stack && stack.name !== state.expected_item) {
              fail(
                `实体 #${state.entity_id} 上的物品变成了 ${stack.name}`,
                'item_entity.changed',
                409,
                { expected: state.expected_item, actual: stack.name },
              )
              return
            }
            // §三十四：别的玩家先捡走了 → 立刻失败，绝不追替代实体
            if (scope.collectedBy.other) {
              fail(
                `实体 #${state.entity_id} 被别的玩家捡走了`,
                'pickup.target_lost',
                409,
                { entity_id: state.entity_id },
              )
              return
            }
            // 实体还在才谈距离（被收集掉之后就没有距离可算了）
            if (!replaced) {
              const distance = distanceToEntity(bot, state.targetEntity)
              // §二十六：目标被拉远 → 停止（不无限追）
              if (distance !== null && distance > maxDistance) {
                fail(
                  `掉落物跑到 ${distance} 格之外，超过上限 ${maxDistance} 格`,
                  'pickup.target_too_far',
                  422,
                  { distance, max_distance: maxDistance },
                )
                return
              }
              // §二十八：进入拾取半径 → 停导航，等服务器收集
              if (state.navigating && distance !== null && distance <= PICKUP_RADIUS) {
                state.navigating = false
                state.distance_collected = distance
                releasePickupNavigation(bot)
              }
            }
            // §二十九/§三十三：只要"我们这边的收集/消失信号"到了就去核对背包；
            // **真正算不算成功只看 inventory 有没有增加**（下面那条硬门禁）
            const collected = scope.collectedBy.bot || scope.gone
            if (!collected) return
            const inventoryAfter = countInventoryItem(bot, state.expected_item)
            if (inventoryAfter > state.inventory_before) {
              // §三十六：**成功也必须收掉导航** —— 服务器可能在两次轮询之间就把物品收进背包
              // （掉落物掉到下层、罐头跟着掉下去正好踩到），这时上面的"进入半径"分支从来没跑过。
              // SUCCEEDED 不触发 cleanup（ActionRuntime 的既定契约），所以这里必须自己收。
              if (state.navigating) {
                state.navigating = false
                releasePickupNavigation(bot)
              }
              finish(null, {
                entity_id: state.entity_id,
                item: { name: state.expected_item, count_before: state.count_before },
                distance_start: state.distance_start,
                distance_collected: state.distance_collected,
                inventory_before: state.inventory_before,
                inventory_after: inventoryAfter,
                collected_count: inventoryAfter - state.inventory_before,
                collected: true,
              })
              return
            }
            // entityGone 但背包还没到账：给一小段宽限（inventory 包可能滞后）
            if (scope.gone && Date.now() - scope.goneAt > pickupInventoryGraceMs()) {
              fail(
                `实体 #${state.entity_id} 消失了，但背包里的 ${state.expected_item} 没有增加`,
                'pickup.unconfirmed',
                500,
                { inventory_before: state.inventory_before, inventory_after: inventoryAfter },
              )
            }
          }, pickupPollMs())
        })
      },
      cleanup(bot, controller) {
        // §三十六：cleanup 至少 setGoal(null) + clearControlStates，且至多一次
        detachPickupListeners(bot, controller ? controller.pickupListeners : null)
        releasePickupNavigation(bot)
      },
    },
    follow_player: {
      // Phase 3D：动态跟随（LOW；不改世界，但属于持续自动移动 → exclusive + STOP + timeout）
      exclusive: true,
      timeout_ms: FOLLOW_TIMEOUT_MS,
      risk: 'LOW',
      detached: true, // 持续型动作：启动即返回 RUNNING，终态由事件送达（§八）
      validate(params) {
        const username = String(params.username ?? '')
        if (!username.trim()) {
          throw new ActionError('username 不能为空', 'action.invalid', 400)
        }
        if (username.length > 16) {
          throw new ActionError('username 最长 16 个字符', 'action.invalid', 400)
        }
        // 控制字符（含换行/制表/空格类不可见字符）一律拒绝
        // eslint-disable-next-line no-control-regex
        if (/[\u0000-\u001f\u007f]/.test(username)) {
          throw new ActionError('username 不能包含控制字符', 'action.invalid', 400)
        }
        const raw = params.distance
        const distance =
          raw === undefined || raw === null || raw === '' ? FOLLOW_DEFAULT_DISTANCE : raw
        if (typeof distance !== 'number' || !Number.isFinite(distance)) {
          throw new ActionError('distance 必须是数字', 'action.invalid', 400)
        }
        if (distance < FOLLOW_MIN_DISTANCE || distance > FOLLOW_MAX_DISTANCE) {
          throw new ActionError(
            `distance 必须在 ${FOLLOW_MIN_DISTANCE}~${FOLLOW_MAX_DISTANCE} 格之间`,
            'action.invalid',
            400,
          )
        }
        return { username, distance }
      },
      async start(bot, params) {
        const { username, distance } = params
        // §五：玩家目标解析——player 与 player.entity 都要存在，否则 player_not_found（404，
        // 且绝不启动 Pathfinder）；启动阶段抛错会同步反馈给调用方
        const player = bot.players[username]
        const targetEntity = player && player.entity ? player.entity : null
        if (!targetEntity) {
          throw new ActionError(`找不到玩家 ${username}（不在线或不在视野内）`, 'player.not_found', 404)
        }
        // §六/§七/§十三：官方 Dynamic Goal——持活 entity 引用，玩家移动时由 pathfinder
        // 通过 goal.hasChanged() 自动重规划（绝不缓存静态坐标、绝不手写轮询 setGoal）
        bot.pathfinder.setGoal(new goals.GoalFollow(targetEntity, distance), true)
        log('info', 'follow started', { username, distance })
        return { targetEntity, lastSeenAt: Date.now() }
      },
      wait(bot, params, token, followState) {
        const { username, distance } = params
        const resolveEntity = () => {
          const player = bot.players[username]
          return player && player.entity ? player.entity : null
        }
        let targetEntity = followState.targetEntity
        let lastSeenAt = followState.lastSeenAt
        const graceMs = followLostGraceMs()
        const chaseLimit = followMaxChaseDistance()
        return new Promise((resolvePromise, rejectPromise) => {
          let settled = false
          let lastSeenAt = Date.now()
          const finish = (error) => {
            if (settled) return
            settled = true
            clearInterval(timer)
            if (error) rejectPromise(error)
            else resolvePromise()
          }
          const fail = (message, code, status) => {
            // 失败时动作自己清 Goal（与 move_to 一致：绝不留残余导航意图）
            try {
              bot.pathfinder.setGoal(null)
            } catch (cleanupError) {
              log('warn', 'follow failure cleanup failed', { error: cleanupError.message })
            }
            finish(new ActionError(message, code, status))
          }
          const timer = setInterval(() => {
            if (token && token.cancelled) {
              // 取消（stop/disconnect/shutdown/timeout）由上层按 CANCELLED/TIMEOUT 收尾 + cleanup
              finish(null)
              return
            }
            const entity = resolveEntity()
            if (!entity) {
              // §十 E/§十六：玩家瞬间被移除一帧不算失败；连续超过宽限期才 FAILED player_lost
              if (Date.now() - lastSeenAt > graceMs) {
                fail(
                  `跟随目标 ${username} 已消失超过 ${Math.round(graceMs / 1000)} 秒`,
                  'player.lost',
                  500,
                )
              }
              return
            }
            lastSeenAt = Date.now()
            // §十五：只有旧 entity 失效（服务器重建引用）才重绑 Dynamic Goal；
            // 正常移动绝不 setGoal（那是 pathfinder goal_moved 的职责）
            if (entity !== targetEntity) {
              targetEntity = entity
              try {
                bot.pathfinder.setGoal(new goals.GoalFollow(targetEntity, distance), true)
              } catch (error) {
                fail(`重建跟随目标失败：${error.message}`, 'action.failed', 500)
                return
              }
            }
            // §十七：最大追逐距离——目标跑太远就失败，不追到世界尽头
            const self = bot.entity ? bot.entity.position : null
            const targetPosition = targetEntity.position
            if (self && targetPosition) {
              const gap = self.distanceTo(targetPosition)
              if (gap > chaseLimit) {
                fail(
                  `跟随目标距离 ${gap.toFixed(1)} 格，超过上限 ${chaseLimit} 格`,
                  'follow.target_too_far',
                  500,
                )
              }
            }
          }, 250)
        })
      },
      cleanup(bot) {
        // 与 move_to 相同的两件套：先硬清 Goal（真正停止导航），再清控制位
        if (bot && bot.pathfinder) {
          try {
            bot.pathfinder.setGoal(null)
          } catch (error) {
            log('warn', 'follow cleanup setGoal(null) failed', { error: error.message })
          }
        }
        if (bot && typeof bot.clearControlStates === 'function') bot.clearControlStates()
      },
    },
    stop: {
      // 控制面动作：不占前台、不产生自己的动作记录；幂等、无 bot 也安全（§六）
      control: true,
      timeout_ms: 3000,
      risk: 'SAFE',
    },
}

const actionRuntime = createActionRuntime({
  registry: ACTION_REGISTRY,
  getBot: () => state.bot,
  isOnline: () => state.phase === 'ONLINE' && state.bot !== null,
  emit: (event, data) => pushEvent(event, data),
  log: (message) => log('info', message),
})

// ------------------------------------------------------------------- bot wiring

function describeReason(reason) {
  if (reason == null) return ''
  if (typeof reason === 'string') return reason
  try {
    return JSON.stringify(reason)
  } catch {
    return String(reason)
  }
}

const recentChats = new Map() // "username\u0000message" → ts

function seenChatBefore(key) {
  const now = Date.now()
  for (const [existing, ts] of recentChats) {
    if (now - ts > CHAT_DEDUPE_TTL_MS) recentChats.delete(existing)
  }
  const previous = recentChats.get(key)
  recentChats.set(key, now)
  return previous !== undefined
}

function wireBot(bot) {
  bot.on('login', () => {
    state.username = bot.username
    recentChats.clear() // 新会话从零开始，旧会话的去重记录不能吞掉新消息
    if (CONNECTING_PHASES.has(state.phase)) {
      state.phase = 'CONNECTED'
      log('info', 'login ok', { username: state.username })
      pushEvent('minecraft.connected', { username: state.username })
    }
  })

  bot.on('spawn', () => {
    const firstSpawn = state.phase !== 'ONLINE'
    state.phase = 'ONLINE'
    state.lastError = null
    state.kickedReason = null
    state.connectedAt = Date.now() / 1000
    // Phase 3C：进入世界后初始化 Pathfinder Movements（最保守：不挖不放）
    try {
      bot.pathfinder.setMovements(configureMovements(new Movements(bot)))
    } catch (error) {
      log('warn', 'pathfinder movements init failed', { error: error.message })
    }
    captureWorldState(bot)
    log('info', 'spawned', { dimension: state.dimension, position: state.position })
    if (firstSpawn) pushEvent('minecraft.spawned', { username: state.username })
  })

  bot.on('move', () => {
    if (state.phase === 'ONLINE' && bot.entity) captureWorldState(bot)
  })

  bot.on('health', () => {
    if (state.phase === 'ONLINE') state.health = typeof bot.health === 'number' ? bot.health : null
  })

  bot.on('chat', (username, message) => {
    if (typeof username !== 'string' || typeof message !== 'string') return
    if (username === bot.username) return // 自己的话由服务器回声，不再上报
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message })
  })

  bot.on('whisper', (username, message) => {
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message, private: true })
  })

  // 兜底：非原版聊天格式的服务器不会触发 'chat'，但 'message' 一定有。
  bot.on('message', (jsonMsg) => {
    if (state.phase !== 'ONLINE') return
    const text = typeof jsonMsg?.toString === 'function' ? jsonMsg.toString() : ''
    if (!text) return
    // 加入/离开提示属于系统行（player_joined / player_left 已单独上报），
    // 不该混进 chat 事件。
    if (/(?:joined|left) the game\.?\s*$/i.test(text)) return
    const match = /^<(.{1,16}?)>\s?([\s\S]*)$/.exec(text)
    const username = match ? match[1] : ''
    const message = match ? match[2] : text
    if (username === bot.username) return
    if (seenChatBefore(`${username}\u0000${message}`)) return
    pushEvent('minecraft.chat', { username, message, raw: text })
  })

  bot.on('playerJoined', (player) => {
    // Phase 5C：事件里带上 uuid，身份桥（IdentityLink）用它做 canonical key
    pushEvent('minecraft.player_joined', {
      username: player?.username ?? null,
      uuid: player?.uuid ?? null,
    })
  })

  bot.on('playerLeft', (player) => {
    pushEvent('minecraft.player_left', {
      username: player?.username ?? null,
      uuid: player?.uuid ?? null,
    })
  })

  bot.on('kicked', (reason) => {
    state.kickedReason = describeReason(reason)
    log('warn', 'kicked', { reason: state.kickedReason })
    pushEvent('minecraft.kicked', { reason: state.kickedReason, username: state.username })
  })

  bot.on('error', (error) => {
    state.lastError = String(error && error.message ? error.message : error)
    state.phase = 'ERROR'
    clearConnectTimer()
    log('error', 'bot error', { error: state.lastError })
    pushEvent('minecraft.error', { error: state.lastError })
  })

  bot.on('end', () => {
    clearConnectTimer()
    const wasActive = state.bot === bot
    state.bot = null
    if (!wasActive && !state.stopping) return // 旧实例的余波
    // Phase 3B：会话结束 → 进行中的动作全部按 CANCELLED 结束（无僵尸 action）
    const cancelledActions = actionRuntime.cancelAll('disconnect')
    if (state.phase !== 'ERROR') state.phase = 'DISCONNECTED'
    state.dimension = null
    state.position = null
    state.health = null
    log('info', 'bot ended', { phase: state.phase, cancelled_actions: cancelledActions.length })
    pushEvent('minecraft.disconnected', {
      username: state.username,
      reason: state.kickedReason || state.lastError || null,
    })
  })
}

function captureWorldState(bot) {
  state.dimension = bot.game?.dimension ?? null
  const position = bot.entity?.position
  state.position =
    position && typeof position.x === 'number'
      ? {
          x: Math.round(position.x * 100) / 100,
          y: Math.round(position.y * 100) / 100,
          z: Math.round(position.z * 100) / 100,
        }
      : null
  state.health = typeof bot.health === 'number' ? bot.health : null
}

function clearConnectTimer() {
  if (state.connectTimer !== null) {
    clearTimeout(state.connectTimer)
    state.connectTimer = null
  }
}

// ----------------------------------------------------------------- lifecycle ops

class BridgeError extends Error {
  constructor(message, code) {
    super(message)
    this.code = code
  }
}

function startConnect(host, port) {
  if (state.stopping) {
    throw new BridgeError('runtime 正在关闭，暂不接受新的连接', 'runtime.stopping')
  }
  if (ACTIVE_PHASES.has(state.phase)) {
    throw new BridgeError('已有 bot 会话在运行，先 disconnect 再重新 connect', 'session.active')
  }
  const auth = readAuth()
  state.sessionId = `mc_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
  state.host = host
  state.port = port
  state.authMode = auth.mode
  state.username = null
  state.dimension = null
  state.position = null
  state.health = null
  state.lastError = null
  state.kickedReason = null
  state.phase = auth.mode === 'microsoft' ? 'AUTHENTICATING' : 'CONNECTING'

  const options = { host, port, auth: auth.mode, hideErrors: true }
  if (auth.mode === 'microsoft') {
    options.username = auth.email
    if (auth.password) options.password = auth.password
  } else {
    options.username = auth.username
  }

  log('info', 'connecting', { host, port, auth_mode: auth.mode, session_id: state.sessionId })
  pushEvent('minecraft.connecting', { host, port })

  try {
    state.bot = mineflayer.createBot(options)
    state.bot.loadPlugin(pathfinder.pathfinder) // Phase 3C：导航能力（move_to 用；注入函数在 .pathfinder）
  } catch (error) {
    state.phase = 'ERROR'
    state.lastError = String(error && error.message ? error.message : error)
    pushEvent('minecraft.error', { error: state.lastError })
    throw new BridgeError(`无法启动 Minecraft 连接：${state.lastError}`, 'connect.failed')
  }
  wireBot(state.bot)

  // 连接看门狗：限时未进世界 → 报错并清理，绝不卡在中间态。
  state.connectTimer = setTimeout(() => {
    if (state.phase === 'ONLINE' || state.phase === 'DISCONNECTED' || state.phase === 'ERROR') return
    const message = `连接超时：${CONNECT_TIMEOUT_S}s 内没有进入世界（host=${host}:${port}）`
    log('error', message)
    state.lastError = message
    state.phase = 'ERROR'
    pushEvent('minecraft.error', { error: message })
    try {
      state.bot?.quit()
      state.bot?.end()
    } catch {
      state.bot = null
    }
  }, CONNECT_TIMEOUT_MS)
  if (typeof state.connectTimer.unref === 'function') state.connectTimer.unref()

  return { session_id: state.sessionId, status: state.phase }
}

function requestDisconnect() {
  clearConnectTimer()
  if (state.bot !== null) {
    state.stopping = false
    if (ACTIVE_PHASES.has(state.phase)) state.phase = 'DISCONNECTING'
    const bot = state.bot
    try {
      bot.quit()
    } catch {
      try {
        bot.end()
      } catch {
        state.bot = null
        if (state.phase === 'DISCONNECTING') state.phase = 'DISCONNECTED'
      }
    }
    return true
  }
  // 幂等：没有 bot 时直接落回 DISCONNECTED（ERROR 也要能被 disconnect 清掉）。
  if (state.phase !== 'DISCONNECTED') {
    state.phase = 'DISCONNECTED'
    state.lastError = null
  }
  return false
}

function statusPayload() {
  const action = actionRuntime.snapshot()
  return {
    runtime_version: RUNTIME_VERSION,
    status: state.phase,
    session_id: state.sessionId,
    host: state.host,
    port: state.port,
    username: state.username,
    auth_mode: state.authMode,
    dimension: state.dimension,
    position: state.position,
    health: state.health,
    last_error: state.lastError,
    kicked_reason: state.kickedReason,
    connected_at: state.connectedAt,
    uptime_seconds: Math.round((Date.now() - state.startedAt) / 1000),
    // Phase 3B：当前动作（前台优先，否则最近一次终态；从未有过 → IDLE）
    action: { ...action.current, active_count: action.active_count },
    // Phase 3C：Pathfinder 诊断（goal 类型 / 目标坐标 / 是否在移动）——只读
    pathfinder: pathfinderStatus(),
  }
}

// ------------------------------------------------------------------ HTTP server

const MAX_BODY_BYTES = 1 << 16

function readBody(request) {
  return new Promise((resolve, reject) => {
    let size = 0
    const chunks = []
    request.on('data', (chunk) => {
      size += chunk.length
      if (size > MAX_BODY_BYTES) {
        reject(new BridgeError('请求体过大', 'request.too_large'))
        request.destroy()
        return
      }
      chunks.push(chunk)
    })
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      if (!raw) {
        resolve({})
        return
      }
      try {
        const parsed = JSON.parse(raw)
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
          reject(new BridgeError('请求体必须是 JSON 对象', 'request.malformed'))
          return
        }
        resolve(parsed)
      } catch (error) {
        reject(new BridgeError(`JSON 解析失败：${error.message}`, 'request.malformed'))
      }
    })
    request.on('error', () => reject(new BridgeError('读取请求体失败', 'request.malformed')))
  })
}

function jsonResponse(response, status, payload) {
  const body = JSON.stringify(payload)
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  })
  response.end(body)
}

function parseTarget(body) {
  const host = String(body.host ?? '').trim()
  if (!host) throw new BridgeError('缺少 host', 'target.invalid')
  if (host.length > 253) throw new BridgeError('host 过长', 'target.invalid')
  const rawPort = body.port === undefined || body.port === null || body.port === '' ? 25565 : body.port
  const port = Number(rawPort)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new BridgeError('port 必须是 1-65535 的整数', 'target.invalid')
  }
  return { host, port }
}

const server = http.createServer((request, response) => {
  void handleRequest(request, response)
})

async function handleRequest(request, response) {
  const path = (request.url || '/').split('?')[0]
  try {
    if (BRIDGE_TOKEN) {
      const supplied = request.headers.authorization || ''
      if (supplied !== `Bearer ${BRIDGE_TOKEN}`) {
        jsonResponse(response, 401, { ok: false, error: { code: 'auth.invalid', message: 'bridge token 无效' } })
        return
      }
    }
    if (request.method === 'GET' && path === '/minecraft/health') {
      jsonResponse(response, 200, { ok: true, uptime_seconds: Math.round((Date.now() - state.startedAt) / 1000) })
      return
    }
    if (request.method === 'GET' && path === '/minecraft/status') {
      jsonResponse(response, 200, { ok: true, ...statusPayload() })
      return
    }
    if (request.method === 'GET' && path === '/minecraft/world/snapshot') {
      // 只读世界感知（Phase 2）：未在线时返回 online=false，缓存由上层失效。
      if (state.phase !== 'ONLINE' || state.bot === null) {
        jsonResponse(response, 200, { ok: true, online: false, fetched_at: Date.now() / 1000 })
        return
      }
      const layers = parseLayers(new URL(request.url, 'http://localhost').searchParams.get('layers'))
      try {
        const snapshot = buildWorldSnapshot(state.bot, layers)
        jsonResponse(response, 200, {
          ok: true,
          online: true,
          fetched_at: Date.now() / 1000,
          ...snapshot,
        })
      } catch (error) {
        // 感知失败不该杀死 bot 会话：如实上报错误。
        const message = String(error && error.message ? error.message : error)
        log('warn', 'world snapshot failed', { error: message })
        jsonResponse(response, 500, {
          ok: false,
          error: { code: 'world.snapshot_failed', message },
        })
      }
      return
    }
    if (request.method === 'POST' && path === '/minecraft/connect') {
      const body = await readBody(request)
      const { host, port } = parseTarget(body)
      const result = startConnect(host, port)
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/chat') {
      // Phase 1 API contract 保持不变（错误码/校验顺序照旧），内部改由
      // ActionRuntime 托管：chat 有自己的 action_id 与 started/completed 事件，
      // 作为非互斥通信动作可与前台动作并存（Phase 3B §七）。
      const body = await readBody(request)
      const message = String(body.message ?? '')
      if (!message.trim()) {
        throw new BridgeError('message 不能为空', 'chat.empty')
      }
      if (message.length > CHAT_MAX_CHARS) {
        throw new BridgeError(`message 超过 ${CHAT_MAX_CHARS} 字符上限`, 'chat.too_long')
      }
      if (state.phase !== 'ONLINE' || state.bot === null) {
        throw new BridgeError('bot 不在线，无法发言', 'chat.not_online')
      }
      const result = await actionRuntime.execute('chat', { message })
      jsonResponse(response, 200, { ok: true, sent: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/look_at') {
      // SAFE 动作：只改朝向，不改世界、不移动（yaw/pitch 数学由 runtime 处理）
      const body = await readBody(request)
      const result = await actionRuntime.execute('look_at', { x: body.x, y: body.y, z: body.z })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/move_to') {
      // Phase 3C 起：非破坏性导航（禁 dig/place）；Phase 3E 起为持续型动作——
      // 启动即返回 RUNNING，终点/失败经 minecraft.action.* 事件送达
      const body = await readBody(request)
      const result = await actionRuntime.execute('move_to', { x: body.x, y: body.y, z: body.z })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'GET' && path === '/minecraft/inventory') {
      // Phase 4C：只读背包切片（按物品名聚合，绝不出 window/slot/NBT）
      jsonResponse(response, 200, inventorySlice(state.bot))
      return
    }
    if (request.method === 'GET' && path === '/minecraft/inventory/slots') {
      // Phase 4D：**调试**槽位视图（WebUI Move Test / smoke 用；LLM 工具绝不读它）
      jsonResponse(response, 200, inventorySlots(state.bot))
      return
    }
    if (request.method === 'POST' && path === '/minecraft/equip') {
      // Phase 4D：把指定物品拿到主手（MEDIUM）。启动即 RUNNING，结果经事件送达。
      const body = await readBody(request)
      const result = await actionRuntime.execute('equip', { item: body.item })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/inventory_move') {
      // Phase 4D：单物品、单来源槽、单目标槽、单数量（MEDIUM）。
      const body = await readBody(request)
      const result = await actionRuntime.execute('inventory_move', {
        source_slot: body.source_slot,
        destination_slot: body.destination_slot,
        item: body.item,
        count: body.count,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/dropped_items') {
      // Phase 4H：读附近的掉落物实体（SAFE 只读；同步返回语义投影）
      await readBody(request).catch(() => ({}))
      const result = await actionRuntime.execute('dropped_items', {})
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/find_blocks') {
      // Phase 4K：找附近的指定方块（SAFE 只读；同步返回语义投影）
      const body = await readBody(request)
      const result = await actionRuntime.execute('find_blocks', {
        block_names: body.block_names,
        max_distance: body.max_distance,
        max_results: body.max_results,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/dig_capability') {
      // Phase 4J：读"这个方块现在能不能挖、大概多久"（SAFE 只读；同步返回语义投影）
      const body = await readBody(request)
      const result = await actionRuntime.execute('dig_capability', {
        x: body.x,
        y: body.y,
        z: body.z,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/pickup_item') {
      // Phase 4H：捡起一个明确的掉落物实体（MEDIUM）。启动即 RUNNING，终态经事件送达。
      const body = await readBody(request)
      const result = await actionRuntime.execute('pickup_item', {
        entity_id: body.entity_id,
        expected_item: body.expected_item,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/recipe_lookup') {
      // Phase 4F/4G：查配方（不带工作台 = 玩家 2×2；带坐标 = 那张工作台的 3×3）
      const body = await readBody(request)
      const result = await actionRuntime.execute('recipe_lookup', {
        item: body.item,
        crafting_table: body.crafting_table,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/craft') {
      // Phase 4F/4G：执行一次配方（MEDIUM）。启动即 RUNNING，结果经事件送达。
      const body = await readBody(request)
      const result = await actionRuntime.execute('craft', {
        recipe_id: body.recipe_id,
        crafting_table: body.crafting_table,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/container_inspect') {
      // Phase 4E：读一个 Chest / Barrel 的真实内容（SAFE 只读；内部 open → read → close）
      const body = await readBody(request)
      const result = await actionRuntime.execute('container_inspect', {
        x: body.x,
        y: body.y,
        z: body.z,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/container_transfer') {
      // Phase 4E：单物品在「容器槽 ↔ 背包槽」之间移动一次（MEDIUM）。启动即 RUNNING。
      const body = await readBody(request)
      const result = await actionRuntime.execute('container_transfer', {
        x: body.x,
        y: body.y,
        z: body.z,
        direction: body.direction,
        container_slot: body.container_slot,
        inventory_slot: body.inventory_slot,
        item: body.item,
        count: body.count,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/place') {
      // Phase 4C：放置单方块（MEDIUM）。启动即 RUNNING，结果经事件送达。
      const body = await readBody(request)
      const result = await actionRuntime.execute('place', {
        x: body.x,
        y: body.y,
        z: body.z,
        face: body.face,
        expected_item: body.expected_item,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/dig') {
      // Phase 4B：破坏单方块（MEDIUM）。启动即 RUNNING，结果经事件送达。
      const body = await readBody(request)
      const result = await actionRuntime.execute('dig', {
        x: body.x,
        y: body.y,
        z: body.z,
        expected_block: body.expected_block,
        // Phase 4I：可选 —— 给了就在 start 里实时校验主手（缺省/空 = Phase 4B 行为）
        expected_tool: body.expected_tool,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/follow_player') {
      // Phase 3D：动态跟随（GoalFollow + dynamic；STOP/timeout/disconnect 都会真停）
      const body = await readBody(request)
      const result = await actionRuntime.execute('follow_player', {
        username: body.username,
        distance: body.distance,
      })
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/stop') {
      // 最高优先级安全停止：幂等、无 bot 也安全，返回被取消的 action_id 列表
      await readBody(request).catch(() => ({}))
      const result = actionRuntime.stop()
      jsonResponse(response, 200, { ok: true, ...result })
      return
    }
    if (request.method === 'POST' && path === '/minecraft/disconnect') {
      await readBody(request).catch(() => ({}))
      const hadBot = requestDisconnect()
      jsonResponse(response, 200, { ok: true, status: state.phase, had_bot: hadBot })
      return
    }
    jsonResponse(response, 404, { ok: false, error: { code: 'route.unknown', message: `未知路由 ${request.method} ${path}` } })
  } catch (error) {
    if (error instanceof BridgeError) {
      jsonResponse(response, error.code === 'session.active' ? 409 : 400, {
        ok: false,
        error: { code: error.code, message: error.message },
      })
      return
    }
    if (error instanceof ActionError) {
      // 动作层的稳定错误码（action.unknown/not_online/invalid/busy/failed）
      const body = { code: error.code, message: error.message }
      if (error.detail) body.detail = error.detail
      jsonResponse(response, error.status, { ok: false, error: body })
      return
    }
    log('error', 'bridge request failed', { error: String(error && error.stack ? error.stack : error) })
    jsonResponse(response, 500, { ok: false, error: { code: 'internal.error', message: 'runtime 内部错误' } })
  }
}

// ------------------------------------------------------------------ resilience

process.on('uncaughtException', (error) => {
  // bot 层的意外异常绝不带走进程：记录、上报、继续服务。
  const message = String(error && error.message ? error.message : error)
  log('error', 'uncaught exception', { error: message })
  try {
    state.lastError = message
    pushEvent('minecraft.error', { error: message, uncaught: true })
  } catch {
    /* 事件通道坏了也要活 */
  }
})

process.on('unhandledRejection', (reason) => {
  const message = String(reason instanceof Error ? reason.message : reason)
  log('error', 'unhandled rejection', { error: message })
})

function shutdown(signal) {
  log('info', 'shutting down', { signal })
  state.stopping = true
  clearConnectTimer()
  actionRuntime.cancelAll('shutdown') // 收尾：不留任何进行中的动作
  if (state.bot !== null) {
    try {
      state.bot.quit()
    } catch {
      /* 忽略 */
    }
  }
  // 先关 HTTP 服务器再退出；兜底定时器不 unref，保证进程一定死。
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 1500)
}

if (require.main === module) {
  process.on('SIGINT', () => shutdown('SIGINT'))
  process.on('SIGTERM', () => shutdown('SIGTERM'))
}

// 供测试 require（不监听端口、不注册信号处理）：注册表 / Movements 配置 / 诊断
module.exports = {
  ACTION_REGISTRY,
  actionRuntime,
  configureMovements,
  pathfinderStatus,
  MOVE_DEFAULTS: {
    radius: MOVE_TO_RADIUS,
    maxDistance: MOVE_MAX_DISTANCE,
    timeoutMs: MOVE_TIMEOUT_MS,
  },
  EQUIP_DEFAULTS: { timeoutMs: EQUIP_DEFAULT_TIMEOUT_MS },
  INVENTORY_MOVE_DEFAULTS: { timeoutMs: MOVE_DEFAULT_TIMEOUT_MS },
  PICKUP_DEFAULTS: {
    timeoutMs: PICKUP_DEFAULT_TIMEOUT_MS,
    maxDistance: PICKUP_MAX_DISTANCE,
    radius: PICKUP_RADIUS,
    maxItems: PICKUP_MAX_ITEMS,
    pollMs: PICKUP_POLL_MS,
    inventoryGraceMs: PICKUP_INVENTORY_GRACE_MS,
    droppedItemsTimeoutMs: DROPPED_ITEMS_TIMEOUT_MS,
  },
  FIND_BLOCKS_DEFAULTS: {
    timeoutMs: FIND_BLOCKS_TIMEOUT_MS,
    maxDistance: FIND_BLOCKS_DEFAULT_DISTANCE,
    maxResults: FIND_BLOCKS_DEFAULT_RESULTS,
    hardMaxDistance: FIND_BLOCKS_MAX_DISTANCE,
    hardMaxResults: FIND_BLOCKS_MAX_RESULTS,
    maxNames: FIND_BLOCKS_MAX_NAMES,
  },
  findBlocksView,
  blockDistanceView,
  DIG_CAPABILITY_DEFAULTS: {
    timeoutMs: DIG_CAPABILITY_TIMEOUT_MS,
    reasons: [...DIG_CAPABILITY_REASONS],
  },
  digCapabilityView,
  isDroppedItemEntity,
  droppedItemStack,
  droppedItemSlotIndex,
  droppedItemsView,
  CRAFT_DEFAULTS: {
    timeoutMs: CRAFT_DEFAULT_TIMEOUT_MS,
    lookupTimeoutMs: LOOKUP_DEFAULT_TIMEOUT_MS,
    maxRecipeIdChars: CRAFT_MAX_RECIPE_ID_CHARS,
    maxEntries: RECIPE_MAX_ENTRIES,
    craftingTableMaxDistance: CRAFTING_TABLE_MAX_DISTANCE,
    craftingTableBlock: CRAFTING_TABLE_BLOCK,
  },
  validateCraftingTableParam,
  requireCraftingTableBlock,
  recipeIdOf,
  recipeIdsForList,
  readableRecipeKey,
  describeRecipe,
  describeRecipeIngredients,
  describeRecipeResult,
  itemNameFromRecipeId,
  countInventoryItem,
  inventoryCountsFor,
  recipeSetsFor,
  CONTAINER_DEFAULTS: {
    timeoutMs: CONTAINER_DEFAULT_TIMEOUT_MS,
    maxDistance: CONTAINER_MAX_DISTANCE,
    slotCount: CONTAINER_SLOT_COUNT,
    playerSlots: CONTAINER_PLAYER_SLOTS,
    directions: [...CONTAINER_DIRECTIONS],
  },
  CONTAINER_BLOCK_TYPES,
  containerTypeLabel,
  containerSlotCountOf,
  requireSingleContainerWindow,
  windowSlotForInventorySlot,
  readContainerSlots,
  describeItem,
  itemStackCapacity,
  closeContainerWindow,
  closeTrackedWindow,
  PLAYER_WINDOW_SLOTS,
  inventorySlotBounds,
  inventorySlots,
  findInventoryItem,
  PLACE_DEFAULTS: {
    timeoutMs: PLACE_DEFAULT_TIMEOUT_MS,
    maxDistance: PLACE_MAX_DISTANCE,
    faces: Object.keys(PLACE_FACES),
  },
  PLACE_FACES,
  normalizeItemName,
  inventorySlice,
  DIG_DEFAULTS: {
    timeoutMs: DIG_DEFAULT_TIMEOUT_MS,
    maxDistance: DIG_MAX_DISTANCE,
  },
  FOLLOW_DEFAULTS: {
    distance: FOLLOW_DEFAULT_DISTANCE,
    minDistance: FOLLOW_MIN_DISTANCE,
    maxDistance: FOLLOW_MAX_DISTANCE,
    timeoutMs: FOLLOW_TIMEOUT_MS,
    maxChaseDistance: FOLLOW_MAX_CHASE_DISTANCE,
    lostGraceMs: 3000,
  },
}

if (require.main === module) {
  server.listen(PORT, '127.0.0.1', () => {
    log('info', 'minecraft runtime listening', {
      port: PORT,
      callback: CALLBACK_URL ? 'configured' : 'none (events poll-only)',
      auth_file: AUTH_FILE,
    })
  })
}

server.on('error', (error) => {
  // 端口被占/权限问题：HTTP 起不来进程就没有存在意义，如实退出让上层重启。
  log('error', 'http server error; exiting', { error: String(error && error.message ? error.message : error) })
  process.exit(1)
})
