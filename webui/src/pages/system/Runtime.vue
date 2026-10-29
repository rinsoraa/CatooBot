<script setup lang="ts">
/**
 * Runtime 运维页（W5 §51-§58）：进程 / 调度器 / 世界 / OneBot / AI / 数据库 / WebSocket。
 *
 * 只读投影来自 `systemApi.overview()` 与 `systemApi.runtime()`；「高级操作」默认收起，
 * 只有两个真实动作：手动推进一次世界 tick 与既有 runtime actions，全部需要确认。
 * 世界暂停 / 恢复 / 重置不在这里重复（属于「角色 · 世界」页）。
 */
import { computed, onMounted, ref } from 'vue'

import { errorMessage } from '@/api/client'
import { systemApi, type RuntimeDetail } from '@/api/system'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import { useRealtimeStore } from '@/stores/realtime'
import type { OverviewData, SchedulerSnapshot } from '@/types/runtime'

type ActionName = 'reload_persona' | 'reload_plugins' | 'reload_models' | 'restore_model_overrides'

interface PendingAction {
  kind: 'tick' | 'action'
  action?: ActionName
  title: string
  message: string
  detail: string
  confirmText: string
  danger: boolean
}

/** runtime 段的 scheduler 带 last_report；Hub 带 queue_size（契约 §7.4 末强化）。 */
interface SchedulerDetail extends SchedulerSnapshot {
  last_report?: Record<string, unknown> | null
}

interface HubSnapshot {
  subscribers?: number | null
  published?: number | null
  dropped?: number | null
  queue_size?: number | null
}

const ACTIONS: { name: ActionName; label: string }[] = [
  { name: 'reload_persona', label: '重载角色人设' },
  { name: 'reload_plugins', label: '重载插件' },
  { name: 'reload_models', label: '重载模型' },
  { name: 'restore_model_overrides', label: '恢复模型覆盖' },
]

function actionLabel(action: ActionName): string {
  return ACTIONS.find((item) => item.name === action)?.label ?? action
}

const realtime = useRealtimeStore()

const overview = ref<OverviewData | null>(null)
const runtime = ref<RuntimeDetail | null>(null)
const loading = ref(false)
const error = ref('')

const pending = ref<PendingAction | null>(null)
const showConfirm = ref(false)
const working = ref(false)
const tickReport = ref<Record<string, unknown> | null>(null)

const scheduler = computed<SchedulerDetail | null>(
  () =>
    (runtime.value?.scheduler ?? overview.value?.runtime?.scheduler ?? null) as SchedulerDetail | null,
)
const lastReport = computed<Record<string, unknown> | null>(() => scheduler.value?.last_report ?? null)
const hub = computed<HubSnapshot | null>(() => (runtime.value?.hub as HubSnapshot | null | undefined) ?? null)
const database = computed(() => runtime.value?.database ?? null)
const process = computed(() => runtime.value?.process ?? null)
const onebot = computed(() => runtime.value?.onebot ?? null)
const ai = computed(() => overview.value?.ai ?? null)
const world = computed(() => overview.value?.world ?? null)
const counts = computed(() => overview.value?.counts ?? {})

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  const parts: string[] = []
  if (days > 0) parts.push(`${days} 天`)
  if (hours > 0) parts.push(`${hours} 小时`)
  if (minutes > 0) parts.push(`${minutes} 分`)
  parts.push(`${secs} 秒`)
  return parts.join(' ')
}

function formatDateTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) return '—'
  const millis = seconds > 1e12 ? seconds : seconds * 1000
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(1)} ${units[index]}`
}

function boolState(value: boolean | null | undefined): StatusState {
  if (value === null || value === undefined) return 'idle'
  return value ? 'ok' : 'error'
}

function onebotState(): StatusState {
  const state = onebot.value?.state ?? ''
  if (state === 'running' || state === 'connected' || state === 'ready') return 'ok'
  if (state === 'disabled') return 'off'
  return 'warn'
}

function stringifyReport(value: Record<string, unknown> | null): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [overviewData, runtimeData] = await Promise.all([
      systemApi.overview(),
      systemApi.runtime(),
    ])
    overview.value = overviewData
    runtime.value = runtimeData
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

function askTick(): void {
  pending.value = {
    kind: 'tick',
    title: '手动推进一次世界 tick',
    message: '确定立即推进一次世界 tick 吗？',
    detail: '这会真实调用 RuntimeScheduler.tick_once()（受运行期锁保护）；不是模拟，可能改变世界状态。',
    confirmText: '推进一次',
    danger: true,
  }
  showConfirm.value = true
}

function askAction(action: ActionName): void {
  pending.value = {
    kind: 'action',
    action,
    title: actionLabel(action),
    message: `确定执行「${actionLabel(action)}」吗？`,
    detail:
      action === 'restore_model_overrides'
        ? '会清空模型覆盖并回到配置默认值；不会删除模型配置。'
        : '会调用后端既有运行时动作，立即在进程内生效。',
    confirmText: '执行',
    danger: action === 'restore_model_overrides',
  }
  showConfirm.value = true
}

async function confirmPending(): Promise<void> {
  const target = pending.value
  showConfirm.value = false
  pending.value = null
  if (!target) return
  working.value = true
  try {
    if (target.kind === 'tick') {
      const result = (await systemApi.tick()) as {
        ran: boolean
        minutes?: number
        report?: Record<string, unknown>
        ticks?: number
      }
      tickReport.value = result.report ?? null
      toast.success('已推进一次世界 tick', `ticks=${display(result.ticks)} · minutes=${display(result.minutes)}`)
    } else if (target.action) {
      const result = await systemApi.runAction(target.action)
      toast.success(actionLabel(target.action), result.detail || '已完成')
    }
    await load()
  } catch (caught) {
    toast.error(target.kind === 'tick' ? '推进失败' : '动作失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="runtime" data-test="system-runtime">
    <ErrorState v-if="error" :message="error" @retry="load" />
    <LoadingState v-else-if="loading && !overview && !runtime" label="正在读取运行时状态…" :rows="6" />

    <template v-else>
      <div class="runtime__grid">
        <section class="runtime__card cb-card" data-test="runtime-process">
          <SectionHeader title="Process" description="进程事实：版本与启动时间。" />
          <dl class="runtime__facts">
            <div><dt>运行时长</dt><dd>{{ formatDuration(process?.uptime_seconds) }}</dd></div>
            <div><dt>启动时间</dt><dd>{{ formatDateTime(process?.started_at) }}</dd></div>
            <div><dt>版本</dt><dd>{{ display(process?.version) }}</dd></div>
            <div><dt>Python</dt><dd>{{ display(process?.python) }}</dd></div>
          </dl>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-scheduler">
          <SectionHeader title="Scheduler" description="世界调度器的心跳与累计 tick。">
            <template #actions>
              <StatusBadge
                :state="boolState(scheduler?.running ?? null)"
                :label="scheduler?.running === true ? '运行中' : scheduler?.running === false ? '已停止' : '未知'"
              />
            </template>
          </SectionHeader>
          <dl class="runtime__facts">
            <div><dt>interval（秒）</dt><dd>{{ display(scheduler?.interval_seconds) }}</dd></div>
            <div><dt>ticks</dt><dd>{{ display(scheduler?.ticks) }}</dd></div>
            <div><dt>catchups</dt><dd>{{ display(scheduler?.catchups) }}</dd></div>
            <div><dt>上次 tick</dt><dd>{{ formatDateTime(scheduler?.last_tick_at) }}</dd></div>
          </dl>
          <p class="runtime__note" data-test="scheduler-note">
            interval 是调度器唤醒频率，不代表世界每 N 秒更新一次：世界只有在真实 tick 被调度并成功
            执行时才会推进。
          </p>
          <details v-if="lastReport" class="runtime__details">
            <summary>上次 tick 报告</summary>
            <pre class="runtime__pre">{{ stringifyReport(lastReport) }}</pre>
          </details>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-world">
          <SectionHeader title="World" description="世界快照（来自 overview，只读）。" />
          <dl class="runtime__facts">
            <div><dt>phase</dt><dd>{{ display(world?.phase) }}</dd></div>
            <div><dt>位置</dt><dd>{{ display(world?.location) }}</dd></div>
            <div><dt>当前动作</dt><dd>{{ display(world?.action?.name) }}</dd></div>
            <div><dt>world_revision</dt><dd>{{ display(world?.world_revision) }}</dd></div>
            <div><dt>cognitive_revision</dt><dd>{{ display(world?.cognitive_revision) }}</dd></div>
            <div><dt>开放目标</dt><dd>{{ display(counts.goals_open) }}</dd></div>
            <div><dt>开放承诺</dt><dd>{{ display(counts.commitments_open) }}</dd></div>
            <div><dt>会话</dt><dd>{{ world?.session?.active ? display(world?.session?.person_id) : '无' }}</dd></div>
          </dl>
          <p class="runtime__note" :class="{ 'runtime__note--warn': world?.interrupted === true }" data-test="world-interrupted">
            interrupted 诊断：{{ world?.interrupted === true ? '存在未完成的打断（interrupted=true）' : '当前没有未完成的打断' }}
          </p>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-onebot">
          <SectionHeader title="OneBot" description="QQ 网关只读计数；未启用时计数为「—」。">
            <template #actions>
              <StatusBadge :state="onebotState()" :label="display(onebot?.state)" />
            </template>
          </SectionHeader>
          <dl class="runtime__facts">
            <div><dt>connected</dt><dd>{{ display(onebot?.connected) }}</dd></div>
            <div><dt>self_id</dt><dd>{{ display(onebot?.self_id) }}</dd></div>
            <div><dt>最后事件</dt><dd>{{ formatDateTime(onebot?.last_event_at) }}</dd></div>
            <div><dt>received / accepted</dt><dd>{{ display(onebot?.received) }} / {{ display(onebot?.accepted) }}</dd></div>
            <div><dt>deduped / dropped</dt><dd>{{ display(onebot?.deduped) }} / {{ display(onebot?.dropped) }}</dd></div>
            <div><dt>sent / failed</dt><dd>{{ display(onebot?.sent) }} / {{ display(onebot?.failed) }}</dd></div>
            <div><dt>responses / self_ignored</dt><dd>{{ display(onebot?.responses) }} / {{ display(onebot?.self_ignored) }}</dd></div>
            <div><dt>pending_outbound</dt><dd>{{ display(onebot?.pending_outbound) }}</dd></div>
            <div><dt>busy</dt><dd>{{ display(onebot?.busy) }}</dd></div>
          </dl>
          <table v-if="onebot && onebot.lanes.length > 0" class="runtime__table" data-test="onebot-lanes">
            <thead>
              <tr>
                <th scope="col">lane</th>
                <th scope="col">pending</th>
                <th scope="col">busy</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="lane in onebot.lanes" :key="lane.lane">
                <td>{{ lane.lane }}</td>
                <td>{{ lane.pending }}</td>
                <td>{{ lane.busy ? '是' : '否' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="cb-muted">没有 lane（网关未启用或没有队列）。</p>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-ai">
          <SectionHeader title="AI" description="来自 overview 的 ai 块。" />
          <dl class="runtime__facts">
            <div><dt>enabled</dt><dd>{{ display(ai?.enabled) }}</dd></div>
            <div><dt>当前模型</dt><dd>{{ display(ai?.current_model) }}</dd></div>
            <div><dt>可用 / 总数</dt><dd>{{ display(ai?.models_ok) }} / {{ display(ai?.models_total) }}</dd></div>
            <div><dt>请求</dt><dd>{{ display(ai?.requests) }}</dd></div>
            <div><dt>错误</dt><dd>{{ display(ai?.errors) }}</dd></div>
            <div><dt>限流</dt><dd>{{ display(ai?.rate_limited) }}</dd></div>
          </dl>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-database">
          <SectionHeader title="Database" description="sqlite 文件大小未知时显示「—」，绝不猜测。" />
          <dl class="runtime__facts">
            <div><dt>connected</dt><dd>{{ display(database?.connected) }}</dd></div>
            <div><dt>size_bytes</dt><dd data-test="database-size">{{ formatBytes(database?.size_bytes) }}</dd></div>
          </dl>
        </section>

        <section class="runtime__card cb-card" data-test="runtime-ws">
          <SectionHeader title="WebSocket" description="后端 Hub 统计与当前前端连接状态。" />
          <dl class="runtime__facts">
            <div><dt>订阅者</dt><dd>{{ display(hub?.subscribers) }}</dd></div>
            <div><dt>published</dt><dd>{{ display(hub?.published) }}</dd></div>
            <div><dt>dropped</dt><dd>{{ display(hub?.dropped) }}</dd></div>
            <div><dt>queue_size</dt><dd>{{ display(hub?.queue_size) }}</dd></div>
            <div>
              <dt>前端连接</dt>
              <dd><span data-test="ws-connection">{{ realtime.label }}（{{ realtime.state }}）</span></dd>
            </div>
          </dl>
        </section>
      </div>

      <details class="runtime__advanced" data-test="runtime-advanced">
        <summary class="runtime__advanced-summary">高级操作</summary>
        <div class="runtime__advanced-body">
          <p class="cb-caption">
            这些操作会直接影响真实运行时；全部需要二次确认。世界的暂停 / 恢复 / 重置属于
            「角色 · 世界」页，这里不重复提供。
          </p>
          <div class="runtime__advanced-actions">
            <button type="button" class="runtime__button runtime__button--danger" :disabled="working" data-test="runtime-tick" @click="askTick">
              手动推进一次世界 tick
            </button>
            <button
              v-for="item in ACTIONS"
              :key="item.name"
              type="button"
              class="runtime__button"
              :disabled="working"
              :data-test="`runtime-action-${item.name}`"
              @click="askAction(item.name)"
            >
              {{ item.label }}
            </button>
          </div>
          <pre v-if="tickReport" class="runtime__pre" data-test="tick-report">{{ stringifyReport(tickReport) }}</pre>
        </div>
      </details>
    </template>

    <ConfirmDialog
      v-model:show="showConfirm"
      :title="pending?.title ?? '确认操作'"
      :message="pending?.message ?? ''"
      :detail="pending?.detail"
      :confirm-text="pending?.confirmText ?? '确认'"
      :danger="pending?.danger ?? false"
      @confirm="confirmPending"
    />
  </div>
</template>

<style scoped>
.runtime {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.runtime__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--cb-space-3);
}

.runtime__card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  min-width: 0;
}

.runtime__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.runtime__facts dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.runtime__facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.runtime__note {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.runtime__note--warn {
  border-color: var(--cb-warning);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
}

.runtime__details summary {
  cursor: pointer;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.runtime__pre {
  max-height: 260px;
  overflow: auto;
  margin: var(--cb-space-2) 0 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.runtime__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.runtime__table th,
.runtime__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
}

.runtime__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.runtime__advanced {
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.runtime__advanced-summary {
  cursor: pointer;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.runtime__advanced-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding-top: var(--cb-space-3);
}

.runtime__advanced-actions {
  display: flex;
  gap: var(--cb-space-2);
  flex-wrap: wrap;
}

.runtime__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.runtime__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.runtime__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.runtime__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
