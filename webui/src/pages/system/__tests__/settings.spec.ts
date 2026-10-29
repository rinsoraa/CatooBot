/**
 * 配置中心页（§38-§53、§72-§73、§86-§96）：级别切换、草稿、预览确认、脏键 PATCH。
 */

import { enableAutoUnmount, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Pinia } from 'pinia'

import { useToast } from '@/composables/toast'
import { useConfigStore } from '@/stores/config'
import type { ConfigFieldMeta, EffectiveField } from '@/types/config'
import Settings from '../Settings.vue'
import {
  fail,
  flushAll,
  installFetch,
  makeField,
  makeRouter,
  ok,
  useFreshPinia,
  type MockReply,
  type MockRequest,
} from './helpers'

const NAME_FIELD = makeField({
  key: 'bot.name',
  label: '机器人名称',
  description: '名称（bot.name）：保存后立即生效。',
  type: 'str',
  area: '系统',
  level: 'basic',
})

const PORT_FIELD = makeField({
  key: 'onebot.port',
  label: 'OneBot 端口',
  type: 'int',
  area: '系统',
  level: 'advanced',
  hot_reload: false,
  restart_required: true,
  usage_status: 'ACTIVE_WITH_RESTART',
  constraints: { min: 1, max: 65535 },
})

const UNUSED_FIELD = makeField({
  key: 'social.attention.enabled',
  label: '注意力',
  type: 'bool',
  area: '社交',
  level: 'expert',
  hot_reload: false,
  restart_required: true,
  usage_status: 'DEFINED_BUT_UNUSED',
  hidden: true,
})

const SCHEMA_ITEMS: ConfigFieldMeta[] = [NAME_FIELD, PORT_FIELD, UNUSED_FIELD]

const EFFECTIVE: EffectiveField[] = [
  {
    key: 'bot.name',
    label: '机器人名称',
    area: '系统',
    level: 'basic',
    type: 'str',
    usage_status: 'ACTIVE',
    hot_reload: true,
    restart_required: false,
    hidden: false,
    value: 'CatooBot',
    source: 'yaml',
  },
  {
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
    source: 'default',
  },
]

function handler(request: MockRequest): MockReply {
  const url = new URL(request.url, 'http://localhost')
  const path = url.pathname
  if (path === '/api/v1/config/schema') {
    const includeUnused = url.searchParams.get('include') === 'unused'
    return ok({
      items: includeUnused ? SCHEMA_ITEMS : SCHEMA_ITEMS.filter((item) => !item.hidden),
      areas: ['系统', '社交'],
      levels: ['basic', 'advanced', 'expert'],
      usage_status: ['ACTIVE', 'ACTIVE_WITH_RESTART', 'CONDITIONALLY_USED', 'DEFINED_BUT_UNUSED', 'LEGACY'],
      legends: { source: {}, usage_status: {} },
    })
  }
  if (path === '/api/v1/config/effective') {
    return ok({ items: EFFECTIVE, count: EFFECTIVE.length })
  }
  if (path === '/api/v1/config/restart-pending') {
    return ok({ pending: [], since: null })
  }
  if (path === '/api/v1/config/validate') {
    const body = request.body as { values: Record<string, unknown> } | null
    const keys = Object.keys(body?.values ?? {})
    return ok({
      valid: true,
      changes: body?.values ?? {},
      hot_reload: keys,
      restart_required: [],
      notes: [],
    })
  }
  if (path === '/api/v1/config' && request.method === 'PATCH') {
    const body = request.body as { values: Record<string, unknown> }
    const keys = Object.keys(body.values)
    return ok({
      saved: keys,
      hot_reload: keys,
      restart_required: [],
      effective: keys.map(() => true),
      values: [],
      notes: [],
      warnings: [],
    })
  }
  return fail(404, 'not_found', `未模拟 ${request.method} ${path}`)
}

interface MountedSettings {
  wrapper: VueWrapper
  calls: MockRequest[]
  pinia: Pinia
}

async function mountSettings(initial = '/system/settings'): Promise<MountedSettings> {
  const pinia = useFreshPinia()
  const calls = installFetch(handler)
  const router = await makeRouter(initial, [
    { path: '/system/settings', name: 'system-settings', component: { template: '<div />' } },
  ])
  const wrapper = mount(Settings, { global: { plugins: [pinia, router] }, attachTo: document.body })
  await flushAll()
  return { wrapper, calls, pinia }
}

const originalScrollIntoView = Element.prototype.scrollIntoView

enableAutoUnmount(afterEach)

beforeEach(() => {
  useToast().clear()
})

afterEach(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView
})

describe('Settings 页', () => {
  it('级别切换会追加更高层字段、加载 unused，并保留未保存草稿', async () => {
    const { wrapper, calls } = await mountSettings()

    expect(wrapper.find('[data-key="bot.name"]').exists()).toBe(true)
    expect(wrapper.find('[data-key="onebot.port"]').exists()).toBe(false)
    expect(wrapper.find('[data-key="social.attention.enabled"]').exists()).toBe(false)

    await wrapper.get('[data-key="bot.name"] input').setValue('新名字')
    await wrapper.get('[data-test="level-expert"]').trigger('click')
    await flushAll()

    expect(wrapper.get('[data-test="draft-kept-hint"]').text()).toContain('已保留 1 项未保存修改')
    expect(wrapper.find('[data-key="onebot.port"]').exists()).toBe(true)
    expect(wrapper.find('[data-key="social.attention.enabled"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="expert-warning"]').text()).toContain('当前未使用')
    expect((wrapper.get('[data-key="bot.name"] input').element as HTMLInputElement).value).toBe('新名字')
    expect(calls.some((call) => call.url.includes('include=unused'))).toBe(true)
  })

  it('修改后出现 DirtyBar，取消能清空草稿', async () => {
    const { wrapper } = await mountSettings()
    const store = useConfigStore()

    expect(wrapper.find('[data-test="dirty-bar"]').exists()).toBe(false)
    await wrapper.get('[data-key="bot.name"] input').setValue('草稿')
    await flushAll()

    expect(wrapper.get('[data-test="dirty-count"]').text()).toContain('有 1 项未保存修改')
    expect(store.isDirty).toBe(true)

    await wrapper.get('[data-test="dirty-discard"]').trigger('click')
    await flushAll()

    expect(wrapper.find('[data-test="dirty-bar"]').exists()).toBe(false)
    expect(store.isDirty).toBe(false)
  })

  it('应用流程：validate 预览 → 确认 → PATCH 只带脏键 → toast + 清空草稿', async () => {
    const { wrapper, calls } = await mountSettings()
    const store = useConfigStore()

    await wrapper.get('[data-key="bot.name"] input').setValue('NewName')
    await flushAll()

    await wrapper.get('[data-test="dirty-apply"]').trigger('click')
    await flushAll()

    const validate = calls.find((call) => call.url.includes('/config/validate'))
    expect(validate?.method).toBe('POST')
    expect(validate?.body).toEqual({ values: { 'bot.name': 'NewName' } })

    const detail = wrapper.get('.cb-dialog__detail').text()
    expect(detail).toContain('旧值')
    expect(detail).toContain('旧值 CatooBot → 新值 NewName')
    expect(detail).toContain('1 项立即生效')
    expect(detail).toContain('0 项需要重启')

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await flushAll()

    const patch = calls.find((call) => call.method === 'PATCH')
    expect(patch?.url).toBe('/api/v1/config')
    expect(patch?.body).toEqual({ values: { 'bot.name': 'NewName' } })

    const { items } = useToast()
    expect(items.value.some((item) => item.message.includes('✓ 设置已保存'))).toBe(true)
    expect(store.isDirty).toBe(false)
    expect(store.draft).toEqual({})
    expect(wrapper.find('[data-test="dirty-bar"]').exists()).toBe(false)
  })

  it('草稿非空时拦截 beforeunload', async () => {
    const { wrapper } = await mountSettings()

    const idle = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(idle)
    expect(idle.defaultPrevented).toBe(false)

    await wrapper.get('[data-key="bot.name"] input').setValue('X')
    const guard = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(guard)
    expect(guard.defaultPrevented).toBe(true)
  })

  it('?level=expert&focus=<key> 深链：定位并高亮目标字段', async () => {
    const scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView

    const { wrapper } = await mountSettings(
      '/system/settings?level=expert&focus=social.attention.enabled',
    )

    const target = wrapper.get('[data-key="social.attention.enabled"]')
    expect(target.classes()).toContain('cb-settings__field--focus')
    expect(target.attributes('id')).toBe('field-social.attention.enabled')
    expect(scrollSpy).toHaveBeenCalled()
  })

  it('来源与生效状态徽标跟随 effective 数据', async () => {
    const { wrapper } = await mountSettings()
    const field = wrapper.get('[data-key="bot.name"]')

    expect(field.get('[data-test="config-source-badge"]').text()).toContain('配置文件')
    expect(field.get('[data-test="config-status-badge"]').text()).toContain('已生效')
  })
})
