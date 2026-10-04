<script setup lang="ts">
/**
 * Minecraft 连接控制页（Phase 1）：只有连接所需的最小集——
 * 服务器地址表单 + 加入/离开 + 状态事实。没有地图、背包、AI 控制台。
 * 数据全部来自 `GET /api/v1/minecraft`（3s 轮询对账），动作走 minecraftApi。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, errorMessage } from '@/api/client'
import { minecraftApi } from '@/api/minecraft'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type { MinecraftOverview } from '@/types/minecraft'

const POLL_INTERVAL_MS = 3000

const overview = ref<MinecraftOverview | null>(null)
const disabled = ref(false)
const loading = ref(false)
const error = ref('')
const working = ref(false)

const host = ref('')
const port = ref('25565')
const showLeaveConfirm = ref(false)

let pollTimer: ReturnType<typeof setInterval> | null = null

const connection = computed(() => overview.value?.connection ?? null)
const runtime = computed(() => overview.value?.runtime ?? null)
const phase = computed(() => connection.value?.status ?? 'DISCONNECTED')
const isActive = computed(() =>
  ['CONNECTING', 'AUTHENTICATING', 'CONNECTED', 'SPAWNING', 'ONLINE', 'DISCONNECTING'].includes(
    phase.value,
  ),
)
const canLeave = computed(() => isActive.value)

function phaseState(): StatusState {
  if (phase.value === 'ONLINE') return 'ok'
  if (phase.value === 'ERROR') return 'error'
  if (phase.value === 'DISCONNECTED') return 'idle'
  return 'warn'
}

const PHASE_LABELS: Record<string, string> = {
  DISCONNECTED: '未连接',
  CONNECTING: '连接中',
  AUTHENTICATING: '认证中',
  CONNECTED: '已连接',
  SPAWNING: '进入世界中',
  ONLINE: '在线',
  DISCONNECTING: '断开中',
  ERROR: '出错',
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function serverLabel(): string {
  const target = connection.value
  if (!target?.host) return '—'
  return target.port ? `${target.host}:${target.port}` : target.host
}

function positionLabel(): string {
  const position = connection.value?.position
  if (!position) return '—'
  return `${Math.round(position.x)}, ${Math.round(position.y)}, ${Math.round(position.z)}`
}

async function load(silent = false): Promise<void> {
  if (!silent) loading.value = true
  try {
    overview.value = await minecraftApi.overview()
    disabled.value = overview.value !== null && !overview.value.enabled
    error.value = ''
  } catch (caught) {
    // 连接层未启用是「功能状态」而不是故障：渲染引导卡，轮询也不必继续。
    if (caught instanceof ApiError && caught.code === 'minecraft.disabled') {
      disabled.value = true
      overview.value = null
      error.value = ''
      stopPolling()
      return
    }
    if (!silent) error.value = errorMessage(caught)
  } finally {
    if (!silent) loading.value = false
  }
}

async function join(): Promise<void> {
  const target = host.value.trim()
  const portNumber = Number(port.value || '25565')
  if (!target) {
    toast.error('缺少服务器地址', '先填写 Minecraft 服务器的 host')
    return
  }
  if (!Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
    toast.error('端口不合法', '端口必须是 1-65535 的整数')
    return
  }
  working.value = true
  try {
    await minecraftApi.join(target, portNumber)
    toast.success('正在加入服务器', `${target}:${portNumber}`)
    await load(true)
  } catch (caught) {
    toast.error('加入失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

function askLeave(): void {
  showLeaveConfirm.value = true
}

async function confirmLeave(): Promise<void> {
  showLeaveConfirm.value = false
  working.value = true
  try {
    await minecraftApi.leave()
    toast.success('已请求离开服务器', '罐头正在回来')
    await load(true)
  } catch (caught) {
    toast.error('离开失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

function stopPolling(): void {
  if (pollTimer !== null) clearInterval(pollTimer)
  pollTimer = null
}

onMounted(() => {
  void load()
  pollTimer = setInterval(() => {
    if (!working.value && !document.hidden) void load(true)
  }, POLL_INTERVAL_MS)
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="minecraft" data-test="minecraft-page">
    <ErrorState v-if="error" :message="error" @retry="load" />
    <LoadingState v-else-if="loading && !overview" label="正在读取 Minecraft 连接状态…" :rows="4" />

    <template v-else>
      <PageHeader
        title="Minecraft"
        subtitle="罐头的 Minecraft 连接层（Phase 1）：加入服务器、看基础状态、离开。"
      >
        <template #actions>
          <StatusBadge
            v-if="overview?.enabled"
            :state="phaseState()"
            :label="PHASE_LABELS[phase] ?? phase"
          />
        </template>
      </PageHeader>

      <section
        v-if="disabled || (overview && !overview.enabled)"
        class="minecraft__notice cb-card"
        data-test="minecraft-disabled"
      >
        <SectionHeader
          title="连接层未启用"
          description="在 config 里把 minecraft.enabled 设为 true（或经「系统 · 设置」开启）并重启后，这里才能控制连接。"
        />
        <p class="cb-caption">
          开启后 CatooBot 会托管一个本地 Minecraft Bridge runtime（Node.js + Mineflayer），
          账号认证配置在 <code>minecraft_runtime/auth.json</code>（参考 auth.example.json）。
        </p>
      </section>

      <template v-else>
        <p
          v-if="runtime?.down"
          class="minecraft__warning"
          data-test="minecraft-runtime-down"
        >
          Bridge runtime 不可达（已自动尝试重启 {{ runtime.restarts }} 次）；连接动作暂不可用。
        </p>

        <section class="minecraft__card cb-card" data-test="minecraft-join-card">
          <SectionHeader
            title="加入服务器"
            description="输入 Minecraft Java 服务器的地址与端口（默认 25565）。"
          />
          <form class="minecraft__form" @submit.prevent="join">
            <label class="minecraft__field">
              <span>Host</span>
              <input
                v-model="host"
                type="text"
                placeholder="例如 127.0.0.1 或 hypixel.net"
                :disabled="working || isActive"
                data-test="minecraft-host"
              />
            </label>
            <label class="minecraft__field minecraft__field--port">
              <span>Port</span>
              <input
                v-model="port"
                type="number"
                min="1"
                max="65535"
                :disabled="working || isActive"
                data-test="minecraft-port"
              />
            </label>
            <button
              type="submit"
              class="minecraft__button minecraft__button--primary"
              :disabled="working || isActive || runtime?.down"
              data-test="minecraft-join"
            >
              加入服务器
            </button>
          </form>
          <p v-if="overview && !overview.auth_configured" class="cb-caption" data-test="minecraft-auth-hint">
            未检测到 <code>minecraft_runtime/auth.json</code>：将以默认 offline 身份
            <code>GuanTou</code> 进入（仅适用于 offline 模式服务器）。
          </p>
        </section>

        <section class="minecraft__card cb-card" data-test="minecraft-status-card">
          <SectionHeader
            title="连接状态"
            description="来自 Bridge runtime 的真实世界状态；进世界与聊天由事件异步确认。"
          />
          <dl class="minecraft__facts" data-test="minecraft-status">
            <div><dt>Status</dt><dd data-test="minecraft-status-value">{{ display(PHASE_LABELS[phase] ?? phase) }}</dd></div>
            <div><dt>Server</dt><dd data-test="minecraft-server">{{ serverLabel() }}</dd></div>
            <div><dt>Username</dt><dd data-test="minecraft-username">{{ display(connection?.username) }}</dd></div>
            <div><dt>Dimension</dt><dd data-test="minecraft-dimension">{{ display(connection?.dimension) }}</dd></div>
            <div><dt>X / Y / Z</dt><dd data-test="minecraft-position">{{ positionLabel() }}</dd></div>
            <div><dt>Health</dt><dd data-test="minecraft-health">{{ display(connection?.health) }}</dd></div>
          </dl>
          <p v-if="connection?.last_error" class="minecraft__warning" data-test="minecraft-last-error">
            上次错误：{{ connection.last_error }}
          </p>
          <button
            v-if="canLeave"
            type="button"
            class="minecraft__button minecraft__button--danger"
            :disabled="working"
            data-test="minecraft-leave"
            @click="askLeave"
          >
            离开服务器
          </button>
        </section>
      </template>
    </template>

    <ConfirmDialog
      v-model:show="showLeaveConfirm"
      title="离开服务器"
      message="确定让罐头离开当前 Minecraft 服务器吗？"
      detail="会主动断开 Bridge 会话；服务器里的玩家会看到罐头退出。"
      confirm-text="离开"
      danger
      @confirm="confirmLeave"
    />
  </div>
</template>

<style scoped>
.minecraft {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.minecraft__notice,
.minecraft__card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  min-width: 0;
}

.minecraft__warning {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.minecraft__form {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.minecraft__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 240px;
}

.minecraft__field--port {
  min-width: 110px;
}

.minecraft__field span {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.minecraft__field input {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.minecraft__field input:disabled {
  opacity: 0.6;
}

.minecraft__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.minecraft__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.minecraft__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.minecraft__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.minecraft__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.minecraft__facts dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.minecraft__facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
</style>
