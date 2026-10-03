/**
 * TestResultPanel（§6/§16）：Provider 测试与模型测试共用的结果形状；
 * 状态永远有文字，HTTP 状态来源为 error_class 时给出说明，后端 message 原文照登。
 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TestResultPanel from '@/components/ai/TestResultPanel.vue'
import type { TestResult } from '@/types/ai'

function makeResult(overrides: Partial<TestResult> = {}): TestResult {
  return {
    ok: true,
    model: 'fast',
    requested_model: 'fast',
    provider: 'deepseek',
    latency_ms: 412,
    http_status: 200,
    http_status_source: 'upstream',
    error_type: '',
    message: '',
    ...overrides,
  }
}

describe('TestResultPanel', () => {
  it('renders success with latency, alias, real provider model and the reply as plain text', () => {
    const wrapper = mount(TestResultPanel, {
      props: {
        result: makeResult({ provider_model: 'deepseek-chat', response: '测试成功。' }),
      },
    })
    expect(wrapper.text()).toContain('成功')
    expect(wrapper.text()).not.toContain('失败')
    expect(wrapper.get('[data-test="test-http-status"]').text()).toContain('200')
    expect(wrapper.get('[data-test="test-latency"]').text()).toBe('412 ms')
    expect(wrapper.get('[data-test="test-model"]').text()).toBe('fast')
    expect(wrapper.get('[data-test="test-provider-model"]').text()).toBe('deepseek-chat')
    const reply = wrapper.get('[data-test="test-reply"]')
    expect(reply.element.tagName).toBe('PRE')
    expect(reply.text()).toBe('测试成功。')
    expect(wrapper.find('[data-test="test-message"]').exists()).toBe(false)
  })

  it('renders failure with backend message verbatim and no fake reply', () => {
    const wrapper = mount(TestResultPanel, {
      props: {
        result: makeResult({
          ok: false,
          http_status: 429,
          error_type: 'RateLimitError',
          message: '上游返回 429：当前请求过多，请稍后重试',
        }),
      },
    })
    expect(wrapper.text()).toContain('失败')
    expect(wrapper.get('[data-test="test-http-status"]').text()).toContain('429')
    expect(wrapper.get('[data-test="test-error-type"]').text()).toBe('RateLimitError')
    expect(wrapper.get('[data-test="test-message"]').text()).toBe('上游返回 429：当前请求过多，请稍后重试')
    expect(wrapper.find('[data-test="test-reply"]').exists()).toBe(false)
  })

  it('notes when the HTTP status was derived from the error class', () => {
    const derived = mount(TestResultPanel, {
      props: {
        result: makeResult({
          ok: false,
          http_status: 502,
          http_status_source: 'error_class',
          error_type: 'ServerError',
          message: 'bad gateway',
        }),
      },
    })
    expect(derived.get('[data-test="test-http-note"]').text()).toContain('由错误分类推导')

    const upstream = mount(TestResultPanel, { props: { result: makeResult() } })
    expect(upstream.find('[data-test="test-http-note"]').exists()).toBe(false)
  })

  it('hides a provider model identical to the alias and shows the provider-test reply field', () => {
    const sameModel = mount(TestResultPanel, {
      props: {
        result: makeResult({ provider_model: 'fast', reply: 'pong', response: undefined }),
      },
    })
    expect(sameModel.find('[data-test="test-provider-model"]').exists()).toBe(false)
    expect(sameModel.get('[data-test="test-reply"]').text()).toBe('pong')
  })

  it('shows a loading state and handles the empty result', () => {
    const loading = mount(TestResultPanel, { props: { result: null, loading: true } })
    expect(loading.text()).toContain('正在测试…')

    const empty = mount(TestResultPanel, { props: { result: null } })
    expect(empty.text()).toContain('暂无测试结果。')
  })
})
