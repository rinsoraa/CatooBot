'use strict'
/**
 * ActionRuntime 单元测试（Phase 3B）：生命周期 / 单前台互斥 / 取消 / 超时 /
 * 安全停止 / 事件 / action_id —— 用测试私有的假动作注册表，不碰 Minecraft。
 *
 * 运行：node minecraft_runtime/test/action_runtime.test.js
 */

const path = require('path')

const { createActionRuntime, ActionError } = require(path.join(__dirname, '..', 'action_runtime'))

// ---------------------------------------------------------------- test harness

let failures = 0
let checks = 0

function assert(condition, message) {
  checks += 1
  if (!condition) {
    failures += 1
    console.error(`  ✗ ${message}`)
  } else {
    console.log(`  ✓ ${message}`)
  }
}

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

function makeHarness() {
  const events = []
  const logs = []
  const state = { online: true, bot: { clearControlStates: () => (state.cleared += 1) }, cleared: 0 }
  const gates = {}
  const actions = {
    quick: {
      exclusive: true,
      timeout_ms: 1000,
      async run() {
        return 'ok'
      },
    },
    slow: {
      exclusive: true,
      timeout_ms: 1000,
      async run() {
        gates.slow = gates.slow || deferred()
        await gates.slow.promise
      },
    },
    hang: {
      exclusive: true,
      timeout_ms: 60,
      cleaned: 0,
      async run() {
        await new Promise(() => {}) // 永不结束 → 必然超时
      },
      cleanup() {
        this.cleaned += 1
      },
    },
    say: {
      exclusive: false, // 非互斥通信动作（chat 形态）
      timeout_ms: 1000,
      offline_code: 'chat.not_online', // 与生产 chat 一致：保持 Phase 1 错误码
      validate(params) {
        if (!String(params.message || '').trim()) throw new ActionError('empty', 'chat.empty', 400)
        return { message: String(params.message) }
      },
      async run() {
        return 'said'
      },
    },
    boom: {
      exclusive: true,
      timeout_ms: 1000,
      async run() {
        throw new Error('kaboom')
      },
    },
    slow_cleanup: {
      exclusive: true,
      timeout_ms: 1000,
      cleanedCount: 0,
      async run() {
        gates.slowCleanup = gates.slowCleanup || deferred()
        await gates.slowCleanup.promise
      },
      cleanup() {
        this.cleanedCount += 1
      },
    },
    race_cleanup: {
      exclusive: true,
      timeout_ms: 1000,
      cleanedCount: 0,
      async run() {
        gates.race = gates.race || deferred()
        await gates.race.promise
      },
      cleanup() {
        this.cleanedCount += 1
      },
    },
    boom_cleanup: {
      exclusive: true,
      timeout_ms: 1000,
      cleanedCount: 0,
      async run() {
        gates.boomCleanup = gates.boomCleanup || deferred()
        await gates.boomCleanup.promise
      },
      cleanup() {
        this.cleanedCount += 1
        throw new Error('cleanup boom')
      },
    },
    }
  const runtime = createActionRuntime({
    registry: actions,
    getBot: () => state.bot,
    isOnline: () => state.online,
    emit: (event, data) => events.push({ event, data }),
    log: (message) => logs.push(message),
    now: () => Date.now(),
  })
  return { runtime, events, logs, state, gates, actions }
}

function eventsOf(events, name) {
  return events.filter((item) => item.event === name)
}

// ---------------------------------------------------------------------- tests

async function main() {
  setTimeout(() => {
    console.error('[action-test] GLOBAL TIMEOUT')
    process.exit(1)
  }, 30000).unref()
  // 动作的超时计时器是 unref 的（生产里由 HTTP 服务器维持事件循环）；单测里
  // 必须自己拿住循环，否则「永不结束」的动作会让进程在超时前静默退出。
  const keepAlive = setInterval(() => {}, 1000)

  console.log('[action-test] completed + action_id + 事件顺序')
  {
    const { runtime, events, logs } = makeHarness()
    const result = await runtime.execute('quick', {})
    assert(result.status === 'SUCCEEDED', `quick → SUCCEEDED（得到 ${result.status}）`)
    assert(typeof result.action_id === 'string' && result.action_id.startsWith('act_'), 'action_id 由 runtime 生成')
    const started = eventsOf(events, 'minecraft.action.started')
    const completed = eventsOf(events, 'minecraft.action.completed')
    assert(started.length === 1 && started[0].data.action_id === result.action_id, 'started 事件带同一 action_id')
    assert(completed.length === 1 && completed[0].data.action === 'quick', 'completed 事件带动作名')
    assert(completed[0].data.status === 'SUCCEEDED', 'completed 事件状态正确')
    assert(typeof completed[0].data.elapsed_ms === 'number', 'completed 事件带 elapsed_ms')
    assert(
      logs.some((line) => line.includes('started action=quick')) &&
        logs.some((line) => line.includes('completed action=quick')),
      'started/completed 都有日志',
    )
    assert(runtime.snapshot().active_count === 0, '结束后无进行中动作（无僵尸）')
    assert(runtime.currentView().status === 'SUCCEEDED', 'currentView 保留最近终态')
  }

  console.log('[action-test] action_id 唯一')
  {
    const { runtime } = makeHarness()
    const first = await runtime.execute('quick', {})
    const second = await runtime.execute('quick', {})
    assert(first.action_id !== second.action_id, '两次执行 id 不同')
  }

  console.log('[action-test] 单前台互斥：第二个 exclusive 被拒（action.busy）')
  {
    const { runtime, gates } = makeHarness()
    const running = runtime.execute('slow', {})
    await sleep(10) // 让 slow 进入 RUNNING
    let busy = null
    try {
      await runtime.execute('quick', {})
    } catch (error) {
      busy = error
    }
    assert(busy instanceof ActionError && busy.code === 'action.busy', 'exclusive 冲突 → action.busy')
    assert(busy.status === 409, 'action.busy 的 HTTP 状态是 409')
    assert(runtime.snapshot().active_count === 1, '被拒的动作没有占用槽位')
    gates.slow.resolve()
    const result = await running
    assert(result.status === 'SUCCEEDED', '原动作不受影响地跑完')
  }

  console.log('[action-test] chat 形态（非互斥）可与 exclusive 并存')
  {
    const { runtime, gates } = makeHarness()
    const running = runtime.execute('slow', {})
    await sleep(10)
    const said = await runtime.execute('say', { message: '你好' })
    assert(said.status === 'SUCCEEDED', '非互斥动作在 exclusive 运行时也能执行')
    gates.slow.resolve()
    await running
  }

  console.log('[action-test] 校验失败不进入生命周期')
  {
    const { runtime, events } = makeHarness()
    let invalid = null
    try {
      await runtime.execute('say', { message: '   ' })
    } catch (error) {
      invalid = error
    }
    assert(invalid instanceof ActionError && invalid.code === 'chat.empty', '校验错误保持 Phase 1 错误码')
    assert(events.length === 0, '校验失败不产生 action 事件')
    let unknown = null
    try {
      await runtime.execute('teleport', {})
    } catch (error) {
      unknown = error
    }
    assert(unknown instanceof ActionError && unknown.code === 'action.unknown', '未知动作 → action.unknown')
    assert(unknown.status === 404, 'action.unknown 的 HTTP 状态是 404')
  }

  console.log('[action-test] 离线拒绝（action.not_online / chat.not_online 兼容）')
  {
    const { runtime, state } = makeHarness()
    state.online = false
    let offline = null
    try {
      await runtime.execute('quick', {})
    } catch (error) {
      offline = error
    }
    assert(offline instanceof ActionError && offline.code === 'action.not_online', '离线 → action.not_online')
    let chatOffline = null
    try {
      await runtime.execute('say', { message: 'hi' })
    } catch (error) {
      chatOffline = error
    }
    assert(
      chatOffline instanceof ActionError && chatOffline.code === 'chat.not_online',
      'chat 保持 Phase 1 离线错误码 chat.not_online',
    )
  }

  console.log('[action-test] stop：取消运行中动作 + 清控制位 + 幂等')
  {
    const { runtime, gates, state, events } = makeHarness()
    const running = runtime.execute('slow', {})
    await sleep(10)
    const stopResult = runtime.stop()
    assert(stopResult.status === 'IDLE', 'stop 返回 IDLE')
    assert(stopResult.cancelled.length === 1, 'stop 返回被取消的 action_id 列表')
    assert(state.cleared === 1, 'stop 清空移动控制位（clearControlStates）')
    const cancelled = eventsOf(events, 'minecraft.action.cancelled')
    assert(cancelled.length === 1 && cancelled[0].data.reason === 'stop', '取消事件带 reason=stop')
    assert(runtime.snapshot().active_count === 0, '取消后无进行中动作')
    const again = runtime.stop()
    assert(again.cancelled.length === 0, '再次 stop 是幂等空操作')
    const result = await running
    assert(result.status === 'CANCELLED', '被取消的动作以 CANCELLED 收尾（不重复发事件）')
    assert(eventsOf(events, 'minecraft.action.cancelled').length === 1, '取消事件不重复')
  }

  console.log('[action-test] 超时 → TIMEOUT + cleanup + 不卡死')
  {
    const { runtime, events, actions } = makeHarness()
    const result = await runtime.execute('hang', {})
    assert(result.status === 'TIMEOUT', `超时动作 → TIMEOUT（得到 ${result.status}）`)
    assert(actions.hang.cleaned === 1, 'TIMEOUT 执行动作 cleanup')
    const timeouts = eventsOf(events, 'minecraft.action.timeout')
    assert(timeouts.length === 1 && timeouts[0].data.reason === 'timeout', 'timeout 事件带 reason=timeout')
    assert(runtime.snapshot().active_count === 0, '超时后动作已出清')
    const after = await runtime.execute('quick', {})
    assert(after.status === 'SUCCEEDED', '超时后 runtime 仍可正常执行新动作（不坏状态）')
  }

  console.log('[action-test] 动作抛错 → FAILED + 事件 + ActionError(action.failed)')
  {
    const { runtime, events } = makeHarness()
    let failure = null
    try {
      await runtime.execute('boom', {})
    } catch (error) {
      failure = error
    }
    assert(failure instanceof ActionError && failure.code === 'action.failed', '动作失败 → action.failed')
    assert(failure.status === 500, 'action.failed 的 HTTP 状态是 500')
    const failed = eventsOf(events, 'minecraft.action.failed')
    assert(failed.length === 1 && failed[0].data.error === 'kaboom', 'failed 事件带错误信息（无堆栈）')
    assert(!JSON.stringify(failed[0].data).includes('at '), '事件里不带内部异常堆栈')
    assert(runtime.snapshot().active_count === 0, '失败后无僵尸动作')
  }

  console.log('[action-test] cancelAll（断开）：进行中动作全部 CANCELLED')
  {
    const { runtime, gates, events } = makeHarness()
    const running = runtime.execute('slow', {})
    await sleep(10)
    const cancelled = runtime.cancelAll('disconnect')
    assert(cancelled.length === 1, 'cancelAll 返回被取消列表')
    const result = await running
    assert(result.status === 'CANCELLED', '断开时动作以 CANCELLED 收尾')
    assert(
      eventsOf(events, 'minecraft.action.cancelled')[0].data.reason === 'disconnect',
      '取消原因带 disconnect',
    )
    assert(runtime.snapshot().active_count === 0, '断开后零残留')
  }

  console.log('[action-test] 初始视图是 IDLE')
  {
    const { runtime } = makeHarness()
    const view = runtime.currentView()
    assert(view.status === 'IDLE' && view.action === null && view.action_id === null, '从未有动作 → IDLE')
    assert(runtime.snapshot().foreground === false, 'foreground 为空')
  }

  console.log('[action-test] stop 取消 → 执行 cleanup（3B.1）')
  {
    const { runtime, actions, gates } = makeHarness()
    const running = runtime.execute('slow_cleanup', {})
    await sleep(10)
    const stopResult = runtime.stop()
    assert(stopResult.cancelled.length === 1, 'stop 返回被取消的 action_id')
    assert(actions.slow_cleanup.cleanedCount === 1, 'stop 时同步执行 cleanup（真正终止底层状态）')
    const result = await running
    assert(result.status === 'CANCELLED', `终态是 CANCELLED（得到 ${result.status}）`)
    assert(actions.slow_cleanup.cleanedCount === 1, 'cleanup 不重复执行')
    gates.slowCleanup.resolve()
  }

  console.log('[action-test] disconnect 取消 → 执行 cleanup（3B.1）')
  {
    const { runtime, actions, gates } = makeHarness()
    const running = runtime.execute('slow_cleanup', {})
    await sleep(10)
    const cancelled = runtime.cancelAll('disconnect')
    assert(cancelled.length === 1, 'cancelAll 返回被取消列表')
    assert(actions.slow_cleanup.cleanedCount === 1, 'disconnect 取消也执行 cleanup')
    const result = await running
    assert(result.status === 'CANCELLED', '断开后动作以 CANCELLED 收尾')
    gates.slowCleanup.resolve()
  }

  console.log('[action-test] shutdown 取消 → 执行 cleanup（3B.1）')
  {
    const { runtime, actions, gates } = makeHarness()
    const running = runtime.execute('slow_cleanup', {})
    await sleep(10)
    runtime.cancelAll('shutdown')
    assert(actions.slow_cleanup.cleanedCount === 1, 'shutdown 取消也执行 cleanup')
    const result = await running
    assert(result.status === 'CANCELLED', '收尾后动作以 CANCELLED 结束')
    gates.slowCleanup.resolve()
  }

  console.log('[action-test] cleanup 抛错：不崩 runtime、不改终态、不重复（3B.1）')
  {
    const { runtime, actions, gates, events, logs } = makeHarness()
    const running = runtime.execute('boom_cleanup', {})
    await sleep(10)
    let threw = null
    try {
      runtime.stop()
    } catch (error) {
      threw = error
    }
    assert(threw === null, 'cleanup 异常不会从 stop() 冒出')
    const result = await running
    assert(result.status === 'CANCELLED', `终态仍是 CANCELLED（得到 ${result.status}）`)
    assert(actions.boom_cleanup.cleanedCount === 1, 'cleanup 被调用一次')
    assert(eventsOf(events, 'minecraft.action.cancelled').length === 1, '终态事件不重复')
    assert(eventsOf(events, 'minecraft.action.completed').length === 0, '没有伪造的完成事件')
    assert(
      logs.some((line) => line.includes('cleanup failed')),
      'cleanup 失败有日志（不静默）',
    )
    const after = await runtime.execute('quick', {})
    assert(after.status === 'SUCCEEDED', 'runtime 仍可执行后续动作')
    gates.boomCleanup.resolve()
  }

  console.log('[action-test] cleanup 恰好一次：stop→cancelAll→底层完成竞态（3B.1）')
  {
    const { runtime, actions, gates, events } = makeHarness()
    const running = runtime.execute('slow_cleanup', {})
    await sleep(10)
    runtime.stop()
    runtime.cancelAll('disconnect')
    runtime.cancelAll('shutdown')
    gates.slowCleanup.resolve() // 底层恰好此刻完成
    const result = await running
    assert(result.status === 'CANCELLED', `最终仍只能是 CANCELLED（得到 ${result.status}）`)
    assert(actions.slow_cleanup.cleanedCount === 1, 'cleanup 恰好一次')
    assert(eventsOf(events, 'minecraft.action.cancelled').length === 1, '终态事件恰好一次')
    assert(eventsOf(events, 'minecraft.action.completed').length === 0, '绝不出现双终态')
  }

  console.log('[action-test] race：底层 promise 恰好在 stop 时完成 → 只能 CANCELLED（3B.1 §六）')
  {
    const { runtime, actions, gates, events } = makeHarness()
    const racing = runtime.execute('race_cleanup', {})
    await sleep(10)
    gates.race.resolve() // 底层 promise 现在完成（结算微任务已排队）
    const stopResult = runtime.stop() // 同一同步块里 stop 抢先落定
    assert(stopResult.cancelled.length === 1, 'stop 认为动作仍在运行并取消它')
    const result = await racing
    assert(result.status === 'CANCELLED', `race 后终态只能是 CANCELLED（得到 ${result.status}）`)
    assert(actions.race_cleanup.cleanedCount === 1, 'race 下 cleanup 恰好一次')
    assert(eventsOf(events, 'minecraft.action.cancelled').length === 1, '只有取消事件')
    assert(eventsOf(events, 'minecraft.action.completed').length === 0, '没有完成事件（无双终态）')
  }

  clearInterval(keepAlive)
  console.log(`\n[action-test] ${checks - failures}/${checks} checks passed`)
  if (failures > 0) {
    console.error(`[action-test] FAILED: ${failures} check(s)`)
    process.exit(1)
  }
  console.log('[action-test] ALL CHECKS PASSED')
}

main().catch((error) => {
  console.error('[action-test] fatal:', error)
  process.exit(1)
})
