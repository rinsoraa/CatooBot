'use strict'

// P3: authenticated evidence only. This module never executes game actions.
const crypto = require('crypto')

const MAX_BYTES = 8192
const DIMENSIONS = new Set(['minecraft:overworld', 'minecraft:the_nether', 'minecraft:the_end'])
const canonical = value => String(value || '').replace(/^minecraft:/, '')
const sign = (body, token) => crypto.createHmac('sha256', token).update(body).digest('hex')
function verify(body, token, signature) {
  if (!token || typeof signature !== 'string' || !/^[0-9a-f]{64}$/i.test(signature)) return false
  return crypto.timingSafeEqual(Buffer.from(sign(body, token), 'hex'), Buffer.from(signature, 'hex'))
}
function dimensionOf(raw) {
  const aliases = { overworld: 'minecraft:overworld', the_nether: 'minecraft:the_nether', the_end: 'minecraft:the_end' }
  const result = aliases[raw] || raw
  return DIMENSIONS.has(result) ? result : ''
}
function unavailable(reason) {
  const error = new Error('严格服务端归因不可用：' + reason)
  error.code = 'block.attribution_unavailable'
  return error
}

class StrictAttribution {
  constructor({ enabled = false, url = 'http://127.0.0.1:19132', token = '', fetcher = globalThis.fetch } = {}) {
    this.enabled = enabled
    this.token = token
    this.fetcher = fetcher
    this.pending = new Map()
    this.counters = { accepted: 0, rejected: 0, duplicate: 0, late: 0, conflict: 0, unproven: 0 }
    try {
      const parsed = new URL(url)
      if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || parsed.username || parsed.password ||
          parsed.search || parsed.hash || (parsed.pathname !== '/' && parsed.pathname !== '')) throw new Error()
      this.url = parsed.origin
    } catch { this.url = null }
  }

  begin({ actionId, sessionId, name, uuid, dimension, target, block }) {
    if (!this.enabled) return null
    if (!this.url || this.token.length < 16) throw unavailable('configuration')
    dimension = dimensionOf(dimension)
    if (!dimension || !actionId || !name || !uuid || !sessionId || !target ||
        !['x', 'y', 'z'].every(key => Number.isSafeInteger(target[key]))) throw unavailable('identity_or_target')
    if (this.pending.size >= 8 || this.pending.has(actionId)) throw unavailable('capacity')
    target = Object.freeze({ ...target })
    const state = {
      actionId, sessionId, name, uuid, dimension, target: { ...target }, block: canonical(block),
      abort: new AbortController(), session: null, reservation: null, event: null,
      closed: false, conflict: false, wake: null,
    }
    this.pending.set(actionId, state)
    const close = () => {
      if (state.closed) return
      state.closed = true
      state.abort.abort()
      this.pending.delete(actionId)
      if (state.wake) state.wake()
    }
    return {
      state, close,
      prepare: async () => {
        try {
          const nonce = crypto.randomUUID()
          const session = await this.request('/session?name=' + encodeURIComponent(name) + '&nonce=' + nonce,
            undefined, name + '\n' + nonce, state)
          if (state.closed || session.schema !== 1 || session.nonce !== nonce || session.player_uuid !== uuid ||
              typeof session.session_ref !== 'string' || !session.session_ref ||
              !Number.isSafeInteger(session.session_seq) || session.session_seq <= 0) throw unavailable('session')
          state.session = session
          const fields = {
            session_ref: session.session_ref, action_id: actionId, nonce: crypto.randomUUID(),
            dimension, pos: `${target.x},${target.y},${target.z}`, ...target,
            expected_block: 'minecraft:' + state.block, ttl_ms: 60000,
          }
          const receipt = await this.request('/reserve', fields, null, state)
          if (state.closed || receipt.schema !== 1 || receipt.action_id !== actionId || receipt.nonce !== fields.nonce ||
              receipt.session_ref !== session.session_ref || typeof receipt.reservation_id !== 'string' || !receipt.reservation_id) {
            throw unavailable('receipt')
          }
          state.reservation = receipt.reservation_id
          if (state.event && state.event.reservation_id !== state.reservation) state.conflict = true
        } catch (error) { close(); throw unavailable(state.abort.signal.aborted ? 'cancelled_or_unavailable' : 'registration') }
      },
      wait: async ms => {
        if (!state.closed && !state.conflict && (!state.event || !state.reservation)) {
          await new Promise(resolve => {
            const timer = setTimeout(done, Math.max(0, ms))
            function done() { clearTimeout(timer); state.wake = null; resolve() }
            state.wake = done
            if (state.closed || state.conflict || (state.event && state.reservation)) done()
          })
        }
        if (state.closed || state.conflict || !state.event || !state.reservation ||
            state.event.reservation_id !== state.reservation) return null
        return {
          kind: 'dig_attribution', schema: 3, action_id: actionId, session_id: sessionId,
          target: { ...target }, dimension, world_effect: 'BLOCK_REMOVED', attribution: 'SELF_CONFIRMED',
          assurance: 'STRICT', confirm_basis: 'server_block_break', strict_self_proof: true,
          reason_code: 'unique_authenticated_server_event',
          server_proof: { schema: 1, source: 'server_block_break', event_id: state.event.event_id,
            reservation_id: state.reservation, action_id: actionId, session_ref: state.session.session_ref,
            session_seq: state.session.session_seq, player_uuid: uuid, dimension,
            pos: `${target.x},${target.y},${target.z}`, block_before: state.event.block_before,
            block_after: state.event.block_after },
        }
      },
    }
  }

  async request(route, fields, signed, state) {
    const body = fields === undefined ? undefined : JSON.stringify(fields)
    const response = await this.fetcher(this.url + route, {
      method: body === undefined ? 'GET' : 'POST', body, redirect: 'error',
      headers: { 'Content-Type': 'application/json', 'X-Signature': sign(signed === null ? body : signed, this.token) },
      signal: AbortSignal.any([state.abort.signal, AbortSignal.timeout(3000)]),
    })
    let length = 0
    const chunks = []
    if (!response.body) throw unavailable('empty_response')
    for await (const chunk of response.body) {
      length += chunk.length
      if (length > MAX_BYTES) { state.abort.abort(); throw unavailable('response_size') }
      chunks.push(Buffer.from(chunk))
    }
    const raw = Buffer.concat(chunks).toString('utf8')
    if (!verify(raw, this.token, response.headers.get('x-signature'))) throw unavailable('response_authentication')
    const result = JSON.parse(raw)
    if (!response.ok || !result || result.ok !== true) throw unavailable('response_rejected')
    return result
  }

  receive(raw, signature) {
    if (!this.enabled) return { code: 503, reason: 'disabled' }
    if (Buffer.byteLength(raw) > MAX_BYTES) return { code: 413, reason: 'too_large' }
    if (!verify(raw, this.token, signature)) { this.counters.rejected++; return { code: 401, reason: 'hmac' } }
    let event
    try { event = JSON.parse(raw) } catch { return { code: 400, reason: 'json' } }
    if (!event || Array.isArray(event) || event.schema !== 1 || event.source !== 'server_block_break' ||
        typeof event.event_id !== 'string' || !event.event_id || event.event_id.length > 128 ||
        typeof event.reservation_id !== 'string' || typeof event.action_id !== 'string') {
      this.counters.rejected++; return { code: 400, reason: 'schema' }
    }
    if (['UNKNOWN', 'AMBIGUOUS', 'UNREGISTERED_BREAK'].includes(event.shadow)) {
      this.counters.unproven++; return { code: 200, reason: 'unproven' }
    }
    if (!event.reservation_id || !event.action_id) { this.counters.rejected++; return { code: 400, reason: 'correlation' } }
    const state = this.pending.get(event.action_id)
    if (!state || state.closed) { this.counters.late++; return { code: 200, reason: 'inactive' } }
    const session = state.session
    if (!session || event.session_ref !== session.session_ref || event.session_seq !== session.session_seq ||
        event.player_uuid !== state.uuid || event.dimension !== state.dimension ||
        event.pos !== `${state.target.x},${state.target.y},${state.target.z}` ||
        canonical(event.block_before) !== state.block || !['minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'].includes(event.block_after) ||
        event.shadow !== 'SELF_CONFIRMED' || (state.reservation && event.reservation_id !== state.reservation)) {
      this.counters.rejected++; return { code: 409, reason: 'correlation' }
    }
    if (state.event) {
      if (JSON.stringify(state.event) === JSON.stringify(event)) { this.counters.duplicate++; return { code: 200, reason: 'duplicate' } }
      state.conflict = true; this.counters.conflict++
      if (state.wake) state.wake()
      return { code: 409, reason: 'conflict' }
    }
    state.event = event
    this.counters.accepted++
    if (state.wake) state.wake()
    return { code: 200, reason: 'accepted' }
  }
}

module.exports = { StrictAttribution, sign, verify, dimensionOf, MAX_BYTES }
