/** RoleCard（W4 §27-§30、§103）：中文角色名 / 未配置 / 重启提示 / 高级信息。 */
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RoleCard from '@/components/ai/RoleCard.vue'
import type { ModelItem, RoleItem } from '@/types/ai'

function model(name: string, overrides: Partial<ModelItem> = {}): ModelItem {
  return {
    name,
    provider: 'prov',
    model: `id-${name}`,
    enabled: true,
    order: 0,
    roles: [],
    live: true,
    in_cooldown: false,
    cooldown_until: 0,
    cooldown_remaining_seconds: 0,
    failure_count: 0,
    last_error: null,
    usage: {
      calls: 0,
      failures: 0,
      prompt_tokens: 0,
      completion_tokens: 0,
      avg_latency_ms: 0,
      max_latency_ms: 0,
    },
    ...overrides,
  }
}

function role(overrides: Partial<RoleItem> = {}): RoleItem {
  return {
    role: 'chat',
    key: 'ai.models[0].name',
    model: 'fast',
    source: 'models',
    restart_required: false,
    ...overrides,
  }
}

const MODELS = [model('fast'), model('smart', { enabled: false })]

describe('RoleCard', () => {
  it('shows the Chinese role label from the descriptor and falls back to the raw key', () => {
    const chat = mount(RoleCard, { props: { item: role(), models: MODELS } })
    expect(chat.get('[data-test="role-card"]').attributes('data-role')).toBe('chat')
    expect(chat.text()).toContain('聊天回复')

    const unknown = mount(RoleCard, {
      props: { item: role({ role: 'custom_role', key: 'custom.key' }), models: MODELS },
    })
    expect(unknown.text()).toContain('custom_role')
  })

  it('marks an empty binding as 未配置 and renders the bound model otherwise', () => {
    const unset = mount(RoleCard, {
      props: { item: role({ role: 'vision', model: '', key: 'media.vision_model' }), models: MODELS },
    })
    expect(unset.get('[data-test="role-unset"]').text()).toBe('⚠ 未配置')

    const bound = mount(RoleCard, { props: { item: role(), models: MODELS } })
    expect(bound.get('[data-test="role-model"]').text()).toBe('fast')
    expect(bound.find('[data-test="role-unset"]').exists()).toBe(false)
  })

  it('shows the restart notice only when restart_required is set', () => {
    const restart = mount(RoleCard, {
      props: { item: role({ role: 'vision', model: 'fast', restart_required: true }), models: MODELS },
    })
    expect(restart.get('[data-test="role-restart"]').text()).toContain('已保存 · 重启后生效')

    const hot = mount(RoleCard, { props: { item: role(), models: MODELS } })
    expect(hot.find('[data-test="role-restart"]').exists()).toBe(false)
  })

  it('keeps the internal key in 高级信息 and emits change with model or empty unbind', async () => {
    const wrapper = mount(RoleCard, { props: { item: role(), models: MODELS } })
    expect(wrapper.get('[data-test="role-details"]').text()).toContain('高级信息')
    expect(wrapper.get('[data-test="role-key"]').text()).toBe('ai.models[0].name')

    const select = wrapper.get('[data-test="role-select"]')
    const options = select.findAll('option').map((option) => option.text())
    expect(options[0]).toContain('解绑')
    expect(options.join(' ')).toContain('smart（已停用）')

    await select.setValue('smart')
    expect(wrapper.emitted('change')?.[0]).toEqual(['smart'])
    await select.setValue('')
    expect(wrapper.emitted('change')?.[1]).toEqual([''])
  })
})
