/** 状态类基础组件（§32）：LoadingState / ErrorState / EmptyState。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'

describe('LoadingState', () => {
  it('sets aria-busy and renders the requested number of skeleton rows', () => {
    const wrapper = mount(LoadingState, { props: { rows: 4 } })
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.findAll('.cb-loading__bar')).toHaveLength(4)
    expect(wrapper.text()).toContain('加载中…')
  })

  it('renders a custom label', () => {
    const wrapper = mount(LoadingState, { props: { label: '正在读取运行状态' } })
    expect(wrapper.text()).toContain('正在读取运行状态')
  })
})

describe('ErrorState', () => {
  it('renders message and detail with role=alert, without a retry button by default', () => {
    const wrapper = mount(ErrorState, {
      props: { message: '无法连接到 CatooBot 服务', detail: 'network.error' },
    })
    expect(wrapper.attributes('role')).toBe('alert')
    expect(wrapper.text()).toContain('无法连接到 CatooBot 服务')
    expect(wrapper.text()).toContain('network.error')
    expect(wrapper.find('[data-test="retry"]').exists()).toBe(false)
  })

  it('renders the retry button and emits retry when a listener exists', async () => {
    const wrapper = mount(ErrorState, {
      props: { message: '加载失败', retryLabel: '重新加载' },
      attrs: { onRetry: vi.fn() },
    })
    const button = wrapper.get('[data-test="retry"]')
    expect(button.text()).toBe('重新加载')
    await button.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })
})

describe('EmptyState', () => {
  it('renders default text and the action slot', () => {
    const fallback = mount(EmptyState)
    expect(fallback.text()).toContain('暂无数据')

    const wrapper = mount(EmptyState, {
      props: { title: '还没有记忆', description: '当 CatooBot 开始聊天后，这里会出现记忆条目。' },
      slots: { default: '<button type="button">去聊天</button>' },
    })
    expect(wrapper.text()).toContain('还没有记忆')
    expect(wrapper.text()).toContain('这里会出现记忆条目')
    expect(wrapper.get('button').text()).toBe('去聊天')
  })
})
