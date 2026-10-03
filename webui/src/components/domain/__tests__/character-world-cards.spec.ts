/**
 * 角色 / 世界域四张卡片（W5 §6-§16、§145）：字段、缺失兜底、stale 提示、
 * 进度条、band 中文与「不做趋势」、Expert 折叠区。无 fetch、无 store。
 */
import { enableAutoUnmount, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

import ActionCard from '@/components/domain/ActionCard.vue'
import GoalCard from '@/components/domain/GoalCard.vue'
import NeedList from '@/components/domain/NeedList.vue'
import WorldStateCard from '@/components/domain/WorldStateCard.vue'
import type { NeedFullRow, WorldData, WorldGoalRow } from '@/types/domain'

enableAutoUnmount(afterEach)

function makeWorld(overrides: Partial<WorldData> = {}): WorldData {
  return {
    phase: 'day',
    location: '客厅',
    action: {
      name: '看书',
      definition_id: 'reading',
      detail: '读一本小说',
      reason_code: 'need_relief',
      space_id: 'living_room',
      goal_id: 'g-1',
      goal_step: '读完这一章',
      progress: 0.4,
      started_at: 1_700_000_000,
      planned_end_at: 1_700_000_600,
    },
    modes: ['focus', 'home'],
    needs: { critical: [], pressing: ['hunger'], bands: { hunger: 'strong' } },
    world_revision: 12,
    cognitive_revision: 8,
    ...overrides,
  }
}

function makeNeed(overrides: Partial<NeedFullRow> = {}): NeedFullRow {
  return {
    key: 'hunger',
    label: '饥饿',
    level: 0.82,
    band: 'strong',
    growth: 0.123456,
    critical: false,
    pressing: true,
    ...overrides,
  }
}

function makeGoal(overrides: Partial<WorldGoalRow> = {}): WorldGoalRow {
  return {
    goal_id: 'g-1',
    title: '读完这本书',
    status: 'active',
    priority: 0.8,
    progress: 0.35,
    current_step: '翻到下一页',
    target_commitment: 'c-1',
    dedupe_key: 'd-1',
    ...overrides,
  }
}

describe('WorldStateCard', () => {
  it('渲染当前活动 / 地点 / phase / 模式 / pressing 需求，Expert 折叠区显示 revision', () => {
    const wrapper = mount(WorldStateCard, { props: { world: makeWorld() } })

    expect(wrapper.get('[data-test="world-action"]').text()).toBe('看书')
    expect(wrapper.get('[data-test="world-location"]').text()).toBe('客厅')
    expect(wrapper.get('[data-test="world-phase"]').text()).toBe('day')

    const modes = wrapper.findAll('[data-test="world-mode"]').map((node) => node.text())
    expect(modes).toEqual(['focus', 'home'])
    expect(wrapper.findAll('[data-test="world-pressing"]').map((node) => node.text())).toEqual([
      'hunger',
    ])

    const expert = wrapper.get('[data-test="world-revision"]').text()
    expect(expert).toContain('world_revision: 12')
    expect(expert).toContain('cognitive_revision: 8')
  })

  it('缺失字段显示「—」；世界存在但没有动作时显示「空闲」；世界为 null 全部为「—」', () => {
    const partial = mount(WorldStateCard, { props: { world: {} } })
    expect(partial.get('[data-test="world-action"]').text()).toBe('空闲')
    expect(partial.get('[data-test="world-location"]').text()).toBe('—')
    expect(partial.get('[data-test="world-phase"]').text()).toBe('—')
    expect(partial.get('[data-test="world-modes-empty"]').text()).toBe('—')
    expect(partial.get('[data-test="world-pressing-empty"]').text()).toBe('—')

    const empty = mount(WorldStateCard, { props: { world: null } })
    expect(empty.get('[data-test="world-action"]').text()).toBe('—')
    expect(empty.get('[data-test="world-location"]').text()).toBe('—')
  })

  it('stale 时显示「数据可能不是最新（实时连接已断开）」', () => {
    const wrapper = mount(WorldStateCard, { props: { world: makeWorld(), stale: true } })

    expect(wrapper.get('[data-test="world-stale"]').text()).toContain(
      '数据可能不是最新（实时连接已断开）',
    )
  })
})

describe('ActionCard', () => {
  it('渲染动作名、0-1 进度百分比条、开始/预计完成、goal/goal_step、space 与真实 ActionInstance 声明', () => {
    const wrapper = mount(ActionCard, { props: { action: makeWorld().action ?? null } })

    expect(wrapper.get('[data-test="action-name"]').text()).toBe('看书')
    expect(wrapper.get('[data-test="action-percent"]').text()).toBe('40%')
    expect(wrapper.get('[data-test="action-progress-bar"]').attributes('style')).toContain(
      'width: 40%',
    )
    expect(wrapper.get('[data-test="action-started"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="action-planned-end"]').text()).not.toBe('—')
    expect(wrapper.get('[data-test="action-goal"]').text()).toBe('g-1')
    expect(wrapper.get('[data-test="action-goal-step"]').text()).toBe('读完这一章')
    expect(wrapper.get('[data-test="action-space"]').text()).toBe('living_room')
    expect(wrapper.get('[data-test="action-instance-note"]').text()).toContain(
      '这是运行中的真实 ActionInstance，不是前端任务',
    )
  })

  it('Expert 折叠区显示 definition_id / reason_code；action 为 null 时显示空态', () => {
    const wrapper = mount(ActionCard, { props: { action: makeWorld().action ?? null } })
    const expert = wrapper.get('[data-test="action-expert"]').text()
    expect(expert).toContain('definition_id: reading')
    expect(expert).toContain('reason_code: need_relief')

    const empty = mount(ActionCard, { props: { action: null } })
    expect(empty.get('.cb-empty__title').text()).toBe('当前没有正在进行的动作')
    expect(empty.find('[data-test="action-name"]').exists()).toBe(false)
  })

  it('progress 缺失显示「—」；结构化 goal_step 也能安全降级显示', () => {
    const wrapper = mount(ActionCard, {
      props: {
        action: {
          name: '发呆',
          progress: null,
          goal_step: { description: '拿饮料' } as unknown as string,
        },
      },
    })

    expect(wrapper.get('[data-test="action-percent"]').text()).toBe('—')
    expect(wrapper.find('[data-test="action-progress-bar"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="action-goal-step"]').text()).toBe('拿饮料')
  })
})

describe('NeedList', () => {
  it('渲染 label / band 中文（strong→强烈）/ level 百分比条 / critical 与 pressing 文本标记', () => {
    const wrapper = mount(NeedList, {
      props: {
        needs: [
          makeNeed(),
          makeNeed({ key: 'sleepiness', label: '困倦', level: 1, band: 'critical', critical: true, pressing: true }),
          makeNeed({ key: 'social', label: '社交', level: 0.2, band: 'calm', critical: false, pressing: false }),
        ],
      },
    })

    const labels = wrapper.findAll('[data-test="need-label"]').map((node) => node.text())
    expect(labels).toEqual(['饥饿', '困倦', '社交'])

    const bands = wrapper.findAll('[data-test="need-band"]').map((node) => node.text())
    expect(bands[0]).toBe('强烈')
    expect(bands[1]).toBe('critical')
    expect(bands[2]).toBe('calm')

    const percents = wrapper.findAll('[data-test="need-percent"]').map((node) => node.text())
    expect(percents).toEqual(['82%', '100%', '20%'])

    expect(wrapper.findAll('[data-test="need-critical"]').length).toBe(1)
    expect(wrapper.findAll('[data-test="need-pressing"]').length).toBe(2)
  })

  it('不显示 growth / 趋势：只有当前读数与标记', () => {
    const wrapper = mount(NeedList, { props: { needs: [makeNeed()] } })

    expect(wrapper.find('[data-test="need-growth"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('0.1234')
    expect(wrapper.findAll('[data-test="need-bar"]').length).toBe(1)
  })

  it('空列表显示空态', () => {
    const wrapper = mount(NeedList, { props: { needs: [] } })
    expect(wrapper.get('.cb-empty__title').text()).toBe('暂无需求数据')
  })
})

describe('GoalCard', () => {
  it('渲染标题 / 状态中文 / 优先级 / 进度 / 当前步骤 / 关联承诺', () => {
    const wrapper = mount(GoalCard, { props: { goal: makeGoal() } })

    expect(wrapper.get('[data-test="goal-title"]').text()).toBe('读完这本书')
    expect(wrapper.get('[data-test="goal-status"]').text()).toBe('进行中')
    expect(wrapper.get('[data-test="goal-priority"]').text()).toBe('0.8')
    expect(wrapper.get('[data-test="goal-percent"]').text()).toBe('35%')
    expect(wrapper.get('[data-test="goal-bar"]').attributes('style')).toContain('width: 35%')
    expect(wrapper.get('[data-test="goal-step"]').text()).toBe('翻到下一页')
    expect(wrapper.get('[data-test="goal-commitment"]').text()).toBe('c-1')
  })

  it('Expert 折叠区显示 goal_id / dedupe_key，缺失字段显示「—」', () => {
    const wrapper = mount(GoalCard, { props: { goal: makeGoal() } })
    const expert = wrapper.get('[data-test="goal-expert"]').text()
    expect(expert).toContain('goal_id: g-1')
    expect(expert).toContain('dedupe_key: d-1')

    const sparse = mount(GoalCard, {
      props: {
        goal: makeGoal({
          status: '',
          current_step: null as unknown as string,
          target_commitment: null as unknown as string,
        }),
      },
    })
    expect(sparse.get('[data-test="goal-status"]').text()).toBe('—')
    expect(sparse.get('[data-test="goal-step"]').text()).toBe('—')
    expect(sparse.get('[data-test="goal-commitment"]').text()).toBe('—')
  })
})
