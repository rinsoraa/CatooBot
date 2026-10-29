/** Agent 任务详情（W5 §43）：计划/步骤/轨迹渲染 + 控制需确认 + 后端拒绝如实显示。 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import AgentTaskDetail from '@/pages/abilities/AgentTaskDetail.vue'
import { fail, installFetch, ok, type MockReply, type MockRequest } from '@/components/config/__tests__/helpers'
import { makeRouter } from '@/pages/system/__tests__/helpers'
import { useToast } from '@/composables/toast'
import type { RouteRecordRaw } from 'vue-router'

const DETAIL = {
  task: {
    task_id: 't1',
    status: 'running',
    classification: 'multi_step',
    step_count: 2,
    completed_steps: 1,
    tool_calls: 1,
    created_at: 1700000000,
    updated_at: 1700000100,
    result_summary: '已查到天气',
    error_type: '',
  },
  goal: { description: '查一下天气', session_id: 's1', user_id: 'u1', group_id: null },
  plans: [{ version: 1, status: 'active', criteria: ['需要真实数据'], steps: [{ id: 1 }] }],
  steps: [{ id: 1, status: 'done', tool: 'weather', description: '查询天气', result_summary: '晴' }],
  observations: [{ step_id: 1, status: 'ok', data: { temperature: 21 } }],
  traces: [{ id: 1, event: 'plan', detail: 'planned' }],
}

const routes: RouteRecordRaw[] = [
  { path: '/abilities/agent/tasks/:taskId', name: 'abilities-agent-task', component: { template: '<div />' } },
]

let requests: MockRequest[] = []
let pinia: Pinia

async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await flushPromises()
}

async function mountPage(): Promise<VueWrapper> {
  requests = installFetch((request): MockReply => {
    if (request.url.includes('/agent/tasks/t1/cancel')) {
      return ok({ task_id: 't1', action: 'cancel', ok: true, detail: 'cancel applied' })
    }
    if (request.url.includes('/agent/tasks/t1/pause')) {
      return fail(409, 'agent.action_failed', '任务状态不允许暂停')
    }
    if (request.url.includes('/agent/tasks/t1') && request.method === 'GET') return ok(DETAIL)
    return fail(404, 'agent.task_unknown', `unmocked ${request.url}`)
  })
  const router = await makeRouter('/abilities/agent/tasks/t1', routes)
  const wrapper = mount(AgentTaskDetail, { global: { plugins: [pinia, router] } })
  await settle()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  useToast().clear()
})

describe('AgentTaskDetail', () => {
  it('renders plan, steps, observations and traces as read-only text', async () => {
    const wrapper = await mountPage()

    expect(wrapper.get('[data-test="task-summary"]').text()).toContain('running')
    expect(wrapper.get('[data-test="task-goal"]').text()).toContain('查一下天气')
    expect(wrapper.get('[data-test="task-plan"]').text()).toContain('需要真实数据')
    expect(wrapper.get('[data-test="task-step-row"]').text()).toContain('weather')
    expect(wrapper.get('[data-test="task-observation"]').text()).toContain('temperature')
    expect(wrapper.get('[data-test="task-trace"]').text()).toContain('planned')
    expect(wrapper.findAll('[data-test^="task-control-"]')).toHaveLength(5)
  })

  it('requires a confirm before controlling a task', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="task-control-cancel"]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('终止性操作')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const cancel = requests.find((request) => request.method === 'POST')
    expect(cancel?.url).toContain('/api/v1/agent/tasks/t1/cancel')
    expect(cancel?.body).toBeNull()
  })

  it('shows the backend message when the state machine rejects an action', async () => {
    const wrapper = await mountPage()

    await wrapper.get('[data-test="task-control-pause"]').trigger('click')
    await wrapper.get('[data-test="confirm"]').trigger('click')
    await settle()

    const error = wrapper.get('[data-test="task-control-error"]')
    expect(error.text()).toContain('任务状态不允许暂停')
    expect(useToast().items.value.at(-1)?.message).toBe('暂停失败')
  })
})
