<script setup lang="ts">
/**
 * 承诺详情（W5 §24）：展示承诺本体 + Person → Social Space → Goal → Action →
 * Outcome 链式区块，回答「这条承诺为什么没有兑现」。
 *
 * 排查结论只由接口字段推导；接口没有提供的数据（例如原时间窗）绝不猜测。
 */
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useSocialStore } from '@/stores/social'
import { commitmentLabel, type CommitmentDetail } from '@/types/social'

type Dict = Record<string, unknown>

/** 真实 Core 的 strength 是字符串枚举（explicit / soft）；类型声明为 number，渲染时两者都兼容。 */
const STRENGTH_LABELS: Record<string, string> = {
  explicit: '明确',
  soft: '软性',
}

function strengthText(value: number | string): string {
  if (value === null || value === undefined || value === '') return '—'
  const key = String(value)
  return STRENGTH_LABELS[key] ?? key
}

const KIND_LABELS: Record<string, string> = {
  shared_activity: '共同活动',
  appointment: '约定',
  help: '帮忙',
  follow_up: '后续跟进',
  deliverable: '交付',
}

const GOAL_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  active: '进行中',
  blocked: '受阻',
  completed: '已完成',
  cancelled: '已取消',
  expired: '已过期',
}

/** 真实 Core 状态中标签表未覆盖的取值。 */
const EXTRA_STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  scheduled: '已排期',
  in_progress: '进行中',
  completed: '已履行',
  declined: '已拒绝',
  expired: '已过期',
}

function statusText(status: string): string {
  const label = commitmentLabel(status)
  return label === status ? (EXTRA_STATUS_LABELS[status] ?? label) : label
}

const route = useRoute()
const store = useSocialStore()

const commitmentId = computed(() => String(route.params.commitmentId ?? ''))
const detail = computed<CommitmentDetail | null>(() => store.commitmentDetail)

const loading = ref(false)

async function load(id: string): Promise<void> {
  if (!id) return
  loading.value = true
  await store.loadCommitment(id)
  loading.value = false
}

watch(
  () => route.params.commitmentId,
  (value) => {
    void load(String(value ?? ''))
  },
  { immediate: true },
)

// 空间名只是展示辅助，失败不影响详情页。
void store.loadSpaces()

const notFound = computed(
  () => !loading.value && detail.value === null && /不存在|commitment_not_found/.test(store.error),
)
const failed = computed(
  () => !loading.value && detail.value === null && store.error !== '' && !notFound.value,
)

// ------------------------------------------------------------------- 字段读取
function str(source: Dict | null | undefined, key: string): string {
  const value = source?.[key]
  if (value === null || value === undefined || value === '') return ''
  return String(value)
}

function num(source: Dict | null | undefined, key: string): number | null {
  const value = source?.[key]
  const numeric = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function pct(source: Dict | null | undefined, key: string): string {
  const value = num(source, key)
  if (value === null) return '—'
  return `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`
}

function formatTime(value: number | null | undefined): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

const goal = computed(() => detail.value?.goal ?? null)
const action = computed(() => detail.value?.action ?? null)
const outcome = computed(() => detail.value?.outcome ?? null)

const spaceId = computed(() => str(action.value, 'space_id'))
const spaceLabel = computed(() => {
  if (!spaceId.value) return ''
  const found = store.spaces.find((space) => space.space_id === spaceId.value)
  return found ? found.name || found.space_id : '未知空间'
})

const kindLabel = computed(() => {
  const kind = detail.value?.kind ?? ''
  return KIND_LABELS[kind] ?? kind
})

const goalStatusLabel = computed(() => {
  const value = str(goal.value, 'status') || detail.value?.goal_status || ''
  if (!value) return '未关联'
  return GOAL_STATUS_LABELS[value] ?? value
})

// ------------------------------------------------------------------- 排查结论
const diagnosis = computed(() => {
  const item = detail.value
  if (!item) return ''
  const now = Date.now() / 1000
  const due = item.due_at
  const dueText = due !== null && due !== undefined && due > 0 ? formatTime(due) : ''
  if (item.status === 'broken') {
    return due !== null && due !== undefined && due > 0 && due < now
      ? `未履行：已超过截止时间 ${dueText}，且没有产生兑现结果。`
      : '未履行：结果记录判定这条承诺没有被兑现。'
  }
  if (item.status === 'cancelled' || item.status === 'declined') {
    return '已取消：这条承诺不再被追踪。'
  }
  if (item.status === 'expired') return '已过期：承诺窗口结束时仍未产生结果。'
  if (item.status === 'completed' || item.status === 'fulfilled') {
    const resolved = item.outcome?.resolved_at ?? null
    return resolved ? `已履行：结果在 ${formatTime(resolved)} 记录。` : '已履行：承诺已完成。'
  }
  if (item.status === 'rescheduled') {
    return '已改期：当前时间窗以「最早 / 截止」字段为准（接口没有提供原窗口，不做推测）。'
  }
  if (due !== null && due !== undefined && due > 0 && due < now) {
    return `仍未产生结果，且已超过截止时间 ${dueText}。`
  }
  if (!item.goal_id) return '进行中：还没有关联 Goal，因此没有可核对的行动链。'
  if (!action.value) return '进行中：当前没有正在执行的关联 Action。'
  if (!item.outcome) return '进行中：Action 已关联，结果尚未产生。'
  return '进行中：等待结果记录。'
})
</script>

<template>
  <div class="cb-commitment-detail" data-test="commitment-detail">
    <ErrorState v-if="notFound" message="找不到这条承诺" :detail="store.error" />
    <RouterLink
      v-if="notFound"
      class="cb-commitment-detail__back"
      data-test="commitment-back"
      to="/social/commitments"
    >
      返回承诺列表
    </RouterLink>

    <ErrorState v-else-if="failed" :message="store.error" @retry="load(commitmentId)" />

    <LoadingState v-else-if="loading && !detail" label="正在读取承诺详情…" :rows="5" />

    <template v-else-if="detail">
      <SectionHeader title="承诺本体" description="字段全部来自后端投影，页面不推断状态。">
        <template #actions>
          <RouterLink class="cb-commitment-detail__back" to="/social/commitments">
            返回列表
          </RouterLink>
        </template>
      </SectionHeader>

      <div class="cb-commitment-detail__card">
        <p class="cb-commitment-detail__summary" data-test="detail-summary">
          {{ detail.summary || '（无摘要）' }}
        </p>
        <dl class="cb-commitment-detail__facts">
          <div>
            <dt>状态</dt>
            <dd data-test="detail-status">{{ statusText(detail.status) }}</dd>
          </div>
          <div>
            <dt>类型</dt>
            <dd data-test="detail-kind">{{ kindLabel || '—' }}</dd>
          </div>
          <div>
            <dt>强度</dt>
            <dd data-test="detail-strength">{{ strengthText(detail.strength) }}</dd>
          </div>
          <div>
            <dt>优先级</dt>
            <dd>{{ detail.priority }}</dd>
          </div>
          <div>
            <dt>时间提示</dt>
            <dd>{{ detail.time_hint || '—' }}</dd>
          </div>
          <div>
            <dt>最早</dt>
            <dd>{{ formatTime(detail.earliest_at) }}</dd>
          </div>
          <div>
            <dt>截止</dt>
            <dd data-test="detail-due">{{ formatTime(detail.due_at) }}</dd>
          </div>
          <div>
            <dt>创建时间</dt>
            <dd>{{ formatTime(detail.created_at) }}</dd>
          </div>
        </dl>
        <p class="cb-commitment-detail__diagnosis" data-test="commitment-diagnosis">
          {{ diagnosis }}
        </p>
      </div>

      <SectionHeader title="为什么没有兑现" description="按 Person → Social Space → Goal → Action → Outcome 逐层核对。" />

      <ol class="cb-commitment-detail__chain">
        <li class="cb-commitment-detail__step" data-test="chain-person">
          <h3 class="cb-commitment-detail__step-title">1. Person</h3>
          <p v-if="detail.person_name" data-test="chain-person-name">
            人物：{{ detail.person_name }}
          </p>
          <p class="cb-commitment-detail__id">person_id: {{ detail.person_id }}</p>
          <RouterLink
            class="cb-commitment-detail__back"
            :to="`/social/users/${encodeURIComponent(detail.person_id)}`"
          >
            查看人物详情
          </RouterLink>
        </li>

        <li class="cb-commitment-detail__step" data-test="chain-space">
          <h3 class="cb-commitment-detail__step-title">2. Social Space</h3>
          <template v-if="spaceId">
            <p data-test="chain-space-name">空间：{{ spaceLabel }}</p>
            <p class="cb-commitment-detail__id">space_id: {{ spaceId }}</p>
          </template>
          <p v-else class="cb-commitment-detail__missing" data-test="chain-space-missing">未关联</p>
          <p class="cb-commitment-detail__note">
            人物与社交空间是两个独立维度：空间是行动发生的场景，不是关系对象（§21）。
          </p>
        </li>

        <li class="cb-commitment-detail__step" data-test="chain-goal">
          <h3 class="cb-commitment-detail__step-title">3. Goal</h3>
          <template v-if="goal">
            <p class="cb-commitment-detail__id">goal_id: {{ str(goal, 'goal_id') || '—' }}</p>
            <p data-test="chain-goal-status">状态：{{ goalStatusLabel }}</p>
            <p>进度：{{ pct(goal, 'progress') }}</p>
            <p v-if="str(goal, 'reason')">原因：{{ str(goal, 'reason') }}</p>
          </template>
          <p v-else class="cb-commitment-detail__missing" data-test="chain-goal-missing">未关联</p>
        </li>

        <li class="cb-commitment-detail__step" data-test="chain-action">
          <h3 class="cb-commitment-detail__step-title">4. Action</h3>
          <template v-if="action">
            <p data-test="chain-action-name">
              动作：{{ str(action, 'name') || str(action, 'definition_id') || '—' }}
            </p>
            <p>状态：{{ str(action, 'status') || '—' }} · 进度：{{ pct(action, 'progress') }}</p>
            <p v-if="str(action, 'space_id')" class="cb-commitment-detail__id">
              space_id: {{ str(action, 'space_id') }}
            </p>
            <p>计划结束：{{ formatTime(num(action, 'planned_end_at')) }}</p>
          </template>
          <p v-else class="cb-commitment-detail__missing" data-test="chain-action-missing">未关联</p>
        </li>

        <li class="cb-commitment-detail__step" data-test="chain-outcome">
          <h3 class="cb-commitment-detail__step-title">5. Outcome</h3>
          <template v-if="outcome">
            <p data-test="chain-outcome-status">结果：{{ statusText(outcome.status) }}</p>
            <p>解决时间：{{ formatTime(outcome.resolved_at) }} · 修订：{{ outcome.revision }}</p>
            <p v-if="outcome.result">说明：{{ outcome.result }}</p>
          </template>
          <p v-else class="cb-commitment-detail__missing" data-test="chain-outcome-missing">
            未关联
          </p>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.cb-commitment-detail {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-commitment-detail__back {
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  text-decoration: none;
  align-self: flex-start;
}

.cb-commitment-detail__back:hover {
  text-decoration: underline;
}

.cb-commitment-detail__card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-commitment-detail__summary {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-commitment-detail__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-commitment-detail__facts dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-commitment-detail__facts dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-commitment-detail__diagnosis {
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-commitment-detail__chain {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-commitment-detail__step {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-left: 3px solid var(--cb-primary);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-commitment-detail__step-title {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-commitment-detail__step p {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-commitment-detail__step p.cb-commitment-detail__id {
  color: var(--cb-text-muted);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-commitment-detail__step p.cb-commitment-detail__missing {
  color: var(--cb-text-faint);
  font-style: italic;
}

.cb-commitment-detail__step p.cb-commitment-detail__note {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}
</style>
