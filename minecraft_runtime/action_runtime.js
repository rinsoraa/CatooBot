'use strict'
/**
 * ActionRuntime（Phase 3B · Safe Action Layer）。
 *
 * 统一的 Minecraft 动作基础设施：未来 move_to / follow / dig / place / craft
 * 全部复用本模块的生命周期（请求模型 / action_id 生成 / 单一前台动作 /
 * 超时 / 取消 / 安全停止 / 状态回报 / 事件），本阶段只注册 look_at / chat / stop。
 *
 * 设计约束（任务书 §一/§八/§九/§十）：
 *   - 同一时间最多一个 foreground（exclusive）动作；再提交 exclusive 动作 →
 *     拒绝并返回 action.busy（不排队、不覆盖）；
 *   - chat 是唯一非互斥通信动作（可与其他动作并存）；
 *   - stop 是控制面动作：不占前台、不产生自己的动作记录，幂等、无 bot 也安全，
 *     取消所有进行中的动作并清空移动控制位（clearControlStates 只在这里用，
 *     绝不作为 API 暴露给 CatooBot）；
 *   - 所有动作带超时；超时 → TIMEOUT + 动作自身 cleanup（不得让 runtime 坏掉）。
 *
 * Action Cleanup 语义（Phase 3B.1 · Cancellation Cleanup Integrity）：
 *   - cleanup() = 动作被**强制终止**后，清理它已经施加到 runtime/bot 的底层状态；
 *   - TIMEOUT / CANCELLED(stop) / CANCELLED(disconnect) / CANCELLED(shutdown)
 *     一律执行 cleanup；SUCCEEDED / FAILED 不强制执行（除非动作自己定义特殊需求）；
 *   - cleanup 至多执行一次；异常被吞掉并记日志——不改终态、不崩 runtime；
 *   - **runCancellable 只是让 runtime 不再等待该 promise，它不会取消底层操作**：
 *     未来 move_to/follow（Pathfinder Goal）、dig/place 等有副作用的动作，
 *     真正的终止必须由自己的 cleanup() 实现（stop() 会先 cleanup 再报 CANCELLED）。
 *
 * 持续型动作（Phase 3D，``detached: true``）：follow_player 这类动作正常不会
 * 自行结束，不能阻塞 HTTP 调用方。注册表用 ``start``/``wait`` 两阶段替代 ``run``：
 *   - ``start(bot, params, token)``：启动阶段（解析目标、建立 Goal）——抛错仍然
 *     同步反馈给调用方（如 player.not_found → 404）；
 *   - 启动成功 → execute 立刻返回 ``status: RUNNING``，**终态（STOP/超时/目标丢失…）
 *     由 action 事件异步送达**；
 *   - ``wait(bot, params, token, state)``：后台生命周期（state 来自 start 的返回值），
 *     resolve = 自行收尾（罕见），reject = 失败原因（ActionCancelled 走取消语义）。
 *
 * 本模块不依赖任何 Minecraft 对象：bot 通过 getBot() 注入，动作由调用方注册。
 */

const STATES = Object.freeze({
  IDLE: 'IDLE',
  QUEUED: 'QUEUED',
  RUNNING: 'RUNNING',
  SUCCEEDED: 'SUCCEEDED',
  FAILED: 'FAILED',
  CANCELLED: 'CANCELLED',
  TIMEOUT: 'TIMEOUT',
})

//: 终态 → CatooBot 事件名
const TERMINAL_EVENTS = Object.freeze({
  SUCCEEDED: 'minecraft.action.completed',
  FAILED: 'minecraft.action.failed',
  CANCELLED: 'minecraft.action.cancelled',
  TIMEOUT: 'minecraft.action.timeout',
})

//: 终态 → 日志动词（任务书 §十六示例：started / completed / failed / cancelled / timeout）
const LOG_VERBS = Object.freeze({
  SUCCEEDED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
  TIMEOUT: 'timeout',
})

class ActionError extends Error {
  constructor(message, code, status = 400, detail = null) {
    super(message)
    this.name = 'ActionError'
    this.code = code
    this.status = status
    this.detail = detail
  }
}

/** 动作被取消（stop / disconnect / timeout）：由 runCancellable 抛出，执行器分类。 */
class ActionCancelled extends Error {
  constructor(reason) {
    super(`action cancelled: ${reason}`)
    this.name = 'ActionCancelled'
    this.reason = reason
  }
}

function createActionRuntime({ registry, getBot, isOnline, emit, log, now = () => Date.now() }) {
  const active = new Map() // action_id -> controller {record, token, def}
  let foregroundId = null // 当前占用前台（exclusive）的动作 id
  let last = null // 最近一次结束的动作视图
  let seq = 0

  function nextActionId() {
    seq += 1
    return `act_${now().toString(36)}_${seq.toString(36)}`
  }

  function makeToken() {
    const token = { cancelled: false, reason: '', timer: null, onCancel: null }
    token.cancel = (reason) => {
      if (token.cancelled) return false
      token.cancelled = true
      token.reason = reason
      if (token.onCancel) token.onCancel(reason)
      return true
    }
    return token
  }

  /** 把动作 promise 包成可取消：取消后立刻以 ActionCancelled 结束等待。
   *
   * 注意：它**只停止等待**，不会取消底层 promise（Phase 3B.1 §四）。
   * 有副作用的动作必须自己实现 cleanup() 来真正终止底层状态。
   */
  function runCancellable(promise, token) {
    if (token.cancelled) return Promise.reject(new ActionCancelled(token.reason))
    return new Promise((resolve, reject) => {
      let settled = false
      token.onCancel = (reason) => {
        if (!settled) {
          settled = true
          reject(new ActionCancelled(reason))
        }
      }
      Promise.resolve(promise).then(
        (value) => {
          if (!settled) {
            settled = true
            resolve(value)
          }
        },
        (error) => {
          if (!settled) {
            settled = true
            reject(error)
          }
        },
      )
    })
  }

  /** 取消原因 → 终态：timeout → TIMEOUT，其余（stop/disconnect/shutdown）→ CANCELLED。 */
  function cancellationStatus(reason) {
    return reason === 'timeout' ? STATES.TIMEOUT : STATES.CANCELLED
  }

  /** 强制终止后的底层状态清理：至多一次、异常吞掉并记日志（Phase 3B.1 §二/§三）。
   *
   * cleanup 失败**不得**改变终态、不得崩 runtime——终态只由 finish() 负责落定。
   */
  function runCleanup(controller) {
    const def = controller.def
    if (typeof def.cleanup !== 'function') return
    if (controller.cleaned) return
    controller.cleaned = true
    try {
      def.cleanup(getBot())
    } catch (cleanupError) {
      log(
        `[Minecraft Action] cleanup failed action=${controller.record.action} id=${controller.record.action_id} error=${cleanupError.message}`,
      )
    }
  }

  function view(controller) {
    const record = controller.record
    const elapsed =
      record._startedMs !== null
        ? record.finished_at !== null
          ? Math.round(record._elapsedMs ?? now() - record._startedMs)
          : Math.round(now() - record._startedMs)
        : null
    return {
      action: record.action,
      action_id: record.action_id,
      status: record.status,
      started_at: record.started_at,
      finished_at: record.finished_at,
      elapsed_ms: elapsed,
    }
  }

  /** 终态落定：写状态、清 timer、退前台、发事件与日志（幂等）。 */
  function finish(controller, status, extra = {}) {
    const record = controller.record
    if (record.status !== STATES.RUNNING && record.status !== STATES.QUEUED) return false
    record.status = status
    record.finished_at = now() / 1000
    record._elapsedMs = record._startedMs !== null ? now() - record._startedMs : null
    if (controller.token.timer !== null) {
      clearTimeout(controller.token.timer)
      controller.token.timer = null
    }
    active.delete(record.action_id)
    if (foregroundId === record.action_id) foregroundId = null
    last = view(controller)
    const suffix = [
      record._elapsedMs !== null ? `elapsed=${record._elapsedMs}ms` : '',
      extra.reason ? `reason=${extra.reason}` : '',
      extra.error ? `error=${extra.error}` : '',
    ]
      .filter(Boolean)
      .join(' ')
    log(
      `[Minecraft Action] ${LOG_VERBS[status] ?? status.toLowerCase()} action=${record.action} id=${record.action_id}${suffix ? ' ' + suffix : ''}`,
    )
    emit(TERMINAL_EVENTS[status], {
      action: record.action,
      action_id: record.action_id,
      status,
      started_at: record.started_at,
      finished_at: record.finished_at,
      elapsed_ms: record._elapsedMs,
      ...extra,
    })
    return true
  }

  /**
   * 执行一个动作。未知/校验失败/离线/忙 → 抛 ActionError（带 status）。
   * 成功与 TIMEOUT/CANCELLED 返回 {action_id, action, status}；
   * 运行时失败（动作自身抛错）→ 记录 FAILED 后抛 ActionError('action.failed')。
   */
  async function execute(name, params) {
    const def = registry[name]
    if (!def) throw new ActionError(`未知动作：${name}`, 'action.unknown', 404)
    // 控制面动作（stop）：先于在线校验——无 bot 也安全、幂等
    if (def.control) return stop()

    if (!isOnline()) {
      throw new ActionError('罐头不在世界里，无法执行动作', def.offline_code || 'action.not_online', 400)
    }
    const validated = def.validate ? def.validate(params || {}) : {}

    if (def.exclusive && foregroundId !== null) {
      const busy = active.get(foregroundId)
      throw new ActionError(
        `已有动作在执行（${busy ? busy.record.action : 'unknown'}）`,
        'action.busy',
        409,
        busy ? { action_id: busy.record.action_id, action: busy.record.action } : null,
      )
    }

    const record = {
      action: name,
      action_id: nextActionId(),
      status: STATES.QUEUED,
      started_at: null,
      finished_at: null,
      _startedMs: null,
      _elapsedMs: null,
    }
    const controller = { record, token: makeToken(), def, cleaned: false }
    active.set(record.action_id, controller)
    if (def.exclusive) foregroundId = record.action_id

    record.status = STATES.RUNNING
    record.started_at = now() / 1000
    record._startedMs = now()
    log(`[Minecraft Action] started action=${name} id=${record.action_id}`)
    emit('minecraft.action.started', {
      action: name,
      action_id: record.action_id,
      status: STATES.RUNNING,
      started_at: record.started_at,
      finished_at: null,
      elapsed_ms: 0,
      ...(validated.message !== undefined ? { message: validated.message } : {}),
    })
    if (def.timeout_ms) {
      controller.token.timer = setTimeout(() => controller.token.cancel('timeout'), def.timeout_ms)
      if (typeof controller.token.timer.unref === 'function') controller.token.timer.unref()
    }

    try {
      if (def.detached) {
        // 持续型动作：启动阶段同步语义（失败仍反馈给调用方），启动成功即返回 RUNNING；
        // 终态在后台按同一套规则落定并发事件（不阻塞调用方）。
        const followState = await def.start(getBot(), validated, controller.token)
        const lifecycle = Promise.resolve(
          def.wait(getBot(), validated, controller.token, followState),
        )
        void runCancellable(lifecycle, controller.token).then(
          () => finish(controller, STATES.SUCCEEDED, {}), // 持续动作自行收尾（仅防御）
          (error) => {
            if (error instanceof ActionCancelled) {
              const status = cancellationStatus(error.reason)
              runCleanup(controller)
              finish(controller, status, { reason: error.reason })
              return
            }
            const message = String(error && error.message ? error.message : error)
            finish(controller, STATES.FAILED, {
              error: message,
              ...(error instanceof ActionError ? { code: error.code } : {}),
            })
          },
        )
        return { action_id: record.action_id, action: name, status: STATES.RUNNING }
      }
      const value = await runCancellable(def.run(getBot(), validated, controller.token), controller.token)
      // race 防线（Phase 3B.1 §六）：stop/cancelAll/timeout 与底层完成同时到达时，
      // 终态只能是 CANCELLED/TIMEOUT —— 绝不出现「记录 CANCELLED、响应 SUCCEEDED」双终态。
      // （正常微任务顺序下由 token.onCancel 先手 reject；这里是任何交错下的兜底。）
      if (controller.token.cancelled || record.status !== STATES.RUNNING) {
        const status = cancellationStatus(controller.token.reason)
        runCleanup(controller)
        finish(controller, status, { reason: controller.token.reason || 'cancelled' })
        return { action_id: record.action_id, action: name, status }
      }
      const extra = {}
      if (validated.message !== undefined) extra.message = validated.message
      // 状态回报：动作完成瞬间的读数（如 look_at 的实际朝向、move_to 的到达坐标）。
      // 服务器可能在稍后回写状态（flying-squid 会发 forcedMove），事件/响应里的
      // result 才是动作的真实结果。
      if (value && typeof value === 'object') extra.result = value
      finish(controller, STATES.SUCCEEDED, extra)
      return {
        action_id: record.action_id,
        action: name,
        status: STATES.SUCCEEDED,
        ...(extra.result !== undefined ? { result: extra.result } : {}),
      }
    } catch (error) {
      if (error instanceof ActionCancelled) {
        const status = cancellationStatus(error.reason)
        // TIMEOUT 与 CANCELLED（stop / disconnect / shutdown）都必须清理底层状态：
        // runCancellable 只是不再等待，真正的终止由动作自己的 cleanup() 负责（§四）
        runCleanup(controller)
        finish(controller, status, { reason: error.reason })
        return { action_id: record.action_id, action: name, status }
      }
      const message = String(error && error.message ? error.message : error)
      finish(controller, STATES.FAILED, {
        error: message,
        ...(error instanceof ActionError ? { code: error.code } : {}),
      })
      // 动作自带稳定错误码（如 move_to 的 path.not_found）：记录 FAILED 后原样抛出，
      // 交给 HTTP 层映射（绝不把 Pathfinder 内部错误对象原样透传）
      if (error instanceof ActionError) throw error
      throw new ActionError(`动作执行失败：${message}`, 'action.failed', 500, {
        action_id: record.action_id,
        action: name,
        status: STATES.FAILED,
      })
    }
  }

  /**
   * 安全停止（最高优先级，任务书 §六/§九）：
   *   逐个取消进行中动作 → 动作自己的 cleanup()（真正终止底层状态）→
   *   清空移动控制位 → 回到 IDLE。
   * 幂等；没有动作/没有 bot 也返回成功，并给出被取消的 action_id 列表。
   * cleanup 与 clearControlStates 是**两件事、都保留**（Phase 3B.1 §七）：
   * cleanup 负责动作私有状态（未来的 Pathfinder Goal 等），clearControlStates
   * 是 runtime 层的全局移动兜底。
   */
  function stop() {
    const cancelled = []
    for (const controller of [...active.values()]) {
      const status = controller.record.status
      if (status !== STATES.RUNNING && status !== STATES.QUEUED) continue
      controller.token.cancel('stop')
      // 先真终止（cleanup），再报 CANCELLED：绝不允许「ActionRuntime=CANCELLED 而
      // Minecraft Bot 继续移动」的状态（Phase 3B.1 §一）
      runCleanup(controller)
      if (finish(controller, STATES.CANCELLED, { reason: 'stop' })) {
        cancelled.push(controller.record.action_id)
      }
    }
    const bot = getBot()
    if (bot && typeof bot.clearControlStates === 'function') {
      try {
        bot.clearControlStates()
      } catch (error) {
        log(`[Minecraft Action] clearControlStates failed error=${error.message}`)
      }
    }
    log(`[Minecraft Action] stop cancelled=${cancelled.length}`)
    return { status: STATES.IDLE, cancelled }
  }

  /** bot 离开/进程收尾：把进行中的动作全部按 CANCELLED 结束（reason=disconnect 等）。 */
  function cancelAll(reason) {
    const cancelled = []
    for (const controller of [...active.values()]) {
      const status = controller.record.status
      if (status !== STATES.RUNNING && status !== STATES.QUEUED) continue
      controller.token.cancel(reason)
      runCleanup(controller) // 断开/收尾同样必须清理动作已经施加的底层状态
      if (finish(controller, STATES.CANCELLED, { reason })) {
        cancelled.push(controller.record.action_id)
      }
    }
    if (cancelled.length > 0) {
      log(`[Minecraft Action] cancelAll reason=${reason} count=${cancelled.length}`)
    }
    return cancelled
  }

  /** 当前动作视图（前台优先，否则最近一次终态；从未有过动作 → IDLE）。 */
  function currentView() {
    if (foregroundId !== null) {
      const controller = active.get(foregroundId)
      if (controller) return view(controller)
    }
    if (last !== null) return { ...last }
    return {
      action: null,
      action_id: null,
      status: STATES.IDLE,
      started_at: null,
      finished_at: null,
      elapsed_ms: null,
    }
  }

  function snapshot() {
    return { current: currentView(), active_count: active.size, foreground: foregroundId !== null }
  }

  return { execute, stop, cancelAll, currentView, snapshot, states: STATES }
}

module.exports = { createActionRuntime, ActionError, ActionCancelled, STATES }
