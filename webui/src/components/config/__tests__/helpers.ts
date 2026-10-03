/** `src/components/config` 测试的共享工具：信封 mock、pinia、字段 fixture。 */

import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { vi } from 'vitest'

import type { ConfigFieldMeta } from '@/types/config'

export interface MockRequest {
  url: string
  method: string
  body: unknown
}

export interface MockReply {
  status?: number
  payload: unknown
}

export function ok<T>(data: T): MockReply {
  return { payload: { ok: true, data, meta: { request_id: 't' } } }
}

export function fail(status: number, code: string, message: string): MockReply {
  return { status, payload: { ok: false, error: { code, message }, meta: { request_id: 't' } } }
}

/** 把 `globalThis.fetch` 换成信封 mock，并返回所有请求记录。 */
export function installFetch(handler: (request: MockRequest) => MockReply): MockRequest[] {
  const calls: MockRequest[] = []
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = null
    if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body)
      } catch {
        body = init.body
      }
    }
    const request: MockRequest = { url, method, body }
    calls.push(request)
    const reply = handler(request)
    const status = reply.status ?? 200
    return {
      ok: status < 400,
      status,
      text: async () => JSON.stringify(reply.payload),
    } as unknown as Response
  })
  globalThis.fetch = mock as unknown as typeof fetch
  return calls
}

export function useFreshPinia(): Pinia {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

export async function flushAll(): Promise<void> {
  await flushPromises()
  await flushPromises()
}

export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function makeField(overrides: Partial<ConfigFieldMeta> = {}): ConfigFieldMeta {
  return {
    key: 'bot.name',
    label: '名称',
    description: '名称（bot.name）：保存后立即生效。',
    type: 'str',
    default: '',
    constraints: {},
    area: '系统',
    level: 'basic',
    hot_reload: true,
    restart_required: false,
    usage_status: 'ACTIVE',
    sensitive: false,
    choices: [],
    hidden: false,
    ...overrides,
  }
}
