<script setup lang="ts">
/**
 * 总览页（W3 §27）：四张指标卡 + 当前世界 + 实时事件。
 *
 * 数据只来自 runtime store（REST 首屏快照 + WebSocket 增量）与 realtime store；
 * 页面不轮询、不编造状态：拿不到的数据显示「—」或 EmptyState。
 */

import { NButton } from 'naive-ui'
import { computed, onMounted } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useRealtimeStore } from '@/stores/realtime'
import { useRuntimeStore } from '@/stores/runtime'
import { useWorldStore } from '@/stores/world'

const runtime = useRuntimeStore()
const realtime = useRealtimeStore()
const world = useWorldStore()

onMounted(() => {
  // 世界的详细信息（needs/goals/interrupted）走一次快照，之后由 world 主题触发刷新
  void world.loadWorld()
  // 首屏由 App.vue 触发；直接进入本页（例如刷新）时补一次快照。
  if (!runtime.overview && !runtime.loading) void runtime.refresh()
})

function refresh(): void {
  void runtime.refresh()
}

const showLoading = computed(() => runtime.loading && !runtime.overview)

const subtitle = computed(() => {
  const online = runtime.qq?.online
  if (online == null) return '等待运行数据'
  return online ? 'QQ 已连接' : 'QQ 离线'
})

// ---------------------------------------------------------------- QQ 指标
const qqValue = computed(() => {
  const online = runtime.qq?.online
  if (online == null) return null
  return online ? '在线' : '离线'
})
const qqState = computed<'ok' | 'off' | 'idle'>(() => {
  const online = runtime.qq?.online
  if (online == null) return 'idle'
  return online ? 'ok' : 'off'
})
const qqHint = computed(() => {
  const qq = runtime.qq
  if (!qq) return ''
  const parts: string[] = []
  if (qq.self_id != null) parts.push(`账号 ${qq.self_id}`)
  if (qq.messages_received != null) parts.push(`消息 ${qq.messages_received}`)
  return parts.join(' · ')
})

// ---------------------------------------------------------------- AI 指标
const ai = computed(() => runtime.overview?.ai ?? null)
const aiValue = computed(() => {
  const enabled = ai.value?.enabled
  if (enabled == null) return null
  return enabled ? '已启用' : '已停用'
})
const aiState = computed<'ok' | 'warn' | 'off' | 'idle'>(() => {
  const snapshot = ai.value
  if (!snapshot || snapshot.enabled == null) return 'idle'
  if (!snapshot.enabled) return 'off'
  const ok = snapshot.models_ok ?? 0
  const total = snapshot.models_total ?? 0
  return total > 0 && ok === 0 ? 'warn' : 'ok'
})
const aiHint = computed(() => {
  const snapshot = ai.value
  if (!snapshot) return ''
  const parts: string[] = []
  if (snapshot.models_total != null) {
    parts.push(`模型 ${snapshot.models_ok ?? 0}/${snapshot.models_total}`)
  }
  if (snapshot.requests != null) parts.push(`请求 ${snapshot.requests}`)
  return parts.join(' · ')
})

// ------------------------------------------------------------- 世界指标
const worldValue = computed(() => runtime.world?.phase ?? null)
const worldHint = computed(() => {
  const revision = runtime.world?.world_revision
  return revision != null ? `世界修订 ${revision}` : ''
})

// ------------------------------------------------------------ 运行时指标
const uptimeValue = computed(() => {
  const seconds = runtime.uptimeSeconds
  if (seconds == null) return null
  return formatDuration(seconds)
})
const runtimeHint = computed(() => {
  const scheduler = runtime.scheduler
  if (!scheduler) return ''
  const parts: string[] = []
  if (scheduler.ticks != null) parts.push(`调度 tick ${scheduler.ticks}`)
  if (scheduler.interval_seconds != null) parts.push(`间隔 ${scheduler.interval_seconds}s`)
  return parts.join(' · ')
})

// ------------------------------------------------------------ 当前世界
const worldMissing = computed(() => {
  const world = runtime.world
  if (!world) return true
  return !world.phase && !world.location && !world.action?.name && !(world.modes?.length ?? 0)
})
const worldAction = computed(() => runtime.world?.action?.name ?? '—')
const worldLocation = computed(() => runtime.world?.location ?? '—')
const worldModes = computed(() => {
  const modes = runtime.world?.modes
  return modes && modes.length > 0 ? modes.join('、') : '—'
})
// ------------------------------------------------------------ 实时事件
const connectionState = computed<'ok' | 'warn' | 'off'>(() => {
  if (realtime.state === 'connected') return 'ok'
  if (realtime.state === 'connecting' || realtime.state === 'reconnecting') return 'warn'
  return 'off'
})

// W5：世界摘要（真实数据，缺失一律 "—"）
const worldGoal = computed(() => world.goals[0] ?? null)
const worldGoalText = computed(() => {
  const goal = worldGoal.value
  if (!goal) return '—'
  const percent = Math.round((goal.progress ?? 0) * 100)
  return `${goal.title || goal.goal_id}（${percent}%）`
})
const worldNeedsText = computed(() => {
  const pressing = world.needsPressing
  if (pressing.length > 0) return pressing.join('、')
  const critical = runtime.world?.needs?.critical ?? []
  return critical.length > 0 ? critical.join('、') : '—'
})
const worldStale = computed(() => world.stale || !realtime.connected)

// W5 §107：社交上下文（当前会话 + 开放承诺数，全部来自后端）
const socialPerson = computed(() => world.world?.session?.person_id ?? runtime.world?.session?.person_id ?? '')
const socialActive = computed(() => Boolean(world.world?.session?.active ?? runtime.world?.session?.active))
const socialValue = computed(() => (socialActive.value ? socialPerson.value || '交互中' : '空闲'))
const socialHint = computed(() => {
  const counts = runtime.counts
  const open = counts.commitments_open
  return open === null || open === undefined ? '开放承诺 —' : `开放承诺 ${open}`
})

// W5 §60：只放高价值入口，不做按钮墙
const quickLinks = [
  { to: { name: 'ai-overview' }, label: '配置 AI' },
  { to: { name: 'character-world' }, label: '查看世界' },
  { to: { name: 'system-logs' }, label: '查看日志' },
  { to: { name: 'social-users' }, label: '查看社交' },
  { to: { name: 'memory' }, label: '查看记忆' },
]

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分`
  return `${minutes}分 ${total % 60}秒`
}

function formatTime(ts: number): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="dash" data-testid="dashboard">
    <PageHeader title="总览" :subtitle="subtitle">
      <template #actions>
        <NButton
          size="small"
          :loading="runtime.loading"
          aria-label="刷新数据"
          data-testid="dashboard-refresh"
          @click="refresh"
        >
          刷新
        </NButton>
      </template>
    </PageHeader>

    <ErrorState v-if="runtime.error" :message="runtime.error" @retry="refresh" />

    <LoadingState v-if="showLoading" label="正在读取运行状态…" :rows="4" />

    <template v-else>
      <div class="cb-grid cb-grid--cards" data-testid="dashboard-metrics">
        <MetricCard label="QQ" :value="qqValue" :hint="qqHint" :state="qqState" />
        <MetricCard label="AI" :value="aiValue" :hint="aiHint" :state="aiState" />
        <MetricCard label="世界" :value="worldValue" :hint="worldHint" />
        <MetricCard label="运行时" :value="uptimeValue" :hint="runtimeHint" />
        <MetricCard label="社交" :value="socialValue" :hint="socialHint" />
      </div>

      <section class="cb-card dash__section" data-testid="dashboard-quick-actions">
        <SectionHeader title="快捷入口" description="最常用的五件事" />
        <div class="dash__quick">
          <RouterLink v-for="link in quickLinks" :key="link.label" class="dash__quick-link" :to="link.to">
            {{ link.label }}
          </RouterLink>
        </div>
      </section>

      <section class="cb-card dash__section" data-testid="dashboard-world">
        <SectionHeader title="当前世界" description="沙箱正在做什么、在哪里" />
        <EmptyState
          v-if="worldMissing"
          title="暂无世界状态"
          description="沙箱未运行或尚未产生快照时，这里没有可展示的世界数据。"
        />
        <dl v-else class="dash__facts">
          <div class="dash__fact">
            <dt class="cb-caption">正在做什么</dt>
            <dd>{{ worldAction }}</dd>
          </div>
          <div class="dash__fact">
            <dt class="cb-caption">地点</dt>
            <dd>{{ worldLocation }}</dd>
          </div>
          <div class="dash__fact">
            <dt class="cb-caption">模式</dt>
            <dd>{{ worldModes }}</dd>
          </div>
          <div class="dash__fact">
            <dt class="cb-caption">需求</dt>
            <dd>{{ worldNeedsText }}</dd>
          </div>
          <div class="dash__fact">
            <dt class="cb-caption">当前目标</dt>
            <dd>{{ worldGoalText }}</dd>
          </div>
        </dl>
        <p v-if="worldStale" class="cb-caption" data-testid="dashboard-world-stale">
          数据可能不是最新（实时连接已断开）
        </p>
        <p class="cb-caption">
          <RouterLink :to="{ name: 'character-world' }">查看世界详情 →</RouterLink>
        </p>
      </section>

      <section class="cb-card dash__section" data-testid="dashboard-events">
        <SectionHeader title="实时事件" :description="`最近 ${realtime.feed.length} 条`">
          <template #actions>
            <StatusBadge :state="connectionState" :label="realtime.label" :pulse="connectionState === 'ok'" />
          </template>
        </SectionHeader>
        <EmptyState
          v-if="realtime.feed.length === 0"
          title="暂无实时事件"
          description="连接建立后，世界变化与日志会实时出现在这里。"
        />
        <ul v-else class="dash__feed">
          <li v-for="(entry, index) in realtime.feed" :key="`${entry.ts}-${index}`" class="dash__feed-item">
            <time class="dash__feed-time cb-faint" :datetime="new Date(entry.ts).toISOString()">
              {{ formatTime(entry.ts) }}
            </time>
            <span class="dash__feed-text">{{ entry.text }}</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.dash__quick {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
}

.dash__quick-link {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  text-decoration: none;
  font-size: var(--cb-text-sm);
}

.dash__quick-link:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.dash {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-5);
}

.dash__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.dash__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--cb-space-4);
  margin: 0;
}

.dash__fact dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.dash__feed {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 360px;
  overflow-y: auto;
}

.dash__feed-item {
  display: flex;
  gap: var(--cb-space-3);
  align-items: baseline;
  padding: var(--cb-space-2) 0;
  border-bottom: 1px solid var(--cb-border);
  font-size: var(--cb-text-sm);
}

.dash__feed-item:last-child {
  border-bottom: none;
}

.dash__feed-time {
  flex: none;
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.dash__feed-text {
  overflow-wrap: anywhere;
}
</style>
