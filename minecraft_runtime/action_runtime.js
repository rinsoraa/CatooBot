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

  /** 把动作 promise 包成可取消：取消后立刻以 ActionCancelled 结束等待。 */
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
    const controller = { record, token: makeToken(), def }
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
      const value = await runCancellable(def.run(getBot(), validated, controller.token), controller.token)
      const extra = {}
      if (validated.message !== undefined) extra.message = validated.message
      // 状态回报：动作完成瞬间的读数（如 look_at 的实际朝向）。服务器可能在稍后
      // 回写状态（flying-squid 会发 forcedMove），事件里的 result 才是动作的真实结果。
      if (value && typeof value === 'object') extra.result = value
      finish(controller, STATES.SUCCEEDED, extra)
      return { action_id: record.action_id, action: name, status: STATES.SUCCEEDED }
    } catch (error) {
      if (error instanceof ActionCancelled) {
        const status = error.reason === 'timeout' ? STATES.TIMEOUT : STATES.CANCELLED
        if (status === STATES.TIMEOUT && typeof def.cleanup === 'function') {
          try {
            def.cleanup(getBot())
          } catch (cleanupError) {
            log(`[Minecraft Action] cleanup failed action=${name} id=${record.action_id} error=${cleanupError.message}`)
          }
        }
        finish(controller, status, { reason: error.reason })
        return { action_id: record.action_id, action: name, status }
      }
      const message = String(error && error.message ? error.message : error)
      finish(controller, STATES.FAILED, { error: message })
      throw new ActionError(`动作执行失败：${message}`, 'action.failed', 500, {
        action_id: record.action_id,
        action: name,
        status: STATES.FAILED,
      })
    }
  }

  /**
   * 安全停止（最高优先级，任务书 §六/§九）：
   *   取消所有进行中动作 → 清空移动控制位 → 回到 IDLE。
   * 幂等；没有动作/没有 bot 也返回成功，并给出被取消的 action_id 列表。
   */
  function stop() {
    const cancelled = []
    for (const controller of [...active.values()]) {
      const status = controller.record.status
      if (status !== STATES.RUNNING && status !== STATES.QUEUED) continue
      controller.token.cancel('stop')
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
