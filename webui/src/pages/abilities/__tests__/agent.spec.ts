/** Agent 页（W5 §42-§44）：disabled 文案 vs ready 任务表；模拟是干跑。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import Agent from '@/pages/abilities/Agent.vue'
import { installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { makeRouter } from '@/pages/system/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { AgentStatus } from '@/types/domain'
import type { RouteRecordRaw } from 'vue-router'

const DISABLED: AgentStatus = { status: 'disabled', active_tasks: [], recent_tasks: [] }

const READY: AgentStatus = {
  status: 'ready',
  health: { planner: 'ok', executor: 'ok' },
  active_tasks: [
    { task_id: 't1', status: 'running', goal: '查天气', updated_at: 1700000000 },
  ],
  recent_tasks: [
    { task_id: 't1', status: 'running', goal: '查天气', updated_at: 1700000000 },
    { task_id: 't2', status: 'completed', goal: '写日报', updated_at: 1699990000 },
  ],
  policy: { planner_model: 'planner-x', evaluator_model: 'eval-y', budget: { max_steps: 12 } },
  budget: { max_steps: 12, max_tool_calls: 5 },
  planner_model: 'planner-x',
  evaluator_model: 'eval-y',
}

const routes: RouteRecordRaw[] = [
  { path: '/abilities/agent', name: 'abilities-agent', component: { template: '<div />' } },
  { path: '/abilities/agent/tasks/:taskId', name: 'abilities-agent-task', component: { template: '<div />' } },
]

let requests: MockRequest[] = []
let pinia: Pinia

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(status: AgentStatus): Promise<VueWrapper> {
  requests = installFetch((request): MockReply => {
    if (request.url.includes('/api/v1/agent/simulate')) {
      return ok({ classification: 'multi_step', plan: { steps: [{ id: 1 }] }, dry_run: true })
    }
    if (request.url.endsWith('/api/v1/agent')) return ok(status)
    return { status: 404, payload: { ok: false, error: { code: 'x', message: `unmocked ${request.url}` } } }
  })
  const router = await makeRouter('/abilities/agent', routes)
  const wrapper = mount(Agent, { global: { plugins: [pinia, router] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
})

describe('Agent 页', () => {
  it('shows only the honest disabled copy without any fake tasks', async () => {
    const wrapper = await mountPage(DISABLED)

    const box = wrapper.get('[data-test="agent-disabled"]')
    expect(box.text()).toContain('尚未启用 Agent Runtime')
    expect(wrapper.get('[data-test="agent-reason"]').text()).toContain('agent.enabled=false')
    expect(wrapper.find('[data-test="agent-tasks"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="agent-recent"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="agent-simulate"]').exists()).toBe(false)
  })

  it('renders active/recent task tables, policy and links when ready', async () => {
    const wrapper = await mountPage(READY)

    expect(wrapper.get('[data-test="agent-status-badge"]').text()).toBe('已启用')
    expect(wrapper.findAll('[data-test="agent-active-row"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-test="agent-recent-row"]')).toHaveLength(2)

    const row = wrapper.get('[data-test="agent-active-row"]')
    expect(row.text()).toContain('t1')
    expect(row.text()).toContain('执行中')
    expect(row.text()).toContain('查天气')
    expect(row.get('[data-test="agent-task-link"]').attributes('href')).toBe('/abilities/agent/tasks/t1')

    expect(wrapper.get('[data-test="agent-policy"]').text()).toContain('planner-x')
    expect(wrapper.get('[data-test="agent-policy"]').text()).toContain('max_steps')
  })

  it('runs the simulator as a declared dry run', async () => {
    const wrapper = await mountPage(READY)

    expect(wrapper.get('[data-test="agent-simulate"]').text()).toContain('不执行工具')
    expect(wrapper.get('[data-test="agent-simulate"]').text()).toContain('干跑')

    await wrapper.get('[data-test="agent-simulate-input"]').setValue('帮我查一下明天的天气')
    await wrapper.get('[data-test="agent-simulate-form"]').trigger('submit')
    await settle()

    const simulate = requests.find((request) => request.url.includes('/agent/simulate'))
    expect(simulate?.method).toBe('POST')
    expect(simulate?.body).toEqual({ text: '帮我查一下明天的天气' })
    expect(wrapper.get('[data-test="agent-simulate-result"]').text()).toContain('multi_step')
  })
})
