/**
 * 提示词页（v0.8 `/prompts` 迁移）：读取双提示词、只提交改动的记忆提取提示、
 * 无改动不发请求、失败展示后端 message、读取失败可重试。
 */
import { flushPromises, mount, RouterLinkStub, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AiPrompts from '@/pages/ai/AiPrompts.vue'
import { useToast, type ToastItem } from '@/composables/toast'

interface RecordedRequest {
  url: string
  method: string
  body: unknown
}

const originalFetch = globalThis.fetch
let requests: RecordedRequest[] = []
let pinia: Pinia

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ ok: true, data, meta: { request_id: 't' } }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function failure(status: number, message: string): Response {
  return new Response(
    JSON.stringify({ ok: false, error: { code: 'prompts.empty', message }, meta: { request_id: 't' } }),
    { status, headers: { 'Content-Type': 'application/json' } },
  )
}

function installFetch(handler: (url: string, method: string, body: unknown) => Response): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: unknown = null
    if (typeof init?.body === 'string' && init.body.length > 0) {
      try {
        body = JSON.parse(init.body) as unknown
      } catch {
        body = null
      }
    }
    requests.push({ url, method, body })
    return handler(url, method, body)
  }) as unknown as typeof fetch
}

const PROMPTS = {
  persona_system_prompt: '你是小灯。',
  memory_extraction_prompt: '从对话中提取值得记住的事实。',
}

function lastToast(): ToastItem | undefined {
  return useToast().items.value.at(-1)
}

async function settle(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  const wrapper = mount(AiPrompts, {
    global: { plugins: [pinia], stubs: { RouterLink: RouterLinkStub } },
    // jsdom 只有在表单挂到 document 上时才会由 submit 按钮触发 submit 事件。
    attachTo: document.body,
  })
  await settle()
  return wrapper
}

function textareaValue(wrapper: VueWrapper): string {
  return (wrapper.get('[data-test="prompts-memory-input"] textarea').element as HTMLTextAreaElement)
    .value
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
  requests = []
  installFetch((url, method) => {
    if (url.includes('/api/v1/prompts') && method === 'GET') return envelope(PROMPTS)
    if (url.includes('/api/v1/prompts') && method === 'PATCH') {
      return envelope({ ...PROMPTS, memory_extraction_prompt: '新的提取提示' })
    }
    return failure(404, `未 mock 的请求：${method} ${url}`)
  })
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('AiPrompts', () => {
  it('读取两个提示词：记忆提取可编辑，人设 System Prompt 只读并链接到角色页', async () => {
    const wrapper = await mountPage()

    expect(textareaValue(wrapper)).toBe('从对话中提取值得记住的事实。')
    expect(wrapper.get('[data-test="prompts-persona-value"]').text()).toBe('你是小灯。')

    const link = wrapper.findComponent(RouterLinkStub)
    expect(link.props('to')).toBe('/character')
  })

  it('保存只提交改动的 memory_extraction_prompt 并提示成功', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="prompts-memory-input"] textarea').setValue('新的提取提示')
    await wrapper.get('[data-test="prompts-save"]').trigger('click')
    await settle()

    const patch = requests.find((request) => request.method === 'PATCH')
    expect(patch?.url).toContain('/api/v1/prompts')
    expect(patch?.body).toEqual({ memory_extraction_prompt: '新的提取提示' })
    expect(lastToast()?.message).toBe('提示词已保存')
  })

  it('没有改动时保存按钮禁用，不发 PATCH', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="prompts-save"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="prompts-save"]').trigger('click')
    await settle()

    expect(requests.filter((request) => request.method === 'PATCH')).toHaveLength(0)
  })

  it('清空输入表示移除覆盖：提交空字符串', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="prompts-memory-input"] textarea').setValue('')
    await wrapper.get('[data-test="prompts-save"]').trigger('click')
    await settle()

    const patch = requests.find((request) => request.method === 'PATCH')
    expect(patch?.body).toEqual({ memory_extraction_prompt: '' })
  })

  it('保存失败时展示后端 message，不弹成功 toast', async () => {
    installFetch((url, method) => {
      if (url.includes('/api/v1/prompts') && method === 'GET') return envelope(PROMPTS)
      if (url.includes('/api/v1/prompts') && method === 'PATCH') {
        return failure(400, '没有可写的提示词字段')
      }
      return failure(404, `未 mock 的请求：${method} ${url}`)
    })
    const wrapper = await mountPage()

    await wrapper.get('[data-test="prompts-memory-input"] textarea').setValue('改一下')
    await wrapper.get('[data-test="prompts-save"]').trigger('click')
    await settle()

    expect(wrapper.get('[data-test="prompts-save-error"]').text()).toContain('没有可写的提示词字段')
    expect(lastToast()?.kind).toBe('error')
    expect(textareaValue(wrapper)).toBe('改一下')
  })

  it('读取失败显示错误态，重试后恢复', async () => {
    let failures = 1
    installFetch((url, method) => {
      if (url.includes('/api/v1/prompts') && method === 'GET') {
        if (failures > 0) {
          failures -= 1
          return failure(500, '读取提示词失败')
        }
        return envelope(PROMPTS)
      }
      return failure(404, `未 mock 的请求：${method} ${url}`)
    })
    const wrapper = await mountPage()

    expect(wrapper.text()).toContain('读取提示词失败')
    await wrapper.get('[data-test="retry"]').trigger('click')
    await settle()

    expect(textareaValue(wrapper)).toBe('从对话中提取值得记住的事实。')
  })
})
