/** LogViewer（W5 §45-§50）：有界 500、暂停计数、清空只清本地、时间与可访问性。 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import LogViewer, { type LogViewerEntry } from '@/components/domain/LogViewer.vue'
import { installFetch, type MockRequest } from '@/components/config/__tests__/helpers'

function entry(index: number, overrides: Partial<LogViewerEntry> = {}): LogViewerEntry {
  return {
    ts: 1700000000 + index,
    level: 'INFO',
    channel: 'world',
    message: `第 ${index} 条`,
    ...overrides,
  }
}

let requests: MockRequest[] = []

beforeEach(() => {
  requests = installFetch(() => ({
    status: 404,
    payload: { ok: false, error: { code: 'x', message: 'LogViewer 不应发起任何请求' } },
  }))
})

describe('LogViewer', () => {
  it('keeps at most 500 rows and drops the oldest', async () => {
    const many = Array.from({ length: 600 }, (_value, index) => entry(index))
    const wrapper = mount(LogViewer, { props: { entries: many } })
    await flushPromises()

    const rows = wrapper.findAll('[data-test="log-row"]')
    expect(rows).toHaveLength(500)
    // 最旧的 100 条被丢弃，缓冲从第 101 条开始
    expect(rows[0]?.get('[data-test="log-message"]').text()).toBe('第 100 条')
    expect(rows.at(-1)?.get('[data-test="log-message"]').text()).toBe('第 599 条')
  })

  it('counts new rows while paused and appends them on resume', async () => {
    const first = [entry(1), entry(2)]
    const wrapper = mount(LogViewer, { props: { entries: first } })
    await wrapper.setProps({ paused: true })
    await wrapper.setProps({ entries: [...first, entry(3), entry(4)] })
    await flushPromises()

    expect(wrapper.findAll('[data-test="log-row"]')).toHaveLength(2)
    expect(wrapper.get('[data-test="log-paused"]').text()).toBe('已暂停 · 新消息 2 条')

    await wrapper.setProps({ paused: false })
    await flushPromises()

    expect(wrapper.findAll('[data-test="log-row"]')).toHaveLength(4)
    expect(wrapper.find('[data-test="log-paused"]').exists()).toBe(false)
  })

  it('clear empties only the local buffer and emits clear without any request', async () => {
    const wrapper = mount(LogViewer, { props: { entries: [entry(1), entry(2)] } })
    expect(wrapper.findAll('[data-test="log-row"]')).toHaveLength(2)

    await wrapper.get('[data-test="log-clear"]').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('clear')).toHaveLength(1)
    expect(wrapper.findAll('[data-test="log-row"]')).toHaveLength(0)
    expect(wrapper.get('[data-test="log-clear"]').attributes('title')).toContain('不会删除服务器日志')
    expect(requests).toHaveLength(0)
  })

  it('formats long timestamps and exposes an aria-live log region', async () => {
    const wrapper = mount(LogViewer, {
      props: {
        entries: [
          entry(0, { ts: 1700000000 }),
          entry(1, { ts: null, time: '12:34:56' }),
        ],
      },
    })

    const times = wrapper.findAll('[data-test="log-time"]')
    expect(times[0]?.text()).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(times[1]?.text()).toBe('12:34:56')

    const region = wrapper.get('[role="log"]')
    expect(region.attributes('aria-live')).toBe('polite')

    await wrapper.get('[data-test="log-autoscroll"]').trigger('click')
    expect(wrapper.emitted('update:autoScroll')?.[0]).toEqual([false])
  })
})
