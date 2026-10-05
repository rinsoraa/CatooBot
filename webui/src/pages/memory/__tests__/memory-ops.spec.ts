/**
 * 记忆运维页（v0.8 迁移）：向量状态与动作、清缓存确认门、整理状态与手动运行、
 * 检索调试（只读）。错误路径必须留在页面上，不把页面打崩。
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fail,
  installFetch,
  ok,
  type MockReply,
  type MockRequest,
} from '@/components/config/__tests__/helpers'
import { useToast } from '@/composables/toast'
import MemoryOps from '@/pages/memory/MemoryOps.vue'
import type {
  MemoryConsolidationReport,
  MemoryConsolidationStatus,
  MemoryEmbeddingStatus,
  MemoryRetrievalDebug,
} from '@/types/domain'

const EMBEDDING: MemoryEmbeddingStatus = {
  available: true,
  embedded: 8,
  memories: 10,
  coverage: 0.8,
  models: 1,
  pending: 2,
  provider: 'openai',
  model: 'text-embedding-3-small',
  dimensions: 1536,
  hits: 12,
  misses: 3,
  failures: 1,
  last_error: null,
  timeout: 30,
}

const REPORT: MemoryConsolidationReport = {
  scanned: 10,
  duplicates_merged: 1,
  archived: 2,
  compressed_clusters: 1,
  compressed_sources: 3,
  llm_compressions: 0,
  conflicts: 0,
  quota_archived: 0,
  errors: 0,
  duration_ms: 12.5,
  scopes: ['user:1'],
  summary: 'merged=1 compressed=1(3 sources, 0 by model) archived=2',
}

const CONSOLIDATION: MemoryConsolidationStatus = {
  enabled: true,
  schedule: 'daily',
  duplicate_threshold: 0.92,
  compression_min_cluster: 3,
  last_report: REPORT,
  runs: 4,
}

const DEBUG: MemoryRetrievalDebug = {
  query: '喜欢吃什么',
  scopes: ['user:1'],
  topics: ['饮食'],
  candidates: 4,
  keyword_candidates: 3,
  semantic_candidates: 2,
  merged_candidates: 4,
  injected: 2,
  dropped_by_guard: 2,
  top_score: 0.87,
  duration_ms: 3.2,
  semantic_available: true,
  weights: { semantic: 0.4 },
  min_final_score: 0.2,
  results: [
    {
      id: 7,
      content: '她喜欢吃草莓',
      layer: 'episodic',
      category: 'preference',
      scope_key: 'user:1',
      status: 'active',
      final: 0.87,
      semantic: 0.9,
      keyword: 0.5,
      importance: 0.6,
      confidence: 0.8,
      recency: 0.7,
      relationship: 0,
      topic_bonus: 0.1,
      temporal: 0,
      origin: 'both',
    },
  ],
}

interface HandlerOptions {
  embedding?: MockReply
  consolidation?: MockReply
  rebuild?: MockReply
  retry?: MockReply
  clearCache?: MockReply
  run?: MockReply
  debug?: MockReply
}

function makeHandler(options: HandlerOptions = {}): (request: MockRequest) => MockReply {
  return (request: MockRequest): MockReply => {
    const path = new URL(request.url, 'http://localhost').pathname
    if (path === '/api/v1/memories/embeddings') return options.embedding ?? ok(EMBEDDING)
    if (path === '/api/v1/memories/embeddings/rebuild') {
      return options.rebuild ?? ok({ action: 'rebuild', result: { rebuilt: 2 } })
    }
    if (path === '/api/v1/memories/embeddings/retry') {
      return options.retry ?? ok({ action: 'retry', result: { rebuilt: 1 } })
    }
    if (path === '/api/v1/memories/embeddings/clear-cache') {
      return options.clearCache ?? ok({ action: 'clear-cache', result: { cleared: true, at: 1 } })
    }
    if (path === '/api/v1/memories/consolidation') return options.consolidation ?? ok(CONSOLIDATION)
    if (path === '/api/v1/memories/consolidation/run') {
      return options.run ?? ok({ scope: '', result: REPORT })
    }
    if (path === '/api/v1/memories/retrieval-debug') return options.debug ?? ok(DEBUG)
    return fail(404, 'internal.error', `未 mock 的请求：${request.url}`)
  }
}

let requests: MockRequest[] = []

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(options: HandlerOptions = {}): Promise<VueWrapper> {
  requests = installFetch(makeHandler(options))
  const wrapper = mount(MemoryOps)
  await settle()
  return wrapper
}

function callsTo(suffix: string, method?: string): MockRequest[] {
  return requests.filter((request) => {
    const path = new URL(request.url, 'http://localhost').pathname
    return path.endsWith(suffix) && (method === undefined || request.method === method)
  })
}

function lastToast() {
  return useToast().items.value.at(-1)
}

const originalFetch = globalThis.fetch

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
  globalThis.fetch = originalFetch
})

describe('MemoryOps', () => {
  it('renders embedding and consolidation status with real numbers', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="embedding-available"]').text()).toContain('已启用')
    expect(wrapper.get('[data-test="embedding-model"]').text()).toContain('openai')
    expect(wrapper.get('[data-test="embedding-model"]').text()).toContain('text-embedding-3-small')
    const coverage = wrapper.get('[data-test="embedding-coverage"]').text()
    expect(coverage).toContain('8')
    expect(coverage).toContain('10')
    expect(coverage).toContain('覆盖率 80%')
    expect(coverage).toContain('待补 2 条')
    expect(wrapper.get('[data-test="embedding-dimensions"]').text()).toContain('1536')
    expect(wrapper.get('[data-test="embedding-failures"]').text()).toContain('1')

    expect(wrapper.get('[data-test="consolidation-schedule"]').text()).toContain('每天')
    expect(wrapper.get('[data-test="consolidation-runs"]').text()).toContain('4')
    expect(wrapper.get('[data-test="consolidation-summary"]').text()).toContain('merged=1')
    expect(wrapper.get('[data-test="report-scanned"]').text()).toBe('10')
    expect(wrapper.get('[data-test="report-scopes"]').text()).toContain('user:1')

    expect(callsTo('/memories/embeddings', 'GET')).toHaveLength(1)
    expect(callsTo('/memories/consolidation', 'GET')).toHaveLength(1)
  })

  it('calls rebuild and retry endpoints and refreshes the status', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="embeddings-rebuild"]').trigger('click')
    await settle()

    const rebuild = callsTo('/memories/embeddings/rebuild', 'POST')
    expect(rebuild).toHaveLength(1)
    expect(lastToast()?.kind).toBe('success')
    expect(lastToast()?.message).toBe('向量重建完成')
    expect(lastToast()?.detail).toBe('已补齐 2 条向量')

    await wrapper.get('[data-test="embeddings-retry"]').trigger('click')
    await settle()

    const retry = callsTo('/memories/embeddings/retry', 'POST')
    expect(retry).toHaveLength(1)
    expect(lastToast()?.message).toBe('向量重试完成')
    expect(lastToast()?.detail).toBe('已补齐 1 条向量')

    // 两个动作后都重新读取状态：初始 1 次 + 重建 1 次 + 重试 1 次。
    expect(callsTo('/memories/embeddings', 'GET')).toHaveLength(3)
  })

  it('surfaces a backend {error} payload as a warning without crashing', async () => {
    const wrapper = await mountPage({
      rebuild: ok({ action: 'rebuild', result: { error: 'semantic memory disabled' } }),
    })

    await wrapper.get('[data-test="embeddings-rebuild"]').trigger('click')
    await settle()

    expect(lastToast()?.kind).toBe('warning')
    expect(lastToast()?.message).toBe('向量重建未执行')
    expect(lastToast()?.detail).toBe('semantic memory disabled')
    // 页面仍在，不因业务错误丢状态。
    expect(wrapper.find('[data-test="embedding-grid"]').exists()).toBe(true)
  })

  it('confirm-gates clear-cache before calling the API and sends the confirm body', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="embeddings-clear-cache"]').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(callsTo('/memories/embeddings/clear-cache', 'POST')).toHaveLength(0)

    // 取消不发请求，页面保留。
    await wrapper.get('[data-test="cancel"]').trigger('click')
    await settle()
    expect(callsTo('/memories/embeddings/clear-cache', 'POST')).toHaveLength(0)

    await wrapper.get('[data-test="embeddings-clear-cache"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const clear = callsTo('/memories/embeddings/clear-cache', 'POST')
    expect(clear).toHaveLength(1)
    expect(clear[0]?.body).toEqual({ confirm: 'clear-cache' })
    expect(lastToast()?.kind).toBe('success')
    expect(lastToast()?.message).toBe('清空向量缓存完成')
  })

  it('runs consolidation with an optional scope and renders the report', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="consolidation-scope"]').setValue('group:9')
    await wrapper.get('[data-test="consolidation-run"]').trigger('click')
    await settle()

    const run = callsTo('/memories/consolidation/run', 'POST')
    expect(run).toHaveLength(1)
    expect(run[0]?.body).toEqual({ scope: 'group:9' })
    expect(lastToast()?.kind).toBe('success')
    expect(lastToast()?.message).toBe('记忆整理完成')
    expect(lastToast()?.detail).toContain('merged=1')
    expect(wrapper.get('[data-test="consolidation-summary"]').text()).toContain('merged=1')
    expect(wrapper.get('[data-test="report-duplicates"]').text()).toBe('1')
  })

  it('keeps the page alive when status requests fail', async () => {
    const wrapper = await mountPage({
      embedding: fail(500, 'internal.error', '向量状态读取失败'),
      consolidation: fail(500, 'internal.error', '整理状态读取失败'),
    })

    expect(wrapper.get('[data-test="embedding-error"]').text()).toContain('向量状态读取失败')
    expect(wrapper.get('[data-test="consolidation-error"]').text()).toContain('整理状态读取失败')
    expect(wrapper.findAll('[role="alert"]').length).toBeGreaterThanOrEqual(2)

    // 其它区块与表单仍然可用。
    expect(wrapper.find('[data-test="ops-retrieval"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="retrieval-form"]').exists()).toBe(true)
  })

  it('surfaces action failures via toast and keeps the page', async () => {
    const wrapper = await mountPage({
      retry: fail(500, 'internal.error', '重试接口失败'),
    })

    await wrapper.get('[data-test="embeddings-retry"]').trigger('click')
    await settle()

    expect(lastToast()?.kind).toBe('error')
    expect(lastToast()?.message).toBe('向量重试失败')
    expect(lastToast()?.detail).toBe('重试接口失败')
    expect(wrapper.find('[data-test="embedding-grid"]').exists()).toBe(true)
  })

  it('refuses an empty retrieval query and renders the scored results otherwise', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="retrieval-form"]').trigger('submit')
    await settle()

    expect(callsTo('/memories/retrieval-debug', 'GET')).toHaveLength(0)
    expect(lastToast()?.kind).toBe('warning')
    expect(lastToast()?.message).toBe('请输入查询词')

    await wrapper.get('[data-test="retrieval-q"]').setValue('喜欢吃什么')
    await wrapper.get('[data-test="retrieval-scope"]').setValue('user:1')
    await wrapper.get('[data-test="retrieval-form"]').trigger('submit')
    await settle()

    const debug = callsTo('/memories/retrieval-debug', 'GET')
    expect(debug).toHaveLength(1)
    expect(decodeURIComponent(debug[0]?.url ?? '')).toContain('q=喜欢吃什么')
    expect(decodeURIComponent(debug[0]?.url ?? '')).toContain('scope=user:1')

    const item = wrapper.get('[data-test="retrieval-item"]')
    expect(item.text()).toContain('她喜欢吃草莓')
    expect(item.get('[data-test="retrieval-final"]').text()).toContain('0.87')
    const components = item.get('[data-test="retrieval-components"]').text()
    expect(components).toContain('语义 0.9')
    expect(components).toContain('关键词 0.5')
    expect(components).toContain('主题 0.1')
    expect(wrapper.get('[data-test="retrieval-candidates"]').text()).toContain('4')
    expect(wrapper.get('[data-test="retrieval-semantic-available"]').text()).toContain('是')
    expect(wrapper.get('[data-test="retrieval-raw"]').text()).toContain('"query"')
  })

  it('shows a retrieval error inline and keeps the rest of the page', async () => {
    const wrapper = await mountPage({
      debug: fail(400, 'memory.query_required', '检索调试需要 q=查询词'),
    })

    await wrapper.get('[data-test="retrieval-q"]').setValue('任意')
    await wrapper.get('[data-test="retrieval-form"]').trigger('submit')
    await settle()

    expect(wrapper.get('[data-test="retrieval-error"]').text()).toContain('检索调试需要 q=查询词')
    expect(wrapper.find('[data-test="retrieval-item"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="ops-maintenance"]').exists()).toBe(true)
  })

  it('disables the vector action buttons while a request is in flight', async () => {
    const gate: { release: (() => void) | null } = { release: null }
    const handler = makeHandler()
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url =
        typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
      const path = new URL(url, 'http://localhost').pathname
      if (path.endsWith('/memories/embeddings/rebuild')) {
        await new Promise<void>((resolve) => {
          gate.release = resolve
        })
      }
      let body: unknown = null
      if (typeof init?.body === 'string') {
        try {
          body = JSON.parse(init.body)
        } catch {
          body = init.body
        }
      }
      const reply = handler({ url, method: (init?.method ?? 'GET').toUpperCase(), body })
      const status = reply.status ?? 200
      return {
        ok: status < 400,
        status,
        text: async () => JSON.stringify(reply.payload),
      } as unknown as Response
    }) as unknown as typeof fetch

    const wrapper = mount(MemoryOps)
    await settle()

    await wrapper.get('[data-test="embeddings-rebuild"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="embeddings-rebuild"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="embeddings-retry"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="embeddings-clear-cache"]').attributes('disabled')).toBeDefined()

    gate.release?.()
    await settle()

    expect(wrapper.get('[data-test="embeddings-rebuild"]').attributes('disabled')).toBeUndefined()
  })
})
