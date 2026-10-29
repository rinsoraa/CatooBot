/** 等待重启页（§90）：pending 列表、运行值 vs 已保存、原因说明与返回链接。 */

import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useToast } from '@/composables/toast'
import type { ConfigFieldMeta, EffectiveField } from '@/types/config'
import RestartPending from '../RestartPending.vue'
import { flushAll, installFetch, makeField, makeRouter, ok, useFreshPinia, type MockRequest } from './helpers'

const PORT_FIELD: ConfigFieldMeta = makeField({
  key: 'onebot.port',
  label: 'OneBot 端口',
  type: 'int',
  area: '系统',
  level: 'advanced',
  hot_reload: false,
  restart_required: true,
  usage_status: 'ACTIVE_WITH_RESTART',
})

const PORT_EFFECTIVE: EffectiveField = {
  key: 'onebot.port',
  label: 'OneBot 端口',
  area: '系统',
  level: 'advanced',
  type: 'int',
  usage_status: 'ACTIVE_WITH_RESTART',
  hot_reload: false,
  restart_required: true,
  hidden: false,
  value: 8500,
  source: 'overrides',
}

function handler(pending: string[], effective: EffectiveField[]) {
  return (request: MockRequest) => {
    const url = new URL(request.url, 'http://localhost')
    if (url.pathname === '/api/v1/config/schema') {
      return ok({
        items: [PORT_FIELD],
        areas: ['系统'],
        levels: ['basic', 'advanced', 'expert'],
        usage_status: [],
        legends: { source: {}, usage_status: {} },
      })
    }
    if (url.pathname === '/api/v1/config/effective') {
      return ok({ items: effective, count: effective.length })
    }
    if (url.pathname === '/api/v1/config/restart-pending') {
      return ok({ pending, since: pending.length ? 1700000000 : null })
    }
    return ok({})
  }
}

async function mountPage(pending: string[], effective: EffectiveField[]): Promise<VueWrapper> {
  const pinia = useFreshPinia()
  installFetch(handler(pending, effective))
  const router = await makeRouter('/system/settings/restart-pending', [
    { path: '/system/settings', name: 'system-settings', component: { template: '<div />' } },
    { path: '/system/settings/restart-pending', name: 'system-restart-pending', component: { template: '<div />' } },
  ])
  const wrapper = mount(RestartPending, { global: { plugins: [pinia, router] } })
  await flushAll()
  return wrapper
}

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  useToast().clear()
})

describe('RestartPending 页', () => {
  it('列出 pending 键：中文名、已保存（等待重启）、当前运行值与原因', async () => {
    const wrapper = await mountPage(['onebot.port'], [PORT_EFFECTIVE])

    const item = wrapper.get('[data-test="restart-pending-item"]')
    expect(item.text()).toContain('OneBot 端口')
    expect(item.text()).toContain('onebot.port')
    expect(item.get('[data-test="restart-pending-saved"]').text()).toContain('已保存（等待重启）')
    expect(item.get('[data-test="restart-pending-running"]').text()).toContain('8500')
    expect(item.text()).toContain('该字段仅在启动时读取')
    expect(wrapper.get('[data-test="restart-pending-since"]').text()).toContain('等待重启')
    expect(wrapper.find('a').exists()).toBe(true)
  })

  it('没有 pending 时显示空态与返回设置', async () => {
    const wrapper = await mountPage([], [])

    expect(wrapper.find('[data-test="restart-pending-item"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('没有等待重启的修改')
    expect(wrapper.get('a').text()).toContain('返回设置')
  })
})
