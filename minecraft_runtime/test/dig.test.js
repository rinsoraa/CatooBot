/**
 * dig 注册与安全配置测试（Phase 4B）+ Phase 4I 工具感知（optional expected_tool）。
 *
 * Phase 4B 部分：注册表属性 / 参数校验 / 执行前校验（start 阶段）/ 结果复核（wait 阶段）/ cleanup。
 * Phase 4I 部分（K–T）：expected_tool 缺省 = 4B 行为；给了就核对**当前主手**
 *   （held.item_changed / held.item_missing）、结果带工具身份、canDigBlock 仍是最终硬门、
 *   **背包里有工具也绝不自动 equip**、ActionRuntime 终态恰好一次 + 取消 cleanup 不变。
 *
 * 这里不连真实服务器：start/wait 的校验全用假 bot 直测（与 move_to.test.js 同一手法），
 * 真正的挖掘行为由 e2e.js（flying-squid）与真实服务器 smoke 负责。
 *
 * 运行：node minecraft_runtime/test/dig.test.js
 */
'use strict'

const { Vec3 } = require('vec3')

const { ACTION_REGISTRY, DIG_DEFAULTS, actionRuntime } = require('../runtime.js')

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

function makeBot({
  block = null,
  position = new Vec3(0, 64, 0),
  canDig = true,
  heldItem = null,
  blockAfterDig = null,
} = {}) {
  const bot = {
    entity: { position },
    // Phase 4I：主手物品（可选）——给了才会被 expected_tool 校验
    heldItem,
    // 自动换工具类 API 的哨兵：任何一个被调用都说明"偷偷换工具了"（§二/§二十七）
    equipCalls: [],
    blockAt: () => block,
    canDigBlock: () => canDig,
    dig: async () => {
      bot.digCalls += 1
      if (blockAfterDig !== null) bot.blockAt = () => blockAfterDig
    },
    digCalls: 0,
    stopDigging() {
      bot.stopCalls += 1
    },
    stopCalls: 0,
    clearControlStates() {
      bot.clearCalls += 1
    },
    clearCalls: 0,
    equip(item) {
      bot.equipCalls.push(item)
    },
    setQuickBarSlot(slot) {
      bot.equipCalls.push(`slot:${slot}`)
    },
    unequip() {
      bot.equipCalls.push('unequip')
    },
  }
  return bot
}

function makeTool(name, count = 1) {
  return { name, count, type: 1, slot: 36 }
}

function makeBlock(name, x, y, z) {
  return { name, position: new Vec3(x, y, z), diggable: true }
}

async function expectCode(promise, code, label) {
  try {
    await promise
  } catch (error) {
    assert(
      error && error.code === code,
      `${label} → ${code}（得到 ${error && (error.code || error.message)}）`,
    )
    return
  }
  assert(false, `${label} → 期望 ${code}，但成功返回了`)
}

// 目标离 bot（0,64,0，眼睛 +1.65）约 3.2 格：在 max_dig_distance(5) 之内
const PARAMS = { x: 2, y: 64, z: 2, expected_block: 'minecraft:stone' }

async function main() {
  console.log('[dig-test] dig 注册表属性')
  {
    const def = ACTION_REGISTRY.dig
    assert(Boolean(def), 'dig 已注册')
    assert(def.exclusive === true, 'exclusive = true（不能边挖边走）')
    assert(def.risk === 'MEDIUM', `risk = MEDIUM（得到 ${def.risk}）`)
    assert(def.timeout_ms === 30000, `默认 timeout = 30s（得到 ${def.timeout_ms}）`)
    assert(def.detached === true, 'detached = true（启动即 RUNNING，终态经事件）')
    assert(typeof def.validate === 'function', '有参数校验')
    assert(typeof def.start === 'function' && typeof def.wait === 'function', '有 start/wait')
    assert(typeof def.run !== 'function', '没有阻塞式 run')
    assert(typeof def.cleanup === 'function', '有 cleanup（stopDigging + 清控制位）')
    assert(
      Object.keys(ACTION_REGISTRY).sort().join(',') ===
        'chat,container_inspect,container_transfer,craft,dig,dropped_items,equip,follow_player,inventory_move,look_at,move_to,pickup_item,place,recipe_lookup,stop',
      `注册表只有已批准动作（得到 ${Object.keys(ACTION_REGISTRY).sort().join(',')}）`,
    )
    // §三 禁止清单：连续挖掘/范围破坏/批量放置类动作一个都不许有
    // （place 从 Phase 4C 起是正式的单方块动作，所以不在禁止清单里；
    //  place_multiple / build / bridge / schematic 这些批量能力仍然必须不存在）
    for (const forbidden of [
      'mine',
      'mine_ore',
      'collect',
      'gather',
      'strip_mine',
      'break_area',
      'dig_multiple',
      'auto_mine',
      'place_multiple',
      'attack',
      // Phase 4F 起 craft 是正式动作（单配方、需确认）→ 这里禁的是批量/链式/自动合成
      'craft_all',
      'auto_craft',
      'craft_chain',
      'crafting_table',
      'smelt',
      'trade',
      'eat',
      'inventory',
      'container',
    ]) {
      assert(ACTION_REGISTRY[forbidden] === undefined, `未注册 ${forbidden}`)
    }
  }

  console.log('[dig-test] DIG_DEFAULTS（超时 / 距离上限）')
  {
    assert(DIG_DEFAULTS.timeoutMs === 30000, `timeout 30s（得到 ${DIG_DEFAULTS.timeoutMs}）`)
    assert(DIG_DEFAULTS.maxDistance === 5, `max_dig_distance 5（得到 ${DIG_DEFAULTS.maxDistance}）`)
  }

  console.log('[dig-test] 参数校验（坐标 + expected_block 必填）')
  {
    const def = ACTION_REGISTRY.dig
    const ok = def.validate({ x: 120, y: 64, z: -230, expected_block: 'minecraft:stone' })
    assert(
      ok.x === 120 && ok.y === 64 && ok.z === -230 && ok.expected_block === 'minecraft:stone',
      '合法参数通过并归一化',
    )

    const badCases = [
      ['缺 expected_block', { x: 1, y: 2, z: 3 }, 'block.invalid'],
      ['空 expected_block', { x: 1, y: 2, z: 3, expected_block: '   ' }, 'block.invalid'],
      ['expected_block 不是字符串', { x: 1, y: 2, z: 3, expected_block: 42 }, 'block.invalid'],
      ['expected_block 过长', { x: 1, y: 2, z: 3, expected_block: 'x'.repeat(65) }, 'block.invalid'],
      ['字符串坐标', { x: '1', y: 2, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['NaN', { x: NaN, y: 2, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['Infinity', { x: 1, y: Infinity, z: 3, expected_block: 'stone' }, 'action.invalid'],
      ['x 超世界边界', { x: 4.0e7, y: 64, z: 0, expected_block: 'stone' }, 'action.invalid'],
      ['y 低于世界下限', { x: 0, y: -600, z: 0, expected_block: 'stone' }, 'action.invalid'],
    ]
    // 坐标沿用既有世界坐标校验（action.invalid）；expected_block 是 dig 特有的（block.invalid）
    for (const [label, params, expected] of badCases) {
      let code = null
      try {
        def.validate(params)
      } catch (error) {
        code = error.code
      }
      assert(code === expected, `${label} → ${expected}（得到 ${code}）`)
    }
  }

  console.log('[dig-test] start 阶段校验（同步反馈，绝不进入挖掘）')
  {
    const def = ACTION_REGISTRY.dig

    // 目标位置没有方块 / 是空气 → block.not_found
    await expectCode(
      def.start(makeBot({ block: null }), PARAMS),
      'block.not_found',
      '方块不存在',
    )
    await expectCode(
      def.start(makeBot({ block: makeBlock('air', 3, 64, 3) }), PARAMS),
      'block.not_found',
      '空气',
    )
    await expectCode(
      def.start(makeBot({ block: makeBlock('cave_air', 3, 64, 3) }), PARAMS),
      'block.not_found',
      'cave_air',
    )

    // §十四：方块已经变了 → block.changed（带 expected/actual）
    const changed = await def
      .start(makeBot({ block: makeBlock('minecraft:dirt', 3, 64, 3) }), PARAMS)
      .then(() => null)
      .catch((error) => error)
    assert(changed && changed.code === 'block.changed', `方块变了 → block.changed（得到 ${changed && changed.code}）`)
    assert(
      changed && changed.detail && changed.detail.expected === 'minecraft:stone' &&
        changed.detail.actual === 'minecraft:dirt',
      'block.changed 带 expected/actual（结构化，不回传原始异常文本）',
    )

    // §十七：太远 → block.too_far（用真实距离算：bot 在 (0,64,0)，目标 (3,64,3)）
    const farBot = makeBot({ block: makeBlock('minecraft:stone', 30, 64, 30) })
    await expectCode(def.start(farBot, { x: 30, y: 64, z: 30, expected_block: 'minecraft:stone' }), 'block.too_far', '太远')

    // §十六：canDigBlock false → block.not_diggable（不换工具、不走近）
    await expectCode(
      def.start(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3), canDig: false }), PARAMS),
      'block.not_diggable',
      '挖不动',
    )

    // 正常：start 返回句柄（不执行挖掘）
    const state = await def.start(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3) }), PARAMS)
    assert(state && state.blockName === 'minecraft:stone' && state.position, 'start 返回 {block, position, blockName}')
  }

  console.log('[dig-test] wait 阶段：不信任 dig Promise，必须复核方块真的没了')
  {
    const def = ACTION_REGISTRY.dig
    const state = {
      block: makeBlock('minecraft:stone', 3, 64, 3),
      position: new Vec3(3, 64, 3),
      blockName: 'minecraft:stone',
    }

    // 方块仍在原位（客户端/服务器不同步）→ block.break_unconfirmed
    await expectCode(
      def.wait(makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3) }), PARAMS, { cancelled: false }, state),
      'block.break_unconfirmed',
      '方块仍在',
    )

    // 挖成功：复核读到空气 → 返回真实结果
    const result = await def.wait(
      makeBot({ block: makeBlock('air', 3, 64, 3) }),
      PARAMS,
      { cancelled: false },
      state,
    )
    assert(
      result.block_before === 'minecraft:stone' && result.block_after === 'air',
      `结果带 block_before/block_after（得到 ${JSON.stringify(result)}）`,
    )

    // 目标方块消失（blockAt → null）也算破坏成功
    const gone = await def.wait(makeBot({ block: null }), PARAMS, { cancelled: false }, state)
    assert(gone.block_after === 'air', 'blockAt 为 null 视为已破坏（block_after=air）')

    // 挖掘被中断（digging aborted）且 token 未取消 → 稳定失败码
    const abortedBot = makeBot({ block: makeBlock('air', 3, 64, 3) })
    abortedBot.dig = async () => {
      throw new Error('Digging aborted.')
    }
    await expectCode(
      def.wait(abortedBot, PARAMS, { cancelled: false }, state),
      'block.dig_aborted',
      '挖掘被中断',
    )

    // 其它异常 → action.failed（不回传原始堆栈）
    const brokenBot = makeBot({ block: makeBlock('air', 3, 64, 3) })
    brokenBot.dig = async () => {
      throw new Error('socket hang up')
    }
    await expectCode(def.wait(brokenBot, PARAMS, { cancelled: false }, state), 'action.failed', 'socket hang up')
  }

  console.log('[dig-test] cleanup：stopDigging + 清控制位')
  {
    const def = ACTION_REGISTRY.dig
    let stopped = 0
    let cleared = 0
    def.cleanup({
      stopDigging() {
        stopped += 1
      },
      clearControlStates() {
        cleared += 1
      },
    })
    assert(stopped === 1 && cleared === 1, `cleanup 调用了两者（stopDigging=${stopped}, clear=${cleared}）`)

    // 没有 bot（断开后）也不能炸；stopDigging 抛错也必须被吞掉
    def.cleanup(null)
    def.cleanup({
      stopDigging() {
        throw new Error('not connected')
      },
      clearControlStates() {},
    })
    assert(true, 'cleanup 对 null bot / 抛错的 stopDigging 都安全')
  }

  console.log('[dig-test] 4I K/L/Q：expected_tool 缺省 = 4B 行为；给了就核对主手')
  {
    const def = ACTION_REGISTRY.dig

    // K：validate 缺省 → expected_tool 归一化成 null（4B 行为不变）
    const legacy = def.validate({ ...PARAMS })
    assert(legacy.expected_tool === null, `缺省 expected_tool → null（得到 ${legacy.expected_tool}）`)
    const explicitNull = def.validate({ ...PARAMS, expected_tool: null })
    assert(explicitNull.expected_tool === null, '显式 null 等同"没要求"')
    const emptyTool = def.validate({ ...PARAMS, expected_tool: '' })
    assert(emptyTool.expected_tool === null, '空字符串等同"没要求"')

    // 没有 heldItem 的 bot（4B 的假 bot）照样能 start —— 4B 兼容
    const legacyBot = makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3) })
    const legacyState = await def.start(legacyBot, legacy)
    assert(
      legacyState.toolExpected === null && legacyState.toolActual === null,
      '没要求工具时 state 里的工具字段都是 null（不误判空手）',
    )

    // L/Q：要求 stone_pickaxe + 主手确实是它 → 通过，并把身份带进 state
    const toolBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
    })
    const toolParams = def.validate({ ...PARAMS, expected_tool: 'minecraft:stone_pickaxe' })
    const toolState = await def.start(toolBot, toolParams)
    assert(
      toolState.toolExpected === 'stone_pickaxe' && toolState.toolActual === 'stone_pickaxe',
      `规范化后两边都是裸物品名（得到 ${toolState.toolExpected} / ${toolState.toolActual}）`,
    )
    assert(toolBot.equipCalls.length === 0 && toolBot.digCalls === 0, 'start 不挖、也不换工具')

    // 简化写法同样匹配（stone_pickaxe == minecraft:stone_pickaxe）
    const bareState = await def.start(
      makeBot({ block: makeBlock('minecraft:stone', 3, 64, 3), heldItem: makeTool('stone_pickaxe') }),
      def.validate({ ...PARAMS, expected_tool: 'stone_pickaxe' }),
    )
    assert(bareState.toolActual === 'stone_pickaxe', '两种写法都被规范化成同一个身份')

    // Q：结果里带工具身份（wait 阶段）
    const digBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
      blockAfterDig: makeBlock('air', 3, 64, 3),
    })
    const result = await def.wait(digBot, toolParams, { cancelled: false }, toolState)
    assert(
      result.tool_expected === 'stone_pickaxe' && result.tool_actual === 'stone_pickaxe',
      `成功结果带 tool_expected/tool_actual（得到 ${JSON.stringify(result)}）`,
    )
    assert(
      result.tool_actual_after &&
        result.tool_actual_after.name === 'stone_pickaxe' &&
        result.tool_actual_after.count === 1,
      `结果带动作后的主手快照（得到 ${JSON.stringify(result.tool_actual_after)}）`,
    )
    assert(
      !('type' in (result.tool_actual_after || {})) && !('slot' in (result.tool_actual_after || {})),
      '结果里绝不带 raw Item object（只有 name/count）',
    )

    // 没要求工具的旧路径：结果里的工具字段是 null（不是 undefined / 不是 raw item）
    const plainResult = await def.wait(
      makeBot({ block: null, heldItem: makeTool('dirt', 3) }),
      legacy,
      { cancelled: false },
      legacyState,
    )
    assert(
      plainResult.tool_expected === null && plainResult.tool_actual === null,
      '4B 路径的结果字段是 null（向后兼容）',
    )
    assert(
      plainResult.tool_actual_after && plainResult.tool_actual_after.name === 'dirt',
      '动作后的主手仍然如实上报（即使没要求工具）',
    )
  }

  console.log('[dig-test] 4I M/N/P/R：工具身份不对就失败，而且**绝不自动换工具**')
  {
    const def = ACTION_REGISTRY.dig
    const params = def.validate({ ...PARAMS, expected_tool: 'minecraft:stone_pickaxe' })

    // M：主手是别的物品 → held.item_changed（带 expected/actual），且没有挖
    const wrongBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('dirt', 3),
    })
    const mismatch = await def.start(wrongBot, params).then(
      () => null,
      (error) => error,
    )
    assert(
      mismatch && mismatch.code === 'held.item_changed',
      `主手不是它 → held.item_changed（得到 ${mismatch && mismatch.code}）`,
    )
    assert(
      mismatch.detail &&
        mismatch.detail.expected === 'stone_pickaxe' &&
        mismatch.detail.actual === 'dirt',
      `detail 带 expected/actual（得到 ${JSON.stringify(mismatch.detail)}）`,
    )
    assert(wrongBot.digCalls === 0, '身份不对时绝不开始挖')
    assert(wrongBot.equipCalls.length === 0, '而且绝不偷偷换工具')

    // N：主手空着 → held.item_missing
    const emptyHandBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: null,
    })
    const missing = await def.start(emptyHandBot, params).then(
      () => null,
      (error) => error,
    )
    assert(
      missing && missing.code === 'held.item_missing',
      `空手 → held.item_missing（得到 ${missing && missing.code}）`,
    )
    assert(
      missing.detail && missing.detail.expected === 'stone_pickaxe' && missing.detail.actual === null,
      'detail 如实说明 expected 与 actual=null',
    )
    assert(emptyHandBot.equipCalls.length === 0, '空手也绝不自动去背包里拿')

    // R（§二十七 关键安全测试）：背包里**有**石镐也不能自动装备
    const richBot = makeBot({
      block: makeBlock('minecraft:iron_ore', 3, 64, 3),
      heldItem: makeTool('dirt', 1),
    })
    const richError = await def
      .start(richBot, def.validate({ ...PARAMS, expected_block: 'minecraft:iron_ore', expected_tool: 'minecraft:stone_pickaxe' }))
      .then(() => null, (error) => error)
    assert(
      richError && richError.code === 'held.item_changed',
      '背包里有工具也要先看**主手**（得到 ' + (richError && richError.code) + '）',
    )
    assert(
      richBot.equipCalls.length === 0,
      `没有发生任何 equip / 切槽（得到 ${JSON.stringify(richBot.equipCalls)}）`,
    )

    // P：canDigBlock=false 是"能不能挖"的最终硬门（工具身份对了也一样拒绝）
    const stubbornBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
      canDig: false,
    })
    const notDiggable = await def.start(stubbornBot, params).then(() => null, (error) => error)
    assert(
      notDiggable && notDiggable.code === 'block.not_diggable',
      `挖不动 → block.not_diggable（得到 ${notDiggable && notDiggable.code}）`,
    )
    assert(stubbornBot.equipCalls.length === 0, '挖不动也不换工具（交给用户决定）')

    // O：start 之后主手变了 —— 动作不改绑、也不重新校验，只是如实报告"结束时的主手"
    const handChangesBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
      blockAfterDig: makeBlock('air', 3, 64, 3),
    })
    const changeState = await def.start(handChangesBot, params)
    handChangesBot.heldItem = makeTool('dirt', 2)
    const changedResult = await def.wait(handChangesBot, params, { cancelled: false }, changeState)
    assert(
      changedResult.tool_actual === 'stone_pickaxe',
      '结果里的 tool_actual 是**校验通过那一刻**的身份（不随之后的手持变化而改）',
    )
    assert(
      changedResult.tool_actual_after &&
        changedResult.tool_actual_after.name === 'dirt' &&
        changedResult.tool_actual_after.count === 2,
      '动作结束后的主手单独如实报告（tool_actual_after）',
    )
  }

  console.log('[dig-test] 4I S/T：ActionRuntime 终态恰好一次 + 取消 cleanup 不变')
  {
    const { createActionRuntime } = require('../action_runtime.js')
    const counter = { cleanup: 0 }
    const definition = {
      ...ACTION_REGISTRY.dig,
      cleanup(bot, controller) {
        counter.cleanup += 1
        return ACTION_REGISTRY.dig.cleanup(bot, controller)
      },
    }
    const events = []
    const state = { bot: null, online: true }
    const runtime = createActionRuntime({
      registry: { dig: definition },
      getBot: () => state.bot,
      isOnline: () => state.online,
      emit: (event, data) => events.push({ event, data }),
      log: () => {},
      now: () => Date.now(),
    })
    const terminals = (actionId) =>
      events.filter(
        (row) =>
          [
            'minecraft.action.completed',
            'minecraft.action.failed',
            'minecraft.action.cancelled',
            'minecraft.action.timeout',
          ].includes(row.event) && row.data.action_id === actionId,
      )
    const settle = async (actionId) => {
      for (let i = 0; i < 200; i += 1) {
        const rows = terminals(actionId)
        if (rows.length > 0) {
          await new Promise((resolve) => setTimeout(resolve, 30))
          return terminals(actionId)
        }
        await new Promise((resolve) => setTimeout(resolve, 10))
      }
      return []
    }

    // 正常完成：一个终态、cleanup 不被调（SUCCEEDED 不触发 cleanup —— 既定契约）
    const okBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
      blockAfterDig: makeBlock('air', 3, 64, 3),
    })
    state.bot = okBot
    const started = await runtime.execute('dig', {
      ...PARAMS,
      expected_tool: 'minecraft:stone_pickaxe',
    })
    assert(
      started.status === 'RUNNING' && Boolean(started.action_id),
      `工具感知的 dig 启动即 RUNNING（得到 ${JSON.stringify(started)}）`,
    )
    const okTerminals = await settle(started.action_id)
    assert(
      okTerminals.length === 1 && okTerminals[0].event === 'minecraft.action.completed',
      `正常完成：恰好一个 completed（得到 ${JSON.stringify(okTerminals.map((r) => r.event))}）`,
    )
    assert(
      okTerminals[0].data.result && okTerminals[0].data.result.tool_actual === 'stone_pickaxe',
      '终态事件里带工具身份',
    )
    assert(counter.cleanup === 0, `成功不触发 cleanup（得到 ${counter.cleanup}）`)
    assert(okBot.equipCalls.length === 0, '整条路径没有任何 equip')

    // 取消：CANCELLED + cleanup 恰好一次 + 真的停止挖掘
    const slowBot = makeBot({
      block: makeBlock('minecraft:stone', 3, 64, 3),
      heldItem: makeTool('stone_pickaxe'),
    })
    slowBot.dig = () => new Promise(() => {}) // 永远挖不完
    state.bot = slowBot
    const slow = await runtime.execute('dig', {
      ...PARAMS,
      expected_tool: 'minecraft:stone_pickaxe',
    })
    await new Promise((resolve) => setTimeout(resolve, 30))
    const stop = runtime.stop()
    assert(
      stop.cancelled.includes(slow.action_id),
      `STOP 取消进行中的 dig（得到 ${JSON.stringify(stop)}）`,
    )
    const slowTerminals = await settle(slow.action_id)
    assert(
      slowTerminals.length === 1 && slowTerminals[0].event === 'minecraft.action.cancelled',
      `取消：恰好一个 cancelled（得到 ${JSON.stringify(slowTerminals.map((r) => r.event))}）`,
    )
    assert(counter.cleanup === 1, `取消路径 cleanup 恰好一次（得到 ${counter.cleanup}）`)
    assert(slowBot.stopCalls === 1, `cleanup 真的调了 stopDigging（得到 ${slowBot.stopCalls}）`)
    assert(slowBot.clearCalls >= 1, 'cleanup 也清了移动控制位')
    assert(slowBot.equipCalls.length === 0, '取消路径也没有 equip')
  }

  console.log('[dig-test] actionRuntime 仍然只有一套（未新建第二套 Action Manager）')
  {
    assert(typeof actionRuntime.execute === 'function', 'execute 在')
    assert(typeof actionRuntime.stop === 'function', 'stop 在')
    assert(typeof actionRuntime.snapshot === 'function', 'snapshot 在')
  }

  console.log(`\n[dig-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[dig-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[dig-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[dig-test] crashed:', error)
  process.exit(1)
})
