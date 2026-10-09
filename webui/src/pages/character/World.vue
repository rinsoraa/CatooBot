<script setup lang="ts">
/**
 * 世界页（W5 §10-§16、§62、§67-§75、§145）。
 *
 * 快照来自 world store（首屏 REST + realtime 变更驱动重新拉取，绝不轮询）；
 * 默认不展示大块 JSON：原始结构只在 Expert 折叠区里，且必须由 API 真实返回。
 * 管理员操作会影响运行中的世界：每个动作都先 ConfirmDialog 二次确认，
 * reset / reinitialize 明确标注不可撤销；失败时展示后端 message。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { NInput, NSelect } from 'naive-ui'

import { behaviorApi } from '@/api/behavior'
import { worldApi } from '@/api/world'
import { errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ActionCard from '@/components/domain/ActionCard.vue'
import GoalCard from '@/components/domain/GoalCard.vue'
import NeedList from '@/components/domain/NeedList.vue'
import WorldStateCard from '@/components/domain/WorldStateCard.vue'
import { toast } from '@/composables/toast'
import { useWorldStore } from '@/stores/world'
import type {
  WorldActivityAdvisorView,
  WorldInitiativeView,
  WorldActivityPlanView,
  WorldActivityView,
} from '@/types/domain'
import type {
  BehaviorPreviewInput,
  BehaviorPreviewResult,
  BehaviorTestResponseResult,
  BehaviorTriggerAction,
} from '@/types/behavior'

const store = useWorldStore()

// Phase 6A §三十六：当前活动（Activity Episode）的**只读**投影。
// 这一页不提供 start / cancel / extend —— WebUI 不得修改 Episode（§三十七）。
const activity = ref<WorldActivityView | null>(null)
const activityError = ref('')

async function loadActivity(): Promise<void> {
  try {
    activity.value = await worldApi.activity(10)
    activityError.value = ''
  } catch (caught) {
    activityError.value = errorMessage(caught)
  }
}

function activityDuration(row: WorldActivityView['recent'][number]): string {
  const start = Number(row.started_at || 0)
  const end = Number(row.ended_at || 0) || Number(row.planned_end_at || 0)
  if (!start || !end || end <= start) return '—'
  const minutes = Math.round((end - start) / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${minutes % 60} 分钟`
}

function elapsedText(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}

function windowText(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '无窗口'
  return `计划结束前 ${Math.round(seconds / 60)} 分钟`
}

function activityClock(seconds: number): string {
  if (!seconds) return '—'
  return new Date(seconds * 1000).toLocaleTimeString()
}

// Phase 6C §五十八：计划（Rolling Horizon）的**只读**视图。
// 计划是"打算"，不是"现状"——页面上必须与"当前活动"分开显示，且没有任何强制选择入口。
const plan = ref<WorldActivityPlanView | null>(null)
const planError = ref('')

async function loadPlan(): Promise<void> {
  try {
    plan.value = await worldApi.activityPlan()
    planError.value = ''
  } catch (caught) {
    planError.value = errorMessage(caught)
  }
}

function horizonText(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  return `未来 ${Math.round(seconds / 60)} 分钟`
}

function minutesText(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '—'
  return `${Math.max(1, Math.round(seconds / 60))} 分钟`
}

function agoText(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '刚刚'
  return `${Math.round(seconds / 60)} 分钟前`
}

function breakdownText(breakdown: Record<string, number> | undefined): string {
  if (!breakdown) return '—'
  return Object.entries(breakdown)
    .filter(([, value]) => value !== 0)
    .map(([key, value]) => `${key} ${value.toFixed(2)}`)
    .join('、')
}

function anchorLabel(anchor: {
  anchor_id: string
  activity: string
  target_time: string
  hard?: boolean
  phase?: string
}): string {
  const hardness = anchor.hard ? '硬' : '软'
  const phase = anchor.phase ? `/${anchor.phase}` : ''
  return `${anchor.anchor_id} ${anchor.target_time} ${anchor.activity}(${hardness}${phase})`
}

/**
 * Phase 6C.1：计划与现实的三种状态。
 * `dirty` 表示"Episode 被延长过，计划边界还没对齐"（通常是在等刷新冷却）。
 */
function planStateText(view: { stale?: boolean; dirty?: boolean }): string {
  if (view.stale) return '计划已过期'
  if (view.dirty) return '待对齐（延长后等冷却）'
  return '计划有效'
}

// Phase 6D §八十九/§九十：模型顾问的**只读**回执（没有"让模型再想一次"这种入口）。
const advisor = ref<WorldActivityAdvisorView | null>(null)
const advisorError = ref('')

async function loadAdvisor(): Promise<void> {
  try {
    advisor.value = await worldApi.activityAdvisor()
    advisorError.value = ''
  } catch (caught) {
    advisorError.value = errorMessage(caught)
  }
}

function advisorStateText(view: WorldActivityAdvisorView | null): string {
  if (!view || !view.enabled) return '未启用（纯规则）'
  return view.available ? '已启用' : '已启用但不可用（退回规则）'
}

// Phase 7A §四十九：Initiative / LifeIntent 的**只读**视图（执行层 NONE）。
// 界面上**没有** Execute / Send / Confirm / Run / Force —— 一个都不给。
const initiative = ref<WorldInitiativeView | null>(null)
const initiativeError = ref('')

async function loadInitiative(): Promise<void> {
  try {
    initiative.value = await worldApi.worldInitiative()
    initiativeError.value = ''
  } catch (caught) {
    initiativeError.value = errorMessage(caught)
  }
}

function initiativeStateText(view: WorldInitiativeView | null): string {
  if (!view || !view.enabled) return '未启用'
  if (view.degraded) return `降级（${view.degraded}）`
  return '运行中（只产生意图）'
}

function intentTime(value: number): string {
  if (!value) return '—'
  return new Date(value * 1000).toLocaleString()
}

function cooldownText(view: WorldInitiativeView | null): string {
  const cooldown = view?.cooldown
  if (!cooldown) return '—'
  const remain = Math.max(0, Math.round(cooldown.seconds_remaining || 0))
  return `冷却 ${cooldown.minutes} 分钟 · 剩余 ${remain} 秒 · 本小时 ${cooldown.proposals_last_hour}/${cooldown.max_proposals_per_hour}`
}

function receiptText(view: WorldActivityAdvisorView | null): string {
  const receipt = view?.last_receipt
  if (!receipt || !receipt.attempted) return '还没问过'
  const proposal = receipt.proposal?.decision || '-'
  if (receipt.accepted) return `${proposal} → 已采纳`
  if (receipt.fallback_used) return `${proposal} → 规则回退（${receipt.rejection_reason || receipt.failure || '?'}）`
  return proposal
}

onMounted(() => {
  if (!store.world && !store.loading) void store.loadWorld()
  void loadActivity()
  void loadPlan()
  void loadAdvisor()
  void loadInitiative()
})

function reload(): void {
  void store.loadWorld()
}

// ---------------------------------------------------------------- 安全文本

function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    const parts = value.map((item) => text(item, '')).filter((part) => part !== '')
    return parts.length > 0 ? parts.join('、') : fallback
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['label', 'name', 'key', 'id']) {
      const candidate = record[key]
      if (candidate !== undefined && candidate !== null && candidate !== '') {
        return text(candidate, fallback)
      }
    }
  }
  return fallback
}

function fractionText(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`
}

// ---------------------------------------------------------------- 数据投影

interface FactRow {
  label: string
  value: string
}

interface SpaceRow {
  id: string
  name: string
  kind: string
}

const spaceRows = computed<SpaceRow[]>(() =>
  (store.world?.spaces ?? []).map((space) => {
    const record = space as unknown as Record<string, unknown>
    return {
      id: text(record.space_id ?? record.id, ''),
      name: text(record.name, '未命名空间'),
      kind: text(record.kind, ''),
    }
  }),
)

const objectRows = computed<FactRow[]>(() =>
  (store.world?.objects ?? []).map((raw) => {
    const record = raw as Record<string, unknown>
    return {
      label: text(record.name, '未命名物件'),
      value: [text(record.kind, ''), text(record.space_id, '')]
        .filter((part) => part !== '')
        .join(' · ') || '—',
    }
  }),
)

function itemsText(items: unknown): string {
  if (items === null || items === undefined) return '—'
  if (typeof items === 'object' && !Array.isArray(items)) {
    const entries = Object.entries(items as Record<string, unknown>)
    if (entries.length === 0) return '空'
    return entries.map(([name, count]) => `${name}×${text(count)}`).join('、')
  }
  return text(items)
}

const inventoryRows = computed<FactRow[]>(() =>
  Object.entries(store.world?.inventories ?? {}).map(([key, raw]) => {
    const record = (raw ?? {}) as Record<string, unknown>
    const capacity = record.capacity
    return {
      label: text(record.name, key),
      value: `${itemsText(record.items)}${
        typeof capacity === 'number' && capacity > 0 ? ` · 容量 ${capacity}` : ' · 容量不限'
      }`,
    }
  }),
)

const pet = computed<Record<string, unknown> | null>(() => {
  const raw = store.world?.pet
  return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null
})

const petRows = computed<FactRow[]>(() => {
  const record = pet.value
  if (!record) return []
  return [
    { label: '名字', value: text(record.name) },
    { label: '种类', value: text(record.species) },
    { label: '活动', value: text(record.activity) },
    { label: '地点', value: text(record.location) },
    { label: '心情', value: text(record.mood) },
    { label: '饥饿', value: fractionText(record.hunger) },
    { label: '精力', value: fractionText(record.energy) },
    { label: '亲密度', value: fractionText(record.affection) },
  ]
})

const petLine = computed(() => (typeof pet.value?.line === 'string' ? pet.value.line : ''))

const PRESENCE_LABELS: Record<string, string> = {
  active: '在场',
  lurking: '潜水',
  offline: '离线',
}

const socialSpaceRows = computed<FactRow[]>(() =>
  (store.world?.social_spaces ?? []).map((raw) => {
    const record = raw as Record<string, unknown>
    const presence = text(record.character_presence, '')
    const participants = record.participants
    return {
      label: text(record.name, '未命名空间'),
      value: [
        text(record.kind, ''),
        presence ? (PRESENCE_LABELS[presence] ?? presence) : '',
        Array.isArray(participants) ? `${participants.length} 人` : '',
      ]
        .filter((part) => part !== '')
        .join(' · ') || '—',
    }
  }),
)

const goals = computed(() => {
  const full = store.world?.goals_full
  if (full && full.length > 0) return full
  return store.world?.goals ?? []
})

const interrupted = computed(() => store.world?.interrupted ?? null)
const interruptedPercent = computed<number | null>(() => {
  const raw = interrupted.value?.progress
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return Math.round(Math.min(1, Math.max(0, raw)) * 100)
})
const interruptedRemaining = computed(() => {
  const minutes = interrupted.value?.remaining_minutes
  if (typeof minutes !== 'number' || !Number.isFinite(minutes)) return '—'
  return `${minutes} 分钟`
})

const rawJson = computed(() => (store.world ? JSON.stringify(store.world, null, 2) : ''))

// ------------------------------------------------------------ 管理员操作

type ControlAction = 'pause' | 'resume' | 'reset' | 'reinitialize'

interface ControlMeta {
  label: string
  title: string
  message: string
  detail: string
  confirmText: string
  danger: boolean
}

const CONTROL_META: Record<ControlAction, ControlMeta> = {
  pause: {
    label: '暂停',
    title: '暂停世界',
    message: '确定暂停沙盒世界吗？',
    detail: '暂停后调度不再推进；可以随时恢复。',
    confirmText: '暂停',
    danger: false,
  },
  resume: {
    label: '恢复',
    title: '恢复世界',
    message: '确定恢复沙盒世界吗？',
    detail: '恢复后调度继续推进，世界从暂停点接着运行。',
    confirmText: '恢复',
    danger: false,
  },
  reset: {
    label: '重置',
    title: '重置世界',
    message: '确定重置沙盒世界吗？',
    detail: '重置不可撤销：世界回到初始种子状态，运行中的动作与进度都会丢失。',
    confirmText: '重置',
    danger: true,
  },
  reinitialize: {
    label: '重新初始化',
    title: '重新初始化世界',
    message: '确定重新初始化沙盒世界吗？',
    detail: '重新初始化不可撤销：会按当前种子重建世界，运行期状态全部清空。',
    confirmText: '重新初始化',
    danger: true,
  },
}

const CONTROL_ACTIONS = Object.keys(CONTROL_META) as ControlAction[]

const pending = ref<ControlAction | null>(null)
const controlError = ref('')

function askControl(control: ControlAction): void {
  controlError.value = ''
  pending.value = control
}

async function confirmControl(): Promise<void> {
  const control = pending.value
  pending.value = null
  if (control === null) return
  const ok = await store.control(control, control)
  if (ok) {
    toast.success(`已执行「${CONTROL_META[control].label}」`)
  } else {
    controlError.value = store.error
    toast.error(store.error || '操作失败')
  }
}

// ------------------------------------------------------------ 行为调试
/**
 * v0.8「沙盒 · 对话行为」迁移：试跑回复 / 手动触发 / 行为模拟器。
 * 三个接口都是 dry-run（永不发送 QQ 消息）；reset_state 会重置叙事状态，
 * 因此单独走 ConfirmDialog。
 */

const behaviorText = ref('')
const behaviorRunning = ref(false)
const behaviorResult = ref<BehaviorTestResponseResult | null>(null)
const behaviorError = ref('')

async function runTestResponse(): Promise<void> {
  const text = behaviorText.value.trim()
  behaviorError.value = ''
  if (!text) {
    toast.warning('请先输入要测试的内容')
    return
  }
  if (behaviorRunning.value) return
  behaviorRunning.value = true
  behaviorResult.value = null
  try {
    behaviorResult.value = await behaviorApi.testResponse(text)
  } catch (caught) {
    // 后端 message 原样展示（如 behavior.text_required）。
    behaviorError.value = errorMessage(caught)
    toast.error('试跑失败', behaviorError.value)
  } finally {
    behaviorRunning.value = false
  }
}

function behaviorStateText(state: Record<string, unknown> | undefined): string {
  if (!state) return '—'
  const parts: string[] = []
  if (state.mood !== undefined && state.mood !== null && state.mood !== '') {
    parts.push(`mood=${String(state.mood)}`)
  }
  if (state.activity !== undefined && state.activity !== null && state.activity !== '') {
    parts.push(`activity=${String(state.activity)}`)
  }
  if (typeof state.energy === 'number') {
    parts.push(`energy=${Math.round(Math.min(1, Math.max(0, state.energy)) * 100)}%`)
  }
  return parts.length > 0 ? parts.join(' · ') : '—'
}

// ---- 手动触发

interface TriggerMeta {
  label: string
  title: string
  message: string
  detail: string
  confirmText: string
  danger: boolean
}

const TRIGGER_META: Record<BehaviorTriggerAction, TriggerMeta> = {
  mood_up: {
    label: '心情 +1',
    title: '上调心情',
    message: '确定手动上调一次心情吗？',
    detail: '会立刻写回运行中的叙事状态（mood）。',
    confirmText: '上调',
    danger: false,
  },
  mood_down: {
    label: '心情 -1',
    title: '下调心情',
    message: '确定手动下调一次心情吗？',
    detail: '会立刻写回运行中的叙事状态（mood）。',
    confirmText: '下调',
    danger: false,
  },
  reset_state: {
    label: '重置状态',
    title: '重置角色状态',
    message: '确定重置角色的叙事状态吗？',
    detail: '重置不可撤销：mood / energy / activity 等会回到初始值。',
    confirmText: '重置',
    danger: true,
  },
  test_initiative: {
    label: '测试主动搭话',
    title: '测试主动搭话',
    message: '确定运行一次主动搭话判定吗？',
    detail: '只做 dry-run 判定，不会真的发送 QQ 消息。',
    confirmText: '运行',
    danger: false,
  },
}

const TRIGGER_ACTIONS = Object.keys(TRIGGER_META) as BehaviorTriggerAction[]

const triggerBusy = ref<BehaviorTriggerAction | ''>('')
const triggerResult = ref('')
const triggerError = ref('')
const pendingTrigger = ref<BehaviorTriggerAction | null>(null)

function describeTriggerResult(value: unknown): string {
  if (value === null || value === undefined) return '（无返回）'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    if ('would_consider' in record) {
      return [
        `会考虑主动搭话：${record.would_consider ? '是' : '否'}`,
        `概率：${String(record.probability ?? '—')}`,
        record.blocked_by ? `受阻：${String(record.blocked_by)}` : '',
      ]
        .filter((part) => part !== '')
        .join(' · ')
    }
    if (record.state && typeof record.state === 'object') {
      return behaviorStateText(record.state as Record<string, unknown>)
    }
    return JSON.stringify(value)
  }
  return String(value)
}

function askTrigger(action: BehaviorTriggerAction): void {
  triggerError.value = ''
  if (action === 'reset_state') {
    pendingTrigger.value = action
    return
  }
  void runTrigger(action)
}

async function runTrigger(action: BehaviorTriggerAction): Promise<void> {
  if (triggerBusy.value !== '') return
  triggerBusy.value = action
  triggerError.value = ''
  try {
    const data = await behaviorApi.trigger(action)
    triggerResult.value = describeTriggerResult(data.result)
    toast.success(`已执行「${TRIGGER_META[action].label}」`)
  } catch (caught) {
    triggerError.value = errorMessage(caught)
    toast.error('触发失败', triggerError.value)
  } finally {
    triggerBusy.value = ''
  }
}

async function confirmTrigger(): Promise<void> {
  const action = pendingTrigger.value
  pendingTrigger.value = null
  if (action === null) return
  await runTrigger(action)
}

// ---- 行为模拟器

const RELATIONSHIP_OPTIONS = [
  { label: '陌生 stranger', value: 'stranger' },
  { label: '认识 acquaintance', value: 'acquaintance' },
  { label: '熟悉 familiar', value: 'familiar' },
  { label: '朋友 friend', value: 'friend' },
  { label: '亲密 close_friend', value: 'close_friend' },
]

const previewDraft = reactive({
  simTime: '',
  mood: '',
  activity: '',
  relationship: 'familiar',
  topic: '',
  sampleReply: '',
})

const previewRunning = ref(false)
const previewResult = ref<BehaviorPreviewResult | null>(null)
const previewError = ref('')

function buildPreviewInput(): BehaviorPreviewInput {
  const input: BehaviorPreviewInput = {}
  const simTime = previewDraft.simTime.trim()
  if (simTime) input.sim_time = simTime
  const mood = previewDraft.mood.trim()
  if (mood) input.mood = mood
  const activity = previewDraft.activity.trim()
  if (activity) input.activity = activity
  if (previewDraft.relationship) input.relationship = previewDraft.relationship
  const topic = previewDraft.topic.trim()
  if (topic) input.topic = topic
  const sampleReply = previewDraft.sampleReply.trim()
  if (sampleReply) input.sample_reply = sampleReply
  return input
}

async function runPreview(): Promise<void> {
  if (previewRunning.value) return
  previewRunning.value = true
  previewError.value = ''
  previewResult.value = null
  try {
    previewResult.value = await behaviorApi.preview(buildPreviewInput())
  } catch (caught) {
    previewError.value = errorMessage(caught)
    toast.error('模拟失败', previewError.value)
  } finally {
    previewRunning.value = false
  }
}

function previewMoodText(): string {
  const state = previewResult.value?.state
  if (!state) return '—'
  return behaviorStateText(state)
}
</script>

<template>
  <div class="cb-world" data-test="world-page">
    <ErrorState v-if="store.error" :message="store.error" @retry="reload" />

    <LoadingState v-if="store.loading && !store.world" label="正在读取世界状态…" :rows="4" />

    <template v-else-if="store.world">
      <div class="cb-world__grid">
        <WorldStateCard :world="store.world" :stale="store.stale" />
        <ActionCard :action="store.world.action ?? null" />
      </div>

      <NeedList :needs="store.world.needs_full ?? []" />

      <section class="cb-card cb-world__section" data-test="world-activity">
        <SectionHeader
          title="当前活动（Phase 6A）"
          description="她现在正在做什么来自 ActivityEpisode（唯一事实来源）。这里**只读**：不提供开始 / 取消 / 延长，也不会驱动任何 Minecraft 动作。"
        />
        <p v-if="activityError" class="cb-world__readonly" data-test="world-activity-error">
          {{ activityError }}
        </p>
        <template v-else-if="activity && activity.enabled && activity.current">
          <dl class="cb-world__facts" data-test="world-activity-facts">
            <div>
              <dt>Episode</dt>
              <dd data-test="world-activity-id">{{ activity.current.episode_id }}</dd>
            </div>
            <div>
              <dt>Activity</dt>
              <dd data-test="world-activity-name">
                {{ activity.current.activity_name }}（{{ activity.current.activity_type }}）
              </dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd data-test="world-activity-status">{{ activity.current.status }}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd data-test="world-activity-started">{{ activityClock(activity.current.started_at) }}</dd>
            </div>
            <div>
              <dt>Planned End</dt>
              <dd data-test="world-activity-planned">
                {{ activityClock(activity.current.planned_end_at) }}
              </dd>
            </div>
            <div>
              <dt>Duration</dt>
              <dd data-test="world-activity-duration">{{ activityDuration(activity.current) }}</dd>
            </div>
            <div>
              <dt>Source</dt>
              <dd data-test="world-activity-source">{{ activity.current.source }}</dd>
            </div>
            <div>
              <dt>Related Task</dt>
              <dd data-test="world-activity-task">{{ text(activity.current.related_task_id) }}</dd>
            </div>
            <div>
              <dt>Transition Reason</dt>
              <dd data-test="world-activity-reason">
                {{ text(activity.current.transition_reason) }}
              </dd>
            </div>
            <div>
              <dt>Extensions</dt>
              <dd data-test="world-activity-extensions">{{ activity.current.extension_count }}</dd>
            </div>
            <div>
              <dt>Elapsed</dt>
              <dd data-test="world-activity-elapsed">{{ elapsedText(activity.decision?.elapsed_seconds) }}</dd>
            </div>
            <div>
              <dt>Transition Window</dt>
              <dd data-test="world-activity-window">
                {{ windowText(activity.decision?.transition_window_seconds) }}
                （{{ activity.decision?.transition_pending ? '已进入，待命' : '未进入' }}）
              </dd>
            </div>
            <div>
              <dt>Decision</dt>
              <dd data-test="world-activity-decision">
                {{ activity.decision?.last_decision?.decision || '（还没做过决策）' }}
              </dd>
            </div>
            <div>
              <dt>Reason</dt>
              <dd data-test="world-activity-decision-reason">
                {{ activity.decision?.last_decision?.reason_code || '—' }}
              </dd>
            </div>
            <div>
              <dt>Next Hint</dt>
              <dd data-test="world-activity-next-hint">
                {{ activity.decision?.last_decision?.next_activity_hint || '—' }}
              </dd>
            </div>
            <div>
              <dt>Extensions Allowed</dt>
              <dd data-test="world-activity-max-extensions">
                {{ activity.decision?.extension_count ?? 0 }} / {{ activity.decision?.max_extensions ?? 0 }}
              </dd>
            </div>
            <div>
              <dt>Consistency</dt>
              <dd data-test="world-activity-consistency">
                {{
                  activity.consistency
                    ? activity.consistency.ok
                      ? 'OK'
                      : `ERROR（${activity.consistency.errors.length}）`
                    : '—'
                }}
              </dd>
            </div>
          </dl>
          <p v-if="activity.degraded" class="cb-world__readonly" data-test="world-activity-degraded">
            活动层当前**降级**：{{ activity.degraded }}
          </p>
        </template>
        <p v-else class="cb-world__readonly" data-test="world-activity-empty">
          现在没有活动片段（没有 Episode 是合法状态 —— 她"还没开始做什么"）。
        </p>

        <template v-if="activity && activity.recent.length > 0">
          <h4 class="cb-world__subtitle">最近的活动（最多 10 条）</h4>
          <table class="cb-world__table" data-test="world-activity-recent">
            <thead>
              <tr>
                <th>Episode</th>
                <th>Activity</th>
                <th>Status</th>
                <th>Source</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in activity.recent" :key="row.episode_id">
                <td>{{ row.episode_id }}</td>
                <td>{{ row.activity_name }}</td>
                <td>{{ row.status }}</td>
                <td>{{ row.source }}</td>
                <td>{{ text(row.transition_reason) }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </section>

      <!-- Phase 6C §五十八：Planner Debug —— **只读**。计划是"打算"，不是"现状"；
           这里没有 force select，也没有重排按钮（那只有 Runtime 按 §八 的触发点才做）。 -->
      <section class="cb-card cb-world__section" data-test="world-plan">
        <SectionHeader
          title="接下来的打算（计划，不是现状）"
          description="Rolling horizon 的只读视图：候选、被拒原因、分数与锚点；不改任何东西"
        />
        <p v-if="planError" class="cb-world__readonly" data-test="world-plan-error">
          计划读取失败：{{ planError }}
        </p>
        <template v-if="plan && plan.plan">
          <dl class="cb-world__facts" data-test="world-plan-facts">
            <div>
              <dt>Plan</dt>
              <dd data-test="world-plan-id">{{ plan.plan.plan_id }}</dd>
            </div>
            <div>
              <dt>版本</dt>
              <dd data-test="world-plan-version">v{{ plan.plan_version }}</dd>
            </div>
            <div>
              <dt>视野</dt>
              <dd data-test="world-plan-horizon">{{ horizonText(plan.planning_horizon_seconds) }}</dd>
            </div>
            <div>
              <dt>来源 / 触发</dt>
              <dd data-test="world-plan-source">{{ plan.source }} · {{ text(plan.trigger) }}</dd>
            </div>
            <div>
              <dt>下一步</dt>
              <dd data-test="world-plan-next">
                {{ plan.next ? plan.next.activity : '—' }}
              </dd>
            </div>
            <div>
              <dt>被选中</dt>
              <dd data-test="world-plan-selected">
                {{ plan.selected ? `${plan.selected.activity} ${plan.selected.score.toFixed(2)}` : '—' }}
              </dd>
            </div>
            <div>
              <dt>刷新</dt>
              <dd data-test="world-plan-refresh">
                共 {{ plan.refresh_count }} 次 · 上次 {{ agoText(plan.seconds_since_last_refresh) }}
                （{{ text(plan.last_result?.reason) }}）
              </dd>
            </div>
            <div>
              <dt>一致性</dt>
              <!-- Phase 6C.1：计划与现状是否对齐（Episode 被延长后可能先"待对齐"等冷却） -->
              <dd data-test="world-plan-stale">{{ planStateText(plan) }}</dd>
            </div>
          </dl>

          <h4 class="cb-world__subtitle">未来安排（最多 {{ plan.upcoming.length }} 条）</h4>
          <table class="cb-world__table" data-test="world-plan-upcoming">
            <thead>
              <tr>
                <th>开始</th>
                <th>Activity</th>
                <th>时长</th>
                <th>理由</th>
                <th>锚点 / 目标</th>
                <th>意图</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in plan.upcoming" :key="`${index}-${row.activity}`">
                <td>{{ activityClock(row.planned_start) }}</td>
                <td>{{ row.activity }}</td>
                <td>{{ minutesText(row.duration) }}</td>
                <td>{{ row.reason }}</td>
                <td>{{ text(row.anchor_id) }} {{ text(row.goal_id) }}</td>
                <td data-test="world-plan-item-intent">{{ text(row.intent_id) }}</td>
              </tr>
            </tbody>
          </table>

          <h4 class="cb-world__subtitle">候选与打分（{{ plan.candidates.length }} 个可用）</h4>
          <table class="cb-world__table" data-test="world-plan-candidates">
            <thead>
              <tr>
                <th>Activity</th>
                <th>Score</th>
                <th>锚点</th>
                <th>目标</th>
                <th>意图</th>
                <th>分项</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in plan.candidates" :key="`ok-${row.activity}`">
                <td>{{ row.activity }}</td>
                <td>{{ row.score.toFixed(2) }}</td>
                <td>{{ text(row.anchor_id) }}</td>
                <td>{{ text(row.goal_id) }}</td>
                <td data-test="world-plan-candidate-intent">{{ text(row.intent_id) }}</td>
                <td class="cb-world__readonly">{{ breakdownText(row.breakdown) }}</td>
              </tr>
            </tbody>
          </table>

          <h4 class="cb-world__subtitle">被拒的候选（{{ plan.rejected.length }} 个）</h4>
          <table class="cb-world__table" data-test="world-plan-rejected">
            <thead>
              <tr>
                <th>Activity</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in plan.rejected" :key="`no-${row.activity}`">
                <td>{{ row.activity }}</td>
                <td>{{ row.reason }}</td>
              </tr>
            </tbody>
          </table>

          <p v-if="plan.anchors && plan.anchors.length > 0" class="cb-world__readonly">
            <span data-test="world-plan-anchors">
              锚点：{{ plan.anchors.map((anchor) => anchorLabel(anchor)).join(' · ') }}
            </span>
          </p>
          <p class="cb-world__readonly" data-test="world-plan-readonly">
            只读视图：计划由运行时按触发点重排，这里既不能强制选择，也不会产生任何世界动作。
          </p>
        </template>
        <p v-else class="cb-world__readonly" data-test="world-plan-empty">
          现在没有生效计划（她还没排过未来一段，或活动层未启用）。
        </p>
      </section>

      <!-- Phase 6D §九十：模型顾问（**只读**）—— 没有强制采纳 / 否决 / 再问一次 -->
      <section class="cb-card cb-world__section" data-test="world-advisor">
        <SectionHeader
          title="模型顾问（软判断的参谋）"
          description="规则永远优先：顾问只能建议，越权一律被规则拒绝并回退"
        />
        <p v-if="advisorError" class="cb-world__readonly" data-test="world-advisor-error">
          顾问状态读取失败：{{ advisorError }}
        </p>
        <dl v-if="advisor" class="cb-world__facts" data-test="world-advisor-facts">
          <div>
            <dt>状态</dt>
            <dd data-test="world-advisor-enabled">{{ advisorStateText(advisor) }}</dd>
          </div>
          <div>
            <dt>Provider / 模型</dt>
            <dd data-test="world-advisor-model">
              {{ text(advisor.provider) }} / {{ text(advisor.model) }}
            </dd>
          </div>
          <div>
            <dt>超时</dt>
            <dd data-test="world-advisor-timeout">{{ advisor.timeout_ms }} ms</dd>
          </div>
          <div>
            <dt>最近一次</dt>
            <dd data-test="world-advisor-receipt">{{ receiptText(advisor) }}</dd>
          </div>
          <div>
            <dt>延迟 / 调用数</dt>
            <dd data-test="world-advisor-latency">
              {{ advisor.last_latency_ms }} ms · 共 {{ advisor.calls }} 次
            </dd>
          </div>
          <div>
            <dt>已问过的 cycle</dt>
            <dd data-test="world-advisor-cycles">{{ advisor.attempted_cycles }}</dd>
          </div>
        </dl>
        <p class="cb-world__readonly" data-test="world-advisor-readonly">
          只读：模型只给「继续 / 延长 / 切换」的建议，硬约束（最短最长时长、锚点、撞车、候选资格）
          一律由规则校验；这里不能强制采纳，也不能让模型再想一次。
        </p>
      </section>

      <!-- Phase 7A §四十九：意图（LifeIntent）**只读** —— 执行层 NONE，没有任何执行入口 -->
      <section class="cb-card cb-world__section" data-test="world-initiative">
        <SectionHeader
          title="意图（她想去做什么）"
          description="只是念头：既不是现状，也不是计划，更不能被执行（执行层 NONE）"
        />
        <p v-if="initiativeError" class="cb-world__readonly" data-test="world-initiative-error">
          意图状态读取失败：{{ initiativeError }}
        </p>
        <dl v-if="initiative" class="cb-world__facts" data-test="world-initiative-facts">
          <div>
            <dt>状态</dt>
            <dd data-test="world-initiative-enabled">{{ initiativeStateText(initiative) }}</dd>
          </div>
          <div>
            <dt>执行层</dt>
            <dd data-test="world-initiative-execution">{{ text(initiative.execution_layer) }}</dd>
          </div>
          <div>
            <dt>最近一次检查</dt>
            <dd data-test="world-initiative-checked">{{ intentTime(initiative.last_check_at) }}</dd>
          </div>
          <div>
            <dt>冷却</dt>
            <dd data-test="world-initiative-cooldown">{{ cooldownText(initiative) }}</dd>
          </div>
        </dl>
        <p v-if="initiative" class="cb-world__readonly" data-test="world-initiative-current">
          现在挂着：{{ initiative.current ? `${initiative.current.title}（${initiative.current.intent_type}）` : '没有' }}
        </p>
        <div v-if="initiative && initiative.candidates.length" class="cb-world__facts" data-test="world-initiative-candidates">
          <div v-for="candidate in initiative.candidates" :key="candidate.fingerprint">
            <dt>{{ candidate.intent_type }}</dt>
            <dd>
              {{ candidate.title }} ·
              {{ candidate.allowed ? '已提出' : `被抑制（${candidate.reason}）` }}
            </dd>
          </div>
        </div>
        <div v-if="initiative && initiative.suppressed.length" class="cb-world__facts" data-test="world-initiative-suppressed">
          <div v-for="item in initiative.suppressed" :key="item.intent_id">
            <dt>被抑制</dt>
            <dd>
              {{ item.title }}（{{ item.intent_type }}）· {{ text(item.suppression_reason) }} ·
              {{ intentTime(item.created_at) }}
            </dd>
          </div>
        </div>
        <p
          v-if="initiative && !initiative.current && !initiative.candidates.length"
          class="cb-world__readonly"
          data-test="world-initiative-empty"
        >
          现在没有任何念头（这一层只在有真实信号时才提；没有就什么都不说）。
        </p>
        <p class="cb-world__readonly" data-test="world-initiative-readonly">
          只读：意图只会被**提出 / 评估 / 记录 / 抑制 / 过期**。它不会创建任务、不会调用工具、
          不会自动确认、不会发消息，也不会自己动 Minecraft —— 这里没有任何执行按钮。
        </p>
      </section>

      <section class="cb-world__section" data-test="world-goals">
        <SectionHeader title="目标" description="来自运行期目标；显示真实状态与进度，不做美化" />
        <EmptyState
          v-if="goals.length === 0"
          title="暂无目标"
          description="角色尚未形成目标，或沙盒未运行。"
        />
        <div v-else class="cb-world__grid">
          <GoalCard v-for="goal in goals" :key="goal.goal_id || goal.title" :goal="goal" />
        </div>
      </section>

      <section
        v-if="interrupted && interrupted.active"
        class="cb-card cb-world__section"
        data-test="world-interrupted"
      >
        <SectionHeader title="被打断的动作" description="InterruptedAction 只读记录，WebUI 不提供修改" />
        <dl class="cb-world__facts">
          <div class="cb-world__fact">
            <dt class="cb-caption">动作定义</dt>
            <dd data-test="interrupted-definition">{{ text(interrupted.definition_id) }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">剩余时间</dt>
            <dd data-test="interrupted-remaining">{{ interruptedRemaining }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">打断原因</dt>
            <dd data-test="interrupted-reason">{{ text(interrupted.reason) }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">进度</dt>
            <dd data-test="interrupted-progress">
              {{ interruptedPercent === null ? '—' : `${interruptedPercent}%` }}
            </dd>
          </div>
        </dl>
        <p class="cb-world__readonly" data-test="interrupted-readonly">只读</p>
      </section>

      <div v-if="spaceRows.length > 0 || objectRows.length > 0 || inventoryRows.length > 0 || pet" class="cb-world__grid">
        <section v-if="spaceRows.length > 0" class="cb-card cb-world__section" data-test="world-spaces">
          <SectionHeader title="空间" description="世界里的地点（只读）" />
          <ul class="cb-world__list">
            <li v-for="space in spaceRows" :key="space.id || space.name" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="space-name">{{ space.name }}</span>
              <span class="cb-world__list-meta">{{ space.kind || '—' }}</span>
            </li>
          </ul>
        </section>

        <section v-if="objectRows.length > 0" class="cb-card cb-world__section" data-test="world-objects">
          <SectionHeader title="物件" description="可交互物品（只读）" />
          <ul class="cb-world__list">
            <li v-for="(row, index) in objectRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="object-name">{{ row.label }}</span>
              <span class="cb-world__list-meta">{{ row.value }}</span>
            </li>
          </ul>
        </section>

        <section v-if="inventoryRows.length > 0" class="cb-card cb-world__section" data-test="world-inventory">
          <SectionHeader title="库存" description="容器与随身物品（只读）" />
          <ul class="cb-world__list">
            <li v-for="(row, index) in inventoryRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="inventory-name">{{ row.label }}</span>
              <span class="cb-world__list-meta">{{ row.value }}</span>
            </li>
          </ul>
        </section>

        <section v-if="pet" class="cb-card cb-world__section" data-test="world-pet">
          <SectionHeader title="宠物" description="运行期宠物状态（只读）" />
          <dl class="cb-world__facts">
            <div v-for="row in petRows" :key="row.label" class="cb-world__fact">
              <dt class="cb-caption">{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <p v-if="petLine" class="cb-world__pet-line">{{ petLine }}</p>
        </section>
      </div>

      <section class="cb-card cb-world__section" data-test="world-social-spaces">
        <SectionHeader title="社交空间" description="角色在线的社交场所（只读）" />
        <EmptyState
          v-if="socialSpaceRows.length === 0"
          title="暂无社交空间"
          description="沙盒未启用社交空间或尚未产生数据。"
        />
        <ul v-else class="cb-world__list">
          <li v-for="(row, index) in socialSpaceRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
            <span class="cb-world__list-name" data-test="social-space-name">{{ row.label }}</span>
            <span class="cb-world__list-meta">{{ row.value }}</span>
          </li>
        </ul>
      </section>

      <details class="cb-card cb-world__expert" data-test="world-expert">
        <summary class="cb-world__expert-summary">Expert 数据：action_defs / modes_defs / 原始 JSON</summary>
        <div class="cb-world__expert-body">
          <h3 class="cb-world__expert-title">action_defs</h3>
          <EmptyState
            v-if="(store.world.action_defs ?? []).length === 0"
            title="无 action_defs"
            description="接口未返回动作定义。"
          />
          <ul v-else class="cb-world__list" data-test="world-action-defs">
            <li
              v-for="definition in store.world.action_defs ?? []"
              :key="definition.definition_id"
              class="cb-world__list-item"
            >
              <span class="cb-world__list-name">{{ definition.definition_id }}</span>
              <span class="cb-world__list-meta">{{ definition.name }}</span>
            </li>
          </ul>

          <h3 class="cb-world__expert-title">modes_defs</h3>
          <ul v-if="(store.world.modes_defs ?? []).length > 0" class="cb-world__list" data-test="world-modes-defs">
            <li
              v-for="mode in store.world.modes_defs ?? []"
              :key="mode.key"
              class="cb-world__list-item"
            >
              <span class="cb-world__list-name">{{ mode.key }}</span>
              <span class="cb-world__list-meta">{{ mode.label }}</span>
            </li>
          </ul>
          <p v-else class="cb-muted">接口未返回 modes_defs。</p>

          <h3 class="cb-world__expert-title">原始 JSON</h3>
          <pre class="cb-world__json" data-test="world-expert-json">{{ rawJson }}</pre>
        </div>
      </details>
    </template>

    <EmptyState
      v-else-if="!store.loading"
      title="暂无世界数据"
      description="沙盒未运行，或接口尚未返回世界快照。"
    />

    <section class="cb-card cb-world__section cb-world__admin">
      <details data-test="world-admin">
        <summary class="cb-world__admin-summary">管理员操作</summary>
        <p class="cb-caption">
          这些操作直接影响运行中的世界；执行前会再次确认。reset / reinitialize 不可撤销。
        </p>
        <div class="cb-world__admin-actions">
          <button
            v-for="control in CONTROL_ACTIONS"
            :key="control"
            type="button"
            class="cb-world__control"
            :class="{ 'cb-world__control--danger': CONTROL_META[control].danger }"
            :data-test="`world-control-${control}`"
            @click="askControl(control)"
          >
            {{ CONTROL_META[control].label }}
          </button>
        </div>
        <p v-if="controlError" class="cb-world__control-error" data-test="world-control-error" role="alert">
          {{ controlError }}
        </p>
      </details>
    </section>

    <ConfirmDialog
      :show="pending !== null"
      :title="pending ? CONTROL_META[pending].title : ''"
      :message="pending ? CONTROL_META[pending].message : ''"
      :detail="pending ? CONTROL_META[pending].detail : ''"
      :confirm-text="pending ? CONTROL_META[pending].confirmText : '确认'"
      :danger="pending ? CONTROL_META[pending].danger : false"
      @confirm="confirmControl"
      @cancel="pending = null"
    />

    <!-- 行为调试（v0.8「沙盒 · 对话行为」迁移；全部 dry-run，永不发送） -->
    <section class="cb-card cb-world__section cb-world__behavior" data-test="world-behavior">
      <SectionHeader
        title="行为调试"
        description="试跑回复链路与行为模拟器；所有结果只在这里展示，永远不会发送 QQ 消息。"
      />

      <div class="cb-world__behavior-grid">
        <div class="cb-world__behavior-block" data-test="behavior-test-response">
          <p class="cb-world__behavior-title">试跑回复</p>
          <label class="cb-world__behavior-field">
            <span class="cb-caption">对方说的话</span>
            <NInput
              v-model:value="behaviorText"
              type="textarea"
              :rows="2"
              placeholder="例如：今天过得怎么样？"
              data-test="behavior-test-input"
              @keydown.enter.exact.prevent="runTestResponse"
            />
          </label>
          <div class="cb-world__behavior-actions">
            <button
              type="button"
              class="cb-world__behavior-button"
              data-test="behavior-test-run"
              :disabled="behaviorRunning"
              @click="runTestResponse"
            >
              {{ behaviorRunning ? '生成中…' : '试跑一次' }}
            </button>
            <span class="cb-caption">只生成，不发送。</span>
          </div>
          <p
            v-if="behaviorError"
            class="cb-world__behavior-error"
            role="alert"
            data-test="behavior-test-error"
          >
            {{ behaviorError }}
          </p>
          <div v-else-if="behaviorResult" class="cb-world__behavior-result" data-test="behavior-test-result">
            <p v-if="!behaviorResult.ok" class="cb-world__behavior-error" data-test="behavior-test-failure">
              {{ behaviorResult.error || '生成失败' }}
            </p>
            <template v-else>
              <p class="cb-world__behavior-reply" data-test="behavior-test-reply">
                {{ behaviorResult.reply || '（空回复）' }}
              </p>
              <dl class="cb-world__behavior-facts">
                <div>
                  <dt class="cb-caption">延迟</dt>
                  <dd data-test="behavior-test-delay">
                    {{ behaviorResult.delay === undefined ? '—' : `${behaviorResult.delay} 秒` }}
                  </dd>
                </div>
                <div>
                  <dt class="cb-caption">分条</dt>
                  <dd data-test="behavior-test-chunks">
                    {{ behaviorResult.chunks && behaviorResult.chunks.length > 0 ? behaviorResult.chunks.join(' / ') : '不分条' }}
                  </dd>
                </div>
                <div>
                  <dt class="cb-caption">状态</dt>
                  <dd data-test="behavior-test-state">{{ behaviorStateText(behaviorResult.state) }}</dd>
                </div>
                <div>
                  <dt class="cb-caption">时间</dt>
                  <dd data-test="behavior-test-time">{{ behaviorResult.time || '—' }}</dd>
                </div>
              </dl>
            </template>
          </div>
        </div>

        <div class="cb-world__behavior-block" data-test="behavior-triggers">
          <p class="cb-world__behavior-title">手动触发</p>
          <div class="cb-world__behavior-actions">
            <button
              v-for="action in TRIGGER_ACTIONS"
              :key="action"
              type="button"
              class="cb-world__behavior-button"
              :class="{ 'cb-world__behavior-button--danger': TRIGGER_META[action].danger }"
              :data-test="`behavior-trigger-${action}`"
              :disabled="triggerBusy !== ''"
              @click="askTrigger(action)"
            >
              {{ triggerBusy === action ? '执行中…' : TRIGGER_META[action].label }}
            </button>
          </div>
          <p
            v-if="triggerError"
            class="cb-world__behavior-error"
            role="alert"
            data-test="behavior-trigger-error"
          >
            {{ triggerError }}
          </p>
          <p v-else-if="triggerResult" class="cb-world__behavior-result" data-test="behavior-trigger-result">
            {{ triggerResult }}
          </p>
          <p v-else class="cb-caption" data-test="behavior-trigger-empty">
            尚未触发过状态变化。
          </p>
        </div>

        <div class="cb-world__behavior-block cb-world__behavior-block--wide" data-test="behavior-preview">
          <p class="cb-world__behavior-title">行为模拟器</p>
          <p class="cb-caption">临时覆盖时间 / 心情 / 关系等条件，看延迟、分条与主动搭话判定；不会发送。</p>
          <div class="cb-world__behavior-form">
            <label class="cb-world__behavior-field">
              <span class="cb-caption">模拟时间 sim_time（HH:MM）</span>
              <NInput v-model:value="previewDraft.simTime" placeholder="23:30" data-test="behavior-preview-sim-time" />
            </label>
            <label class="cb-world__behavior-field">
              <span class="cb-caption">心情 mood</span>
              <NInput v-model:value="previewDraft.mood" placeholder="happy" data-test="behavior-preview-mood" />
            </label>
            <label class="cb-world__behavior-field">
              <span class="cb-caption">正在做 activity</span>
              <NInput v-model:value="previewDraft.activity" placeholder="看书" data-test="behavior-preview-activity" />
            </label>
            <label class="cb-world__behavior-field">
              <span class="cb-caption">关系 relationship</span>
              <NSelect
                v-model:value="previewDraft.relationship"
                :options="RELATIONSHIP_OPTIONS"
                data-test="behavior-preview-relationship"
              />
            </label>
            <label class="cb-world__behavior-field">
              <span class="cb-caption">话题 topic（可选）</span>
              <NInput v-model:value="previewDraft.topic" placeholder="未完成的项目" data-test="behavior-preview-topic" />
            </label>
            <label class="cb-world__behavior-field">
              <span class="cb-caption">示例回复 sample_reply（可选）</span>
              <NInput
                v-model:value="previewDraft.sampleReply"
                type="textarea"
                :rows="2"
                placeholder="好呀。\n\n等我看一下。"
                data-test="behavior-preview-sample"
              />
            </label>
          </div>
          <div class="cb-world__behavior-actions">
            <button
              type="button"
              class="cb-world__behavior-button"
              data-test="behavior-preview-run"
              :disabled="previewRunning"
              @click="runPreview"
            >
              {{ previewRunning ? '模拟中…' : '运行模拟' }}
            </button>
          </div>
          <p
            v-if="previewError"
            class="cb-world__behavior-error"
            role="alert"
            data-test="behavior-preview-error"
          >
            {{ previewError }}
          </p>
          <dl
            v-else-if="previewResult"
            class="cb-world__behavior-facts"
            data-test="behavior-preview-result"
          >
            <div>
              <dt class="cb-caption">时间</dt>
              <dd data-test="behavior-preview-time">
                {{ previewResult.time || '—' }}{{ previewResult.period ? `（${previewResult.period}）` : '' }}
              </dd>
            </div>
            <div>
              <dt class="cb-caption">睡眠 / 免打扰</dt>
              <dd data-test="behavior-preview-presence">
                {{ previewResult.sleeping ? '睡眠中' : '清醒' }} · {{ previewResult.dnd ? '免打扰' : '可打扰' }}
              </dd>
            </div>
            <div>
              <dt class="cb-caption">延迟</dt>
              <dd data-test="behavior-preview-delay">
                {{ previewResult.delay === undefined ? '—' : `${previewResult.delay} 秒` }}
              </dd>
            </div>
            <div>
              <dt class="cb-caption">分条</dt>
              <dd data-test="behavior-preview-chunks">
                {{ previewResult.chunks && previewResult.chunks.length > 0 ? previewResult.chunks.join(' / ') : '不分条' }}
              </dd>
            </div>
            <div>
              <dt class="cb-caption">状态</dt>
              <dd data-test="behavior-preview-state">{{ previewMoodText() }}</dd>
            </div>
            <div v-if="previewResult.initiative">
              <dt class="cb-caption">主动搭话</dt>
              <dd data-test="behavior-preview-initiative">
                {{ previewResult.initiative.would_consider ? '会考虑' : '不会' }}
                · 概率 {{ previewResult.initiative.probability }}
                · 掷点 {{ previewResult.initiative.simulated_roll }}
                <template v-if="previewResult.initiative.blocked_by">
                  · 受阻：{{ previewResult.initiative.blocked_by }}
                </template>
                <template v-else-if="previewResult.initiative.reason">
                  · 理由：{{ previewResult.initiative.reason }}
                </template>
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </section>

    <ConfirmDialog
      :show="pendingTrigger !== null"
      :title="pendingTrigger ? TRIGGER_META[pendingTrigger].title : ''"
      :message="pendingTrigger ? TRIGGER_META[pendingTrigger].message : ''"
      :detail="pendingTrigger ? TRIGGER_META[pendingTrigger].detail : ''"
      :confirm-text="pendingTrigger ? TRIGGER_META[pendingTrigger].confirmText : '确认'"
      :danger="pendingTrigger ? TRIGGER_META[pendingTrigger].danger : false"
      @confirm="confirmTrigger"
      @cancel="pendingTrigger = null"
    />
  </div>
</template>

<style scoped>
.cb-world {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-world__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--cb-space-4);
  align-items: start;
}

.cb-world__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-world__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3) var(--cb-space-4);
  margin: 0;
}

.cb-world__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-world__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-world__list-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding-bottom: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
}

.cb-world__list-item:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.cb-world__list-name {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-world__list-meta {
  flex: none;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  text-align: right;
  overflow-wrap: anywhere;
}

.cb-world__readonly {
  align-self: flex-start;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-world__pet-line {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-world__expert summary {
  cursor: pointer;
}

.cb-world__expert-summary {
  color: var(--cb-text-md);
}

.cb-world__expert-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin-top: var(--cb-space-3);
}

.cb-world__expert-title {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-family: var(--cb-font-mono);
}

.cb-world__json {
  max-height: 360px;
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.cb-world__admin summary {
  cursor: pointer;
}

.cb-world__admin-summary {
  color: var(--cb-text-md);
}

.cb-world__admin-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
  margin-top: var(--cb-space-3);
}

.cb-world__control {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-world__control:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-world__control--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-world__control--danger:hover {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}

.cb-world__control-error {
  margin-top: var(--cb-space-3);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

/* ------------------------------------------------------------ 行为调试 */

.cb-world__behavior-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--cb-space-4);
  align-items: start;
}

.cb-world__behavior-block {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  min-width: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.cb-world__behavior-block--wide {
  grid-column: 1 / -1;
}

.cb-world__behavior-title {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
}

.cb-world__behavior-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-2);
}

.cb-world__behavior-button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-world__behavior-button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-world__behavior-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-world__behavior-button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-world__behavior-form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--cb-space-3);
}

.cb-world__behavior-field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.cb-world__behavior-field :deep(.n-input),
.cb-world__behavior-field :deep(.n-select) {
  width: 100%;
}

.cb-world__behavior-result {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-world__behavior-reply {
  margin-bottom: var(--cb-space-2);
  white-space: pre-wrap;
}

.cb-world__behavior-error {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-world__behavior-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-world__behavior-facts dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}
</style>
