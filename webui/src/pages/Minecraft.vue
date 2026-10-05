<script setup lang="ts">
/**
 * Minecraft 连接控制页（Phase 1）：只有连接所需的最小集——
 * 服务器地址表单 + 加入/离开 + 状态事实。没有地图、背包、AI 控制台。
 * 数据全部来自 `GET /api/v1/minecraft`（3s 轮询对账），动作走 minecraftApi。
 */
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'

import { ApiError, errorMessage } from '@/api/client'
import { minecraftApi } from '@/api/minecraft'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type {
  MinecraftActionView,
  MinecraftOverview,
  MinecraftPathfinderInfo,
  MinecraftWorldView,
} from '@/types/minecraft'

const POLL_INTERVAL_MS = 3000

const overview = ref<MinecraftOverview | null>(null)
const world = ref<MinecraftWorldView | null>(null)
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
const isOnline = computed(() => phase.value === 'ONLINE')
const canLeave = computed(() => isActive.value)

// Phase 3B：Action Runtime 视图（IDLE = 从未有动作）
const action = computed<MinecraftActionView | null>(() => overview.value?.action ?? null)
// Phase 3C：Pathfinder 诊断 + move_to 目标输入
const pathfinder = computed<MinecraftPathfinderInfo | null>(() => overview.value?.pathfinder ?? null)
const moveTarget = reactive({ x: '', y: '', z: '' })

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

const TIME_PHASE_LABELS: Record<string, string> = {
  day: '白天',
  sunset: '日落',
  night: '夜晚',
  sunrise: '日出',
}

const semantic = computed(() => world.value?.semantic ?? null)
const worldSelf = computed(() => semantic.value?.self ?? null)
const worldEnvironment = computed(() => semantic.value?.environment ?? null)

function envValue(key: string): string {
  const value = (worldEnvironment.value as Record<string, unknown> | null)?.[key]
  if (key === 'time_phase' && typeof value === 'string') {
    return TIME_PHASE_LABELS[value] ?? value
  }
  return display(value)
}

function stringifyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function formatTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds <= 0) {
    return '—'
  }
  const date = new Date(seconds * 1000)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatElapsed(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`
}

async function lookAtTest(): Promise<void> {
  const position = connection.value?.position
  if (!position) {
    toast.error('没有可用坐标', '罐头不在世界里，无法 look_at')
    return
  }
  // 开发用测试坐标：罐头附近 (+5, 0, +5)
  const x = Math.round(position.x) + 5
  const y = Math.round(position.y)
  const z = Math.round(position.z) + 5
  working.value = true
  try {
    const result = await minecraftApi.lookAt(x, y, z)
    toast.success('look_at 已执行', `${result.action} · ${result.status} · ${result.action_id}`)
    await load(true)
  } catch (caught) {
    toast.error('look_at 失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

function prefillMoveTarget(): void {
  // 首次拿到位置时给一个「附近测试坐标」默认值（当前 +4 x），开发调试用
  const position = connection.value?.position
  if (!position) return
  if (moveTarget.x === '' && moveTarget.y === '' && moveTarget.z === '') {
    moveTarget.x = String(Math.round(position.x) + 4)
    moveTarget.y = String(Math.round(position.y))
    moveTarget.z = String(Math.round(position.z))
  }
}

async function moveTo(): Promise<void> {
  const raw = [moveTarget.x, moveTarget.y, moveTarget.z]
  if (raw.some((value) => String(value).trim() === '')) {
    toast.error('目标坐标不合法', 'X / Y / Z 都要填写')
    return
  }
  const [x, y, z] = raw.map(Number)
  if (![x, y, z].every((value) => Number.isFinite(value))) {
    toast.error('目标坐标不合法', 'X / Y / Z 都必须是数字')
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.moveTo(x, y, z)
    toast.success('move_to 已执行', `${result.action} · ${result.status}`)
    await load(true)
  } catch (caught) {
    toast.error('移动失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function stopAction(): Promise<void> {
  working.value = true
  try {
    const result = await minecraftApi.stop()
    toast.success(
      '已停止',
      result.cancelled.length > 0
        ? `取消动作：${result.cancelled.join('、')}`
        : '当前没有可取消的动作',
    )
    await load(true)
  } catch (caught) {
    toast.error('停止失败', errorMessage(caught))
  } finally {
    working.value = false
  }
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
    prefillMoveTarget()
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

async function loadWorld(): Promise<void> {
  // World Debug 数据独立加载：失败不打断连接状态显示（旧数据静默保留）。
  try {
    world.value = await minecraftApi.world()
  } catch {
    /* 保留上一份数据 */
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
  void loadWorld()
  pollTimer = setInterval(() => {
    if (working.value || document.hidden) return
    void load(true)
    void loadWorld()
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

        <section class="minecraft__card cb-card" data-test="mc-action">
          <SectionHeader
            title="Current Action"
            description="Action Runtime（Phase 3B）：look_at / stop 的实时状态；同一时间只有一个前台动作。"
          />
          <dl class="minecraft__facts" data-test="mc-action-facts">
            <div><dt>Action</dt><dd data-test="mc-action-name">{{ display(action?.action) }}</dd></div>
            <div><dt>Status</dt><dd data-test="mc-action-status">{{ display(action?.status) }}</dd></div>
            <div><dt>Action ID</dt><dd data-test="mc-action-id">{{ display(action?.action_id) }}</dd></div>
            <div><dt>Started</dt><dd>{{ formatTime(action?.started_at) }}</dd></div>
            <div><dt>Elapsed</dt><dd data-test="mc-action-elapsed">{{ formatElapsed(action?.elapsed_ms) }}</dd></div>
          </dl>
          <p v-if="pathfinder?.target" class="cb-caption" data-test="mc-moving-to">
            Moving to: {{ Math.round(pathfinder.target.x) }}
            {{ Math.round(pathfinder.target.y) }}
            {{ Math.round(pathfinder.target.z) }}
            <template v-if="pathfinder.moving">（正在移动）</template>
          </p>
          <div class="minecraft__form" data-test="mc-move-form">
            <label class="minecraft__field minecraft__field--coord">
              <span>Target X</span>
              <input v-model="moveTarget.x" type="number" :disabled="working || !isOnline" data-test="mc-move-x" />
            </label>
            <label class="minecraft__field minecraft__field--coord">
              <span>Target Y</span>
              <input v-model="moveTarget.y" type="number" :disabled="working || !isOnline" data-test="mc-move-y" />
            </label>
            <label class="minecraft__field minecraft__field--coord">
              <span>Target Z</span>
              <input v-model="moveTarget.z" type="number" :disabled="working || !isOnline" data-test="mc-move-z" />
            </label>
            <button
              type="button"
              class="minecraft__button minecraft__button--primary"
              :disabled="working || !isOnline"
              data-test="mc-move-to"
              @click="moveTo"
            >
              MOVE TO
            </button>
          </div>
          <div class="minecraft__form">
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !isOnline"
              data-test="mc-action-look"
              @click="lookAtTest"
            >
              Look At Test
            </button>
            <button
              type="button"
              class="minecraft__button minecraft__button--danger"
              :disabled="working"
              data-test="mc-action-stop"
              @click="stopAction"
            >
              STOP
            </button>
          </div>
          <p class="cb-caption">
            Look At Test 看向罐头附近 (+5, 0, +5) 的测试坐标（SAFE 动作，不改世界、不移动）；
            STOP 是最高优先级安全停止：取消进行中的动作并清空移动控制位（幂等）。
          </p>
        </section>

        <section
          v-if="phase === 'ONLINE' && semantic"
          class="minecraft__card cb-card"
          data-test="mc-world"
        >
          <SectionHeader
            title="World Debug（只读感知）"
            :description="`Raw World Snapshot → 语义模型；数据年龄 ${display(world?.age_seconds)} 秒。`"
          />
          <dl class="minecraft__facts" data-test="mc-world-env">
            <div><dt>生物群系</dt><dd>{{ envValue('biome') }}</dd></div>
            <div><dt>时间</dt><dd>{{ envValue('time_phase') }}</dd></div>
            <div><dt>天气</dt><dd>{{ envValue('weather') }}</dd></div>
            <div><dt>光照</dt><dd>{{ envValue('light') }}</dd></div>
            <div><dt>饥饿</dt><dd>{{ display(worldSelf?.food) }}</dd></div>
            <div><dt>游戏模式</dt><dd>{{ display(worldSelf?.game_mode) }}</dd></div>
            <div><dt>手持</dt><dd>{{ display(worldSelf?.held_item) }}</dd></div>
            <div><dt>朝向</dt><dd>{{ display(worldSelf?.yaw) }}</dd></div>
          </dl>

          <div v-if="(semantic?.players?.length ?? 0) > 0" data-test="mc-world-players">
            <h4 class="minecraft__subhead">附近玩家</h4>
            <table class="minecraft__table">
              <thead><tr><th>玩家</th><th>方向</th><th>距离</th></tr></thead>
              <tbody>
                <tr v-for="player in semantic?.players" :key="player.name">
                  <td>{{ player.name }}</td><td>{{ player.direction }}</td><td>{{ player.distance }} 格</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="cb-muted" data-test="mc-world-players-empty">附近没有其他玩家。</p>

          <div v-if="(semantic?.entities?.length ?? 0) > 0" data-test="mc-world-entities">
            <h4 class="minecraft__subhead">附近生物</h4>
            <table class="minecraft__table">
              <thead><tr><th>类型</th><th>数量</th><th>方位</th><th>最近</th></tr></thead>
              <tbody>
                <tr v-for="entity in semantic?.entities" :key="entity.type">
                  <td>{{ entity.type }}</td><td>×{{ entity.count }}</td>
                  <td>{{ display(entity.direction) }}</td><td>{{ display(entity.distance) }} 格</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="cb-muted" data-test="mc-world-entities-empty">附近没有发现生物。</p>

          <div v-if="(semantic?.points_of_interest?.length ?? 0) > 0" data-test="mc-world-poi">
            <h4 class="minecraft__subhead">兴趣点（功能方块 / 光源 / 矿石）</h4>
            <table class="minecraft__table">
              <thead><tr><th>方块</th><th>方向</th><th>距离</th></tr></thead>
              <tbody>
                <tr v-for="(poi, index) in semantic?.points_of_interest" :key="`${poi.type}-${index}`">
                  <td>{{ poi.type }}</td><td>{{ poi.direction }}</td><td>{{ poi.distance }} 格</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="(semantic?.terrain?.length ?? 0) > 0" data-test="mc-world-terrain">
            <h4 class="minecraft__subhead">地形摘要</h4>
            <p class="cb-muted">
              {{ (semantic?.terrain ?? []).map((item) => `${item.type}（${item.direction}）`).join('、') }}
            </p>
          </div>

          <details class="minecraft__details" data-test="mc-world-raw">
            <summary>Raw Snapshot / 语义模型 JSON</summary>
            <pre class="minecraft__pre">{{ stringifyJson(world?.semantic) }}</pre>
            <pre class="minecraft__pre">{{ stringifyJson(world?.raw) }}</pre>
          </details>
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

.minecraft__field--coord {
  min-width: 96px;
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

.minecraft__subhead {
  margin: var(--cb-space-2) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.minecraft__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.minecraft__table th,
.minecraft__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
}

.minecraft__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.minecraft__details summary {
  cursor: pointer;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.minecraft__pre {
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
  font-size: var(--cb-text-xs);
}
</style>
