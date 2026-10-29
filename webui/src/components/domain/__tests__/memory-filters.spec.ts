/** 记忆筛选（W5 §27）：300ms 防抖、各参数出参、清空回 undefined。 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import MemoryFilters from '@/components/domain/MemoryFilters.vue'
import type { MemoryFilters as MemoryFilterModel } from '@/types/domain'
import { wait } from '@/components/config/__tests__/helpers'

function model(overrides: Partial<MemoryFilterModel> = {}): MemoryFilterModel {
  return { mode: 'hybrid', status: 'active', limit: 20, ...overrides }
}

function mountFilters(initial: MemoryFilterModel = model()): VueWrapper {
  return mount(MemoryFilters, { props: { model: initial } })
}

function lastModel(wrapper: VueWrapper): MemoryFilterModel {
  const events = wrapper.emitted('update:model')
  expect(events && events.length > 0).toBe(true)
  const payload = events?.[events.length - 1]?.[0]
  return payload as MemoryFilterModel
}

describe('MemoryFilters', () => {
  it('debounces the keyword input to exactly one update', async () => {
    const wrapper = mountFilters()
    const input = wrapper.get('[data-test="filter-q"]')

    await input.setValue('猫')
    await input.setValue('猫咪')
    await wait(400)

    const events = wrapper.emitted('update:model')
    expect(events?.length).toBe(1)
    expect(lastModel(wrapper).q).toBe('猫咪')

    await wait(400)
    expect(wrapper.emitted('update:model')?.length).toBe(1)
  })

  it('debounces the person input', async () => {
    const wrapper = mountFilters()
    const input = wrapper.get('[data-test="filter-person"]')

    await input.setValue('10001')
    await wait(400)

    expect(lastModel(wrapper).person).toBe('10001')
    expect(lastModel(wrapper).status).toBe('active')
  })

  it('emits every select parameter while preserving the rest of the model', async () => {
    const wrapper = mountFilters(model({ q: '猫' }))

    await wrapper.get('[data-test="filter-mode"]').setValue('semantic')
    expect(lastModel(wrapper).mode).toBe('semantic')
    expect(lastModel(wrapper).q).toBe('猫')

    await wrapper.get('[data-test="filter-category"]').setValue('fact')
    expect(lastModel(wrapper).category).toBe('fact')
    expect(lastModel(wrapper).mode).toBe('semantic')

    await wrapper.get('[data-test="filter-layer"]').setValue('episodic')
    expect(lastModel(wrapper).layer).toBe('episodic')

    await wrapper.get('[data-test="filter-status"]').setValue('archived')
    expect(lastModel(wrapper).status).toBe('archived')

    await wrapper.get('[data-test="filter-limit"]').setValue('50')
    expect(lastModel(wrapper).limit).toBe(50)

    const modes = wrapper
      .get('[data-test="filter-mode"]')
      .findAll('option')
      .map((option) => option.attributes('value'))
    expect(modes).toEqual(['hybrid', 'keyword', 'semantic'])
  })

  it('clears parameters back to undefined so the URL drops them', async () => {
    const wrapper = mountFilters(model({ q: '猫', category: 'fact' }))

    await wrapper.get('[data-test="filter-category"]').setValue('')
    expect(lastModel(wrapper).category).toBeUndefined()

    await wrapper.get('[data-test="filter-q"]').setValue('')
    await wait(400)
    expect(lastModel(wrapper).q).toBeUndefined()

    await wrapper.get('[data-test="filter-status"]').setValue('active')
    expect(lastModel(wrapper).status).toBe('active')
  })
})
