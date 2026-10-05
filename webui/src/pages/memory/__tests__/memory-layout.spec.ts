/** 记忆外壳（W5 §26）：唯一 h1 + 四个 tab + RouterView。 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { describe, expect, it } from 'vitest'

import MemoryLayout from '@/pages/memory/MemoryLayout.vue'
import { flushAll } from '@/components/config/__tests__/helpers'

const Child = defineComponent({ template: '<p data-test="memory-child">浏览子页面</p>' })
const Host = defineComponent({
  components: { RouterView },
  template: '<RouterView />',
})

describe('MemoryLayout', () => {
  it('renders a single h1 记忆, the four tabs and the child route', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/memory',
          component: MemoryLayout,
          children: [
            { path: '', name: 'memory', component: Child },
            { path: 'timeline', name: 'memory-timeline', component: Child },
            { path: 'health', name: 'memory-health', component: Child },
            { path: 'ops', name: 'memory-ops', component: Child },
          ],
        },
      ],
    })
    await router.push('/memory')
    await router.isReady()

    const wrapper = mount(Host, { global: { plugins: [router] } })
    await flushAll()

    const headings = wrapper.findAll('h1')
    expect(headings).toHaveLength(1)
    expect(headings[0]?.text()).toBe('记忆')

    expect(wrapper.get('[data-test="memory-tab-browse"]').attributes('href')).toBe('/memory')
    expect(wrapper.get('[data-test="memory-tab-timeline"]').attributes('href')).toBe(
      '/memory/timeline',
    )
    expect(wrapper.get('[data-test="memory-tab-health"]').attributes('href')).toBe('/memory/health')
    expect(wrapper.get('[data-test="memory-tab-ops"]').attributes('href')).toBe('/memory/ops')
    expect(wrapper.get('[data-test="memory-child"]').text()).toBe('浏览子页面')
  })
})
