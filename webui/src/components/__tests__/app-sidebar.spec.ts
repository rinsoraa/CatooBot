/** AppSidebar（§24-§25 / W4 / Minecraft Phase 1）：八个入口全为真实路由，顺序与信息架构一致。 */
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import AppSidebar from '@/components/AppSidebar.vue'

async function mountSidebar(collapsed: boolean) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' }, meta: { title: '总览' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const wrapper = mount(AppSidebar, {
    props: { collapsed },
    global: { plugins: [router] },
  })
  return { wrapper, router }
}

describe('AppSidebar', () => {
  it('renders exactly the v1.0 entries in order', async () => {
    const { wrapper } = await mountSidebar(false)
    const labels = wrapper.findAll('.cb-sidebar__label').map((item) => item.text())
    expect(labels).toEqual([
      '总览',
      '角色',
      'AI 与模型',
      '社交',
      '记忆',
      '媒体与能力',
      'Minecraft',
      '系统',
    ])
  })

  it('links every entry to a real v1 route after W5', async () => {
    const { wrapper } = await mountSidebar(false)
    // W5 wired the remaining domains: no 「即将开放」 placeholders are left.
    expect(wrapper.findAll('button[aria-disabled="true"]')).toHaveLength(0)

    const links = wrapper.findAll('a')
    // 信息架构顺序：总览 → 角色 → AI → 社交 → 记忆 → 能力 → Minecraft → 系统
    expect(links.map((link) => link.attributes('href'))).toEqual([
      '/',
      '/character',
      '/ai',
      '/social',
      '/memory',
      '/abilities/tools',
      '/minecraft',
      '/system/settings',
    ])
    for (const link of links) {
      expect(link.attributes('aria-disabled')).toBeUndefined()
    }
  })

  it('marks the active route with aria-current=page', async () => {
    const { wrapper } = await mountSidebar(false)
    const current = wrapper.get('a[aria-current="page"]')
    expect(current.text()).toContain('总览')
    expect(wrapper.findAll('[aria-current="page"]')).toHaveLength(1)
  })

  it('keeps accessible names in collapsed mode', async () => {
    const { wrapper } = await mountSidebar(true)
    expect(wrapper.findAll('.cb-sidebar__label')).toHaveLength(0)
    expect(wrapper.findAll('.cb-sidebar__soon')).toHaveLength(0)

    const items = wrapper.findAll('.cb-sidebar__item')
    expect(items).toHaveLength(8)
    for (const item of items) {
      expect(item.attributes('aria-label')).toBeTruthy()
      expect(item.attributes('title')).toBeTruthy()
    }
    expect(items[0]?.attributes('aria-label')).toBe('总览')
    // 所有入口都是真实路由，因此可访问名就是入口名（不再有「即将开放」后缀）
    expect(items[1]?.attributes('aria-label')).toBe('角色')
  })
})
