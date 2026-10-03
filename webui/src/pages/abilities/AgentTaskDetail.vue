<script setup lang="ts">
/**
 * Agent 任务详情（W5 §43）：计划 / 步骤 / 观察 / 轨迹 + 受控操作。
 *
 * 控制动作（暂停 / 恢复 / 取消 / 重试 / 重放）全部需要二次确认；
 * 后端拒绝时如实显示后端 message（例如 409 agent.action_failed），不吞错。
 */
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { agentApi } from '@/api/abilities'
import { ApiError, errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'

type ControlAction = 'pause' | 'resume' | 'cancel' | 'retry' | 'replay'

interface ControlMeta {
  label: string
  title: string
  message: string
  detail: string
  danger: boolean
}

interface ControlItem {
  action: ControlAction
  meta: ControlMeta
}

const CONTROLS: ControlItem[] = [
  {
    action: 'pause',
    meta: {
      label: '暂停',
      title: '暂停任务',
      message: '确定暂停这个任务吗？',
      detail: '暂停后任务不会继续执行步骤，之后可以恢复。',
      danger: false,
    },
  },
  {
    action: 'resume',
    meta: {
      label: '恢复',
      title: '恢复任务',
      message: '确定恢复这个任务吗？',
      detail: '任务会从暂停处继续执行。',
      danger: false,
    },
  },
  {
    action: 'cancel',
    meta: {
      label: '取消',
      title: '取消任务',
      message: '确定取消这个任务吗？',
      detail: '取消是终止性操作，任务不会再继续执行。',
      danger: true,
    },
  },
  {
    action: 'retry',
    meta: {
      label: '重试',
      title: '重试任务',
      message: '确定重试这个任务吗？',
      detail: '会用同一目标重新运行一次 Agent 任务。',
      danger: false,
    },
  },
  {
    action: 'replay',
    meta: {
      label: '重放',
      title: '重放任务',
      message: '确定重放这个任务的规划吗？',
      detail: '重放只重新规划，不执行工具、不会发送任何 QQ 消息。',
      danger: false,
    },
  },
]

const route = useRoute()
const taskId = computed(() => String(route.params.taskId ?? ''))

const detail = ref<Record<string, unknown> | null>(null)
const loading = ref(false)
const loadError = ref('')
const notFound = ref(false)

const pendingAction = ref<ControlAction | null>(null)
const showConfirm = ref(false)
const working = ref(false)
const controlError = ref('')
const controlDetail = ref('')

const task = computed<Record<string, unknown>>(
  () => (detail.value?.task as Record<string, unknown> | undefined) ?? {},
)
const goal = computed<Record<string, unknown> | null>(
  () => (detail.value?.goal as Record<string, unknown> | null | undefined) ?? null,
)
const plans = computed<Record<string, unknown>[]>(() => rowsOf('plans'))
const steps = computed<Record<string, unknown>[]>(() => rowsOf('steps'))
const observations = computed<Record<string, unknown>[]>(() => rowsOf('observations'))
const traces = computed<Record<string, unknown>[]>(() => rowsOf('traces'))

const pendingMeta = computed<ControlMeta | null>(
  () => CONTROLS.find((item) => item.action === pendingAction.value)?.meta ?? null,
)

function metaLabel(action: ControlAction): string {
  return CONTROLS.find((item) => item.action === action)?.meta.label ?? action
}

function rowsOf(key: string): Record<string, unknown>[] {
  const value = detail.value?.[key]
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : []
}

function stringify(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function describe(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join('、') : '—'
  if (typeof value === 'object') return stringify(value)
  return String(value)
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatTimestamp(value: unknown): string {
  const seconds = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  const millis = seconds > 1e12 ? seconds : seconds * 1000
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value_: number): string => String(value_).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  notFound.value = false
  try {
    detail.value = await agentApi.task(taskId.value)
  } catch (caught) {
    detail.value = null
    if (caught instanceof ApiError && caught.status === 404) {
      notFound.value = true
    } else {
      loadError.value = errorMessage(caught)
    }
  } finally {
    loading.value = false
  }
}

function startControl(action: ControlAction): void {
  pendingAction.value = action
  controlError.value = ''
  controlDetail.value = ''
  showConfirm.value = true
}

async function confirmControl(): Promise<void> {
  const action = pendingAction.value
  showConfirm.value = false
  if (!action) return
  working.value = true
  controlError.value = ''
  try {
    const result = await agentApi.control(taskId.value, action)
    controlDetail.value = text(result.detail)
    toast.success(`${metaLabel(action)}已应用`, text(result.detail))
    await load()
  } catch (caught) {
    controlError.value = errorMessage(caught)
    toast.error(`${metaLabel(action)}失败`, controlError.value)
  } finally {
    working.value = false
    pendingAction.value = null
  }
}

watch(taskId, () => void load(), { immediate: true })
</script>

<template>
  <div class="agent-task" data-test="agent-task-detail">
    <ErrorState
      v-if="notFound"
      :message="`Agent 任务不存在：${taskId}`"
      detail="任务可能已被清理，请回到 Agent 面板查看当前任务。"
    />
    <ErrorState v-else-if="loadError" :message="loadError" @retry="load" />
    <LoadingState v-else-if="loading && !detail" label="正在读取任务详情…" :rows="5" />

    <template v-else-if="detail">
      <section class="agent-task__section cb-card" data-test="task-summary">
        <SectionHeader :title="`任务 ${taskId}`" description="任务状态与结果来自真实 Agent 存储。">
          <template #actions>
            <StatusBadge state="idle" :label="text(task.status)" />
          </template>
        </SectionHeader>
        <dl class="agent-task__facts">
          <div><dt>分类</dt><dd>{{ text(task.classification) }}</dd></div>
          <div><dt>步骤</dt><dd>{{ text(task.completed_steps) }} / {{ text(task.step_count) }}</dd></div>
          <div><dt>工具调用</dt><dd>{{ text(task.tool_calls) }}</dd></div>
          <div><dt>创建时间</dt><dd>{{ formatTimestamp(task.created_at) }}</dd></div>
          <div><dt>更新时间</dt><dd>{{ formatTimestamp(task.updated_at) }}</dd></div>
          <div><dt>错误类型</dt><dd>{{ text(task.error_type) }}</dd></div>
        </dl>
        <p v-if="task.result_summary" class="agent-task__text">结果摘要：{{ text(task.result_summary) }}</p>
      </section>

      <section class="agent-task__section cb-card" data-test="task-controls">
        <SectionHeader title="控制" description="所有控制动作都需要二次确认；后端拒绝时会显示原始 message。" />
        <div class="agent-task__controls">
          <button
            v-for="item in CONTROLS"
            :key="item.action"
            type="button"
            class="agent-task__button"
            :class="{ 'agent-task__button--danger': item.meta.danger }"
            :disabled="working"
            :data-test="`task-control-${item.action}`"
            @click="startControl(item.action)"
          >
            {{ item.meta.label }}
          </button>
        </div>
        <p v-if="controlError" class="agent-task__error" role="alert" data-test="task-control-error">
          {{ controlError }}
        </p>
        <p v-else-if="controlDetail" class="cb-caption" data-test="task-control-detail">{{ controlDetail }}</p>
      </section>

      <section v-if="goal" class="agent-task__section cb-card" data-test="task-goal">
        <SectionHeader title="目标" description="任务的原始目标描述。" />
        <p class="agent-task__text">{{ text(goal.description) }}</p>
        <dl class="agent-task__facts">
          <div><dt>会话</dt><dd>{{ text(goal.session_id) }}</dd></div>
          <div><dt>用户</dt><dd>{{ text(goal.user_id) }}</dd></div>
          <div><dt>群</dt><dd>{{ text(goal.group_id) }}</dd></div>
        </dl>
      </section>

      <section class="agent-task__section cb-card" data-test="task-plans">
        <SectionHeader title="计划" description="每个计划版本的标准与步骤（JSON 已由后端解码）。" />
        <p v-if="plans.length === 0" class="cb-muted">还没有计划。</p>
        <article v-for="(plan, index) in plans" :key="index" class="agent-task__plan" data-test="task-plan">
          <h3 class="agent-task__label">版本 {{ text(plan.version) }} · {{ text(plan.status) }}</h3>
          <p class="cb-caption">标准：{{ describe(plan.criteria) }}</p>
          <pre class="agent-task__pre">{{ stringify(plan.steps) }}</pre>
        </article>
      </section>

      <section class="agent-task__section cb-card" data-test="task-steps">
        <SectionHeader title="步骤" description="执行顺序与状态；参数不回显原始敏感内容。" />
        <table v-if="steps.length > 0" class="agent-task__table">
          <thead>
            <tr>
              <th scope="col">步骤</th>
              <th scope="col">状态</th>
              <th scope="col">工具</th>
              <th scope="col">描述</th>
              <th scope="col">结果</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(step, index) in steps" :key="index" data-test="task-step-row">
              <td>{{ text(step.id) }}</td>
              <td>{{ text(step.status) }}</td>
              <td>{{ text(step.tool) }}</td>
              <td>{{ text(step.description) }}</td>
              <td>{{ text(step.result_summary) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="cb-muted">还没有步骤。</p>
      </section>

      <section class="agent-task__section cb-card" data-test="task-observations">
        <SectionHeader title="观察" description="每步工具返回的观察数据（截断窗口内）。" />
        <p v-if="observations.length === 0" class="cb-muted">还没有观察记录。</p>
        <article v-for="(observation, index) in observations" :key="index" class="agent-task__plan" data-test="task-observation">
          <h3 class="agent-task__label">步骤 {{ text(observation.step_id) }} · {{ text(observation.status) }}</h3>
          <pre class="agent-task__pre">{{ stringify(observation.data) }}</pre>
        </article>
      </section>

      <section class="agent-task__section cb-card" data-test="task-traces">
        <SectionHeader title="轨迹" description="结构化执行轨迹，只读。" />
        <p v-if="traces.length === 0" class="cb-muted">还没有轨迹记录。</p>
        <pre v-for="(trace, index) in traces" :key="index" class="agent-task__pre" data-test="task-trace">{{ stringify(trace) }}</pre>
      </section>
    </template>

    <ConfirmDialog
      v-model:show="showConfirm"
      :title="pendingMeta?.title ?? '确认操作'"
      :message="pendingMeta?.message ?? ''"
      :detail="pendingMeta?.detail"
      :confirm-text="pendingMeta?.label ?? '确认'"
      :danger="pendingMeta?.danger ?? false"
      @confirm="confirmControl"
    />
  </div>
</template>

<style scoped>
.agent-task {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.agent-task__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.agent-task__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.agent-task__facts dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.agent-task__facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.agent-task__text {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.agent-task__controls {
  display: flex;
  gap: var(--cb-space-2);
  flex-wrap: wrap;
}

.agent-task__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.agent-task__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.agent-task__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.agent-task__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.agent-task__error {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
}

.agent-task__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.agent-task__plan {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
}

.agent-task__pre {
  max-height: 280px;
  overflow: auto;
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.agent-task__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.agent-task__table th,
.agent-task__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  overflow-wrap: anywhere;
}

.agent-task__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}
</style>
