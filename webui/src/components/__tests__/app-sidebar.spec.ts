/** AppSidebar（§24-§25）：七个入口、六个「即将开放」占位、当前项与折叠模式。 */
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
  it('renders exactly the seven v1.0 entries in order', async () => {
    const { wrapper } = await mountSidebar(false)
    const labels = wrapper.findAll('.cb-sidebar__label').map((item) => item.text())
    expect(labels).toEqual(['总览', '角色', 'AI 与模型', '社交', '记忆', '媒体与能力', '系统'])
  })

  it('renders six disabled placeholders showing 即将开放 and never a dead link', async () => {
    const { wrapper } = await mountSidebar(false)
    const disabled = wrapper.findAll('button[aria-disabled="true"]')
    expect(disabled).toHaveLength(6)
    for (const item of disabled) {
      expect(item.text()).toContain('即将开放')
    }

    const links = wrapper.findAll('a')
    expect(links).toHaveLength(1)
    expect(links[0]?.attributes('href')).toBe('/')
    expect(links[0]?.attributes('aria-disabled')).toBeUndefined()
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
    expect(items).toHaveLength(7)
    for (const item of items) {
      expect(item.attributes('aria-label')).toBeTruthy()
      expect(item.attributes('title')).toBeTruthy()
    }
    expect(items[0]?.attributes('aria-label')).toBe('总览')
    expect(items[1]?.attributes('aria-label')).toBe('角色（即将开放）')
  })
})
