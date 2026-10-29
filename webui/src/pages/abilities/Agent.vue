<script setup lang="ts">
/**
 * Agent 面板（W5 §42-§44）：状态由真实运行时推导（ready / disabled / unavailable）。
 *
 * 绝不猜测：没有 Agent Runtime 或已禁用时，只显示「尚未启用 Agent Runtime」
 * 与原因，不展示任何假任务、假预算。任务模拟是干跑，只分类 + 规划。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { agentApi } from '@/api/abilities'
import { errorMessage } from '@/api/client'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import MetricCard from '@/components/MetricCard.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type { AgentStatus, AgentTaskRow } from '@/types/domain'

const TASK_STATUS_LABELS: Record<string, string> = {
  created: '已创建',
  planning: '规划中',
  ready: '待执行',
  running: '执行中',
  waiting: '等待中',
  replanning: '重规划中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  canceled: '已取消',
}

const STATUS_META: Record<string, { state: StatusState; label: string; reason: string }> = {
  ready: {
    state: 'ok',
    label: '已启用',
    reason: 'Agent Runtime 正在运行。',
  },
  disabled: {
    state: 'off',
    label: '尚未启用',
    reason: '后端存在 Agent Runtime，但配置里 agent.enabled=false，没有启动任务。',
  },
  unavailable: {
    state: 'error',
    label: '不可用',
    reason: '后端没有 Agent Runtime（bot.agent 不存在），无法读取任务或策略。',
  },
}

const loading = ref(false)
const error = ref('')
const status = ref<AgentStatus | null>(null)

const simulateText = ref('')
const simulating = ref(false)
const simulation = ref<Record<string, unknown> | null>(null)

const statusKey = computed(() => status.value?.status ?? '')
const meta = computed(
  () =>
    STATUS_META[statusKey.value] ?? {
      state: 'idle' as StatusState,
      label: '状态未知',
      reason: '无法识别的 Agent 状态，请检查后端。',
    },
)
const isReady = computed(() => statusKey.value === 'ready')

const activeTasks = computed<AgentTaskRow[]>(
  () => (status.value?.active_tasks ?? []) as AgentTaskRow[],
)
const recentTasks = computed<AgentTaskRow[]>(
  () => (status.value?.recent_tasks ?? []) as AgentTaskRow[],
)
const budgetEntries = computed<[string, string][]>(() =>
  Object.entries(status.value?.budget ?? {}).map(([key, value]) => [key, describe(value)]),
)
const policyEntries = computed<[string, string][]>(() =>
  Object.entries(status.value?.policy ?? {})
    .filter(([key]) => key !== 'budget' && key !== 'planner_model' && key !== 'evaluator_model')
    .map(([key, value]) => [key, describe(value)]),
)

function describe(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join('、') : '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function taskId(row: AgentTaskRow): string {
  return String(row.task_id ?? '')
}

function taskStatusLabel(value: unknown): string {
  const key = String(value ?? '')
  return TASK_STATUS_LABELS[key] ?? (key || '—')
}

function taskGoal(row: AgentTaskRow): string {
  return text(row.goal ?? row.goal_id)
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
  )}:${pad(date.getMinutes())}`
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    status.value = await agentApi.status()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function runSimulate(): Promise<void> {
  const input = simulateText.value.trim()
  if (!input) {
    toast.warning('请输入一段文本')
    return
  }
  simulating.value = true
  try {
    simulation.value = await agentApi.simulate(input)
  } catch (caught) {
    toast.error('模拟失败', errorMessage(caught))
  } finally {
    simulating.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="agent" data-test="abilities-agent">
    <ErrorState v-if="error" :message="error" @retry="load" />
    <LoadingState v-else-if="loading && !status" label="正在读取 Agent 状态…" :rows="4" />

    <template v-else-if="status">
      <section class="agent__section cb-card" data-test="agent-status">
        <SectionHeader title="Agent Runtime" description="状态由后端真实运行时推导，绝不猜测。">
          <template #actions>
            <StatusBadge :state="meta.state" :label="meta.label" data-test="agent-status-badge" />
          </template>
        </SectionHeader>
        <p class="agent__reason" :data-status="statusKey" data-test="agent-reason">{{ meta.reason }}</p>
      </section>

      <template v-if="isReady">
        <section class="agent__section cb-card" data-test="agent-tasks">
          <SectionHeader title="进行中的任务" description="仍在执行或等待中的 Agent 任务；点击打开任务详情。" />
          <table v-if="activeTasks.length > 0" class="agent__table" data-test="agent-active-table">
            <thead>
              <tr>
                <th scope="col">任务</th>
                <th scope="col">状态</th>
                <th scope="col">目标</th>
                <th scope="col">更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="task in activeTasks" :key="taskId(task)" data-test="agent-active-row">
                <td>
                  <RouterLink :to="`/abilities/agent/tasks/${taskId(task)}`" data-test="agent-task-link">
                    {{ taskId(task) }}
                  </RouterLink>
                </td>
                <td>{{ taskStatusLabel(task.status) }}</td>
                <td>{{ taskGoal(task) }}</td>
                <td>{{ formatTimestamp(task.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="cb-muted" data-test="agent-active-empty">当前没有进行中的任务。</p>
        </section>

        <section class="agent__section cb-card" data-test="agent-recent">
          <SectionHeader title="最近任务" description="最近快照（后端只保留有限条数）。" />
          <table v-if="recentTasks.length > 0" class="agent__table" data-test="agent-recent-table">
            <thead>
              <tr>
                <th scope="col">任务</th>
                <th scope="col">状态</th>
                <th scope="col">目标</th>
                <th scope="col">更新时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="task in recentTasks" :key="taskId(task)" data-test="agent-recent-row">
                <td>
                  <RouterLink :to="`/abilities/agent/tasks/${taskId(task)}`">
                    {{ taskId(task) }}
                  </RouterLink>
                </td>
                <td>{{ taskStatusLabel(task.status) }}</td>
                <td>{{ taskGoal(task) }}</td>
                <td>{{ formatTimestamp(task.updated_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="cb-muted">还没有任务记录。</p>
        </section>

        <section class="agent__section" data-test="agent-policy">
          <div class="agent__cards">
            <MetricCard label="规划模型" :value="status.planner_model || null" />
            <MetricCard label="评估模型" :value="status.evaluator_model || null" />
            <MetricCard label="健康" :value="describe(status.health)" />
          </div>
          <div class="agent__policy cb-card">
            <SectionHeader title="策略与预算" description="来自后端 policy_snapshot；没有数据显示「—」。" />
            <div class="agent__policy-grid">
              <dl class="agent__kv">
                <dt>预算</dt>
                <dd>
                  <ul v-if="budgetEntries.length > 0" class="agent__kv-list">
                    <li v-for="[key, value] in budgetEntries" :key="key">{{ key }}：{{ value }}</li>
                  </ul>
                  <span v-else>—</span>
                </dd>
              </dl>
              <dl class="agent__kv">
                <dt>策略</dt>
                <dd>
                  <ul v-if="policyEntries.length > 0" class="agent__kv-list">
                    <li v-for="[key, value] in policyEntries" :key="key">{{ key }}：{{ value }}</li>
                  </ul>
                  <span v-else>—</span>
                </dd>
              </dl>
            </div>
          </div>
        </section>

        <section class="agent__section cb-card" data-test="agent-simulate">
          <SectionHeader
            title="任务模拟"
            description="干跑：只做分类与规划，不执行工具，不会发送任何 QQ 消息。"
          />
          <form class="agent__simulate-form" data-test="agent-simulate-form" @submit.prevent="runSimulate">
            <input
              v-model="simulateText"
              type="text"
              class="agent__simulate-input"
              placeholder="输入一句话，例如：帮我查一下明天的天气"
              aria-label="任务模拟文本"
              data-test="agent-simulate-input"
            />
            <button type="submit" class="agent__button agent__button--primary" :disabled="simulating" data-test="agent-simulate-run">
              {{ simulating ? '模拟中…' : '模拟' }}
            </button>
          </form>
          <pre v-if="simulation" class="agent__pre" data-test="agent-simulate-result">{{ describe(simulation) }}</pre>
        </section>
      </template>

      <section v-else class="agent__section cb-card agent__disabled" data-test="agent-disabled">
        <h2 class="agent__disabled-title">尚未启用 Agent Runtime</h2>
        <p class="cb-muted">{{ meta.reason }}</p>
        <p class="cb-caption">
          启用后这里会显示进行中 / 最近任务、策略与预算；在此之前不会展示任何占位数据。
        </p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.agent {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.agent__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.agent__reason {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.agent__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.agent__table th,
.agent__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  overflow-wrap: anywhere;
}

.agent__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.agent__cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--cb-space-3);
}

.agent__policy {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.agent__policy-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cb-space-3);
}

.agent__kv dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.agent__kv dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.agent__kv-list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  margin: 0;
  padding-left: var(--cb-space-4);
}

.agent__simulate-form {
  display: flex;
  gap: var(--cb-space-2);
}

.agent__simulate-input {
  flex: 1;
  min-width: 0;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.agent__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
  white-space: nowrap;
}

.agent__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.agent__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.agent__pre {
  max-height: 320px;
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

.agent__disabled {
  align-items: flex-start;
}

.agent__disabled-title {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
}
</style>
