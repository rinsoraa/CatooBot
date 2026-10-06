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
  MinecraftAgentContext,
  MinecraftAgentToolRow,
  MinecraftConfirmationView,
  MinecraftInventorySlot,
  MinecraftInventorySlotsView,
  MinecraftInventoryView,
  MinecraftOverview,
  MinecraftPathfinderInfo,
  MinecraftPlaceFace,
  MinecraftWorldView,
} from '@/types/minecraft'

const POLL_INTERVAL_MS = 3000

const overview = ref<MinecraftOverview | null>(null)
const world = ref<MinecraftWorldView | null>(null)
// Phase 4C：只读背包切片（Place 面板要看到手里有什么）
const inventory = ref<MinecraftInventoryView | null>(null)
// Phase 4D：调试用原始槽位表（Move Test 照着它操作；模型看不到槽位）
const inventorySlots = ref<MinecraftInventorySlotsView | null>(null)
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
// Phase 3E：LLM Tool Debug（每个工具的风险/开关/是否允许 + Agent 上下文）
const agentTools = computed<MinecraftAgentToolRow[]>(() => overview.value?.agent?.tools ?? [])
const agentContext = computed<Partial<MinecraftAgentContext>>(
  () => overview.value?.agent?.context ?? {},
)
// Phase 4A：待确认动作（确认门）——只读列表；按钮只能取消/过期/造测试条
const confirmations = computed<MinecraftConfirmationView[]>(
  () => overview.value?.agent?.confirmations?.pending ?? [],
)
const trustedPlayers = computed<string[]>(() => overview.value?.agent?.trusted_players ?? [])

function confirmStatusLabel(row: MinecraftConfirmationView): string {
  return row.status === 'PENDING' ? '待确认' : row.status
}

async function createTestConfirmation(): Promise<void> {
  working.value = true
  try {
    await minecraftApi.confirm('create_test', { tool: 'minecraft_test_medium', risk: 'MEDIUM' })
    toast.success('已创建测试确认', '仅用于调试确认门；真实确认必须由用户在对话里做出')
    await load(true)
  } catch (caught) {
    toast.error('创建测试确认失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function cancelConfirmation(row: MinecraftConfirmationView, action: 'cancel' | 'expire') {
  working.value = true
  try {
    await minecraftApi.confirm(action, { confirmation_id: row.confirmation_id })
    toast.success(action === 'cancel' ? '已取消该确认' : '已把它置为过期', row.summary)
    await load(true)
  } catch (caught) {
    toast.error('操作失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

/** 风险等级 → 状态徽标的语义色（SAFE 绿 / LOW 黄 / 更高风险红）。 */
function riskState(risk: string): StatusState {
  if (risk === 'SAFE') return 'ok'
  if (risk === 'LOW') return 'warn'
  return 'error'
}

/** 工具是否允许 + 被拒原因（只读展示，不做任何执行）。 */
function toolState(row: MinecraftAgentToolRow): StatusState {
  if (!row.enabled) return 'idle'
  return row.allowed ? 'ok' : 'warn'
}

function toolLabel(row: MinecraftAgentToolRow): string {
  // 允许 → 「允许」；否则如实显示原因（minecraft.offline / tool.disabled …）
  return row.allowed ? '允许' : row.reason || '不允许'
}

function playerLabel(player: { name: string; distance: number | null }): string {
  const gap = player.distance === null || player.distance === undefined ? '' : ` ${player.distance} 格`
  return `${player.name}${gap}`
}
const moveTarget = reactive({ x: '', y: '', z: '' })
const followTarget = reactive({ username: '', distance: '2.5' })
// Phase 4B：Dig Test（第一个世界修改动作；必须过 MEDIUM 确认门）
const digTarget = reactive({ x: '', y: '', z: '', expectedBlock: '' })
// Phase 4C：Place Test（对称于 dig；六个 face + 主手物品约束）
const PLACE_FACES: MinecraftPlaceFace[] = ['up', 'down', 'north', 'south', 'east', 'west']
const placeTarget = reactive({
  x: '',
  y: '',
  z: '',
  face: 'up' as MinecraftPlaceFace,
  expectedItem: '',
})
// Phase 4D：Equip / Move Test（一次只操作一个物品 / 一个槽位；MEDIUM 必须确认）
const equipTarget = reactive({ item: '' })
const invMove = reactive({ source: '', destination: '', item: '', count: '1' })
const hotbarStart = computed(() => inventorySlots.value?.hotbar_start ?? 36)
const inventoryStart = computed(() => inventorySlots.value?.inventory_start ?? 9)
const slotRows = computed<MinecraftInventorySlot[]>(() => inventorySlots.value?.slots ?? [])
const heldItem = computed(() => inventory.value?.held_item ?? null)
/** 不是主手物品的第一个背包物品（Equip Test 的默认值：真的能换出东西来）。 */
const equipSuggestion = computed(() => {
  const held = heldItem.value?.name ?? ''
  const candidate = (inventory.value?.items ?? []).find((item) => item.name !== held)
  return candidate?.name ?? ''
})
const inventorySummary = computed(() => {
  const items = inventory.value?.items ?? []
  if (!items.length) return '背包是空的'
  return items
    .slice(0, 8)
    .map((item) => `${item.name}×${item.count}`)
    .join('、')
})

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
    // Phase 3E：导航是持续动作——启动即 RUNNING，终点由 Minecraft 事件确认
    toast.success('已开始移动', `${result.action} · ${result.status}（到达/失败会由事件更新）`)
    await load(true)
  } catch (caught) {
    toast.error('移动失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

function prefillDigTarget(): void {
  // 默认填「脚下前方」的一个可挖方块，只为开发调试方便
  const position = connection.value?.position
  if (!position) return
  if (digTarget.x === '' && digTarget.y === '' && digTarget.z === '') {
    digTarget.x = String(Math.round(position.x) + 2)
    digTarget.y = String(Math.round(position.y) - 1)
    digTarget.z = String(Math.round(position.z))
  }
  if (!digTarget.expectedBlock) digTarget.expectedBlock = 'minecraft:stone'
}

function prefillPlaceTarget(): void {
  // 默认填「脚前方一格」的空气位（face=up → 参考方块是它下面那格），期望物品跟随主手
  const position = connection.value?.position
  if (position) {
    if (placeTarget.x === '' && placeTarget.y === '' && placeTarget.z === '') {
      placeTarget.x = String(Math.round(position.x) + 2)
      placeTarget.y = String(Math.round(position.y))
      placeTarget.z = String(Math.round(position.z))
    }
  }
  if (!placeTarget.expectedItem && heldItem.value) {
    placeTarget.expectedItem = heldItem.value.name
  }
}

function useHeldItem(): void {
  if (heldItem.value) placeTarget.expectedItem = heldItem.value.name
}

async function placeBlock(): Promise<void> {
  const raw = [placeTarget.x, placeTarget.y, placeTarget.z]
  if (raw.some((value) => String(value).trim() === '')) {
    toast.error('目标坐标不合法', 'X / Y / Z 都要填写（方块坐标是整数）')
    return
  }
  const coords = raw.map(Number)
  if (!coords.every((value) => Number.isInteger(value))) {
    toast.error('目标坐标不合法', '方块坐标必须是整数（没有小数）')
    return
  }
  const item = placeTarget.expectedItem.trim()
  if (!item) {
    toast.error('缺少物品名', 'expected_item 必填（先用 minecraft_inventory 看主手拿着什么）')
    return
  }
  if (!PLACE_FACES.includes(placeTarget.face)) {
    toast.error('face 不合法', `只能是 ${PLACE_FACES.join(' / ')}`)
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.place(
      coords[0],
      coords[1],
      coords[2],
      placeTarget.face,
      item,
    )
    toast.success('已开始放置', `${result.action} · ${result.status}（结果会由事件确认）`)
    await load(true)
  } catch (caught) {
    // MEDIUM 动作必须用户确认：这里只会拿到 minecraft.confirmation_required
    toast.error('PLACE 被拒绝', errorMessage(caught))
    if (caught instanceof ApiError && catchConfirmationId(caught)) {
      toast.info('已挂起一条待确认', '确认只能由用户在对话里做出；这里只能 CANCEL / EXPIRE')
    }
  } finally {
    working.value = false
  }
}

async function digBlock(): Promise<void> {
  const raw = [digTarget.x, digTarget.y, digTarget.z]
  if (raw.some((value) => String(value).trim() === '')) {
    toast.error('目标坐标不合法', 'X / Y / Z 都要填写')
    return
  }
  const [x, y, z] = raw.map(Number)
  if (![x, y, z].every((value) => Number.isFinite(value))) {
    toast.error('目标坐标不合法', 'X / Y / Z 都必须是数字')
    return
  }
  const expected = digTarget.expectedBlock.trim()
  if (!expected) {
    toast.error('缺少方块名', 'expected_block 必填（先看清目标方块再用它的名字）')
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.dig(x, y, z, expected)
    toast.success('已开始挖掘', `${result.action} · ${result.status}（结果会由事件确认）`)
    await load(true)
  } catch (caught) {
    // MEDIUM 动作必须用户确认：这里只会拿到 minecraft.confirmation_required
    toast.error('DIG 被拒绝', errorMessage(caught))
    if (caught instanceof ApiError && catchConfirmationId(caught)) {
      toast.info('已挂起一条待确认', '确认只能由用户在对话里做出；这里只能 CANCEL / EXPIRE')
    }
  } finally {
    working.value = false
  }
}

function catchConfirmationId(error: ApiError): string {
  const detail = error.detail
  if (detail && typeof detail === 'object' && 'confirmation' in detail) {
    const confirmation = (detail as { confirmation?: { confirmation_id?: string } }).confirmation
    return confirmation?.confirmation_id ?? ''
  }
  return ''
}

async function followPlayer(): Promise<void> {
  const username = followTarget.username.trim()
  if (!username) {
    toast.error('缺少玩家名', '先填写要跟随的玩家 username')
    return
  }
  if (username.length > 16) {
    toast.error('玩家名过长', 'username 最长 16 个字符')
    return
  }
  const distance = Number(followTarget.distance === '' ? '2.5' : followTarget.distance)
  if (!Number.isFinite(distance) || distance < 1.5 || distance > 6) {
    toast.error('距离不合法', 'distance 必须在 1.5 ~ 6 格之间')
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.followPlayer(username, distance)
    toast.success('follow_player 已执行', `${result.action} · ${result.status}（持续跟随，STOP 可停）`)
    await load(true)
  } catch (caught) {
    toast.error('跟随失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function equipItem(): Promise<void> {
  const item = equipTarget.item.trim()
  if (!item) {
    toast.error('缺少物品名', '先填写要拿到手里的物品（背包里必须真的存在）')
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.equip(item)
    toast.success('已开始换手', `${result.action} · ${result.status}（结果会由事件确认）`)
    await load(true)
  } catch (caught) {
    // MEDIUM 动作必须用户确认：这里只会拿到 minecraft.confirmation_required
    toast.error('EQUIP 被拒绝', errorMessage(caught))
    if (caught instanceof ApiError && catchConfirmationId(caught)) {
      toast.info('已挂起一条待确认', '确认只能由用户在对话里做出；这里只能 CANCEL / EXPIRE')
    }
  } finally {
    working.value = false
  }
}

async function moveSlot(): Promise<void> {
  const source = Number(invMove.source)
  const destination = Number(invMove.destination)
  const count = Number(invMove.count)
  const item = invMove.item.trim()
  const low = inventoryStart.value
  const high = hotbarStart.value + 8
  if (!Number.isInteger(source) || source < low || source > high) {
    toast.error('source 槽位不合法', `必须是 ${low}~${high} 的整数（主背包 9-35 + 快捷栏 36-44）`)
    return
  }
  if (!Number.isInteger(destination) || destination < low || destination > high) {
    toast.error('destination 槽位不合法', `必须是 ${low}~${high} 的整数`)
    return
  }
  if (source === destination) {
    toast.error('槽位冲突', 'source 与 destination 不能相同')
    return
  }
  if (!item) {
    toast.error('缺少物品名', 'source 槽位上的物品名必须与之一致（照着槽位表填）')
    return
  }
  if (!Number.isInteger(count) || count < 1) {
    toast.error('数量不合法', 'count 必须是 >= 1 的整数')
    return
  }
  working.value = true
  try {
    const result = await minecraftApi.inventoryMove(source, destination, item, count)
    toast.success('已开始搬运', `${result.action} · ${result.status}（结果会由事件确认）`)
    await load(true)
  } catch (caught) {
    // MEDIUM 动作必须用户确认：这里只会拿到 minecraft.confirmation_required
    toast.error('MOVE 被拒绝', errorMessage(caught))
    if (caught instanceof ApiError && catchConfirmationId(caught)) {
      toast.info('已挂起一条待确认', '确认只能由用户在对话里做出；这里只能 CANCEL / EXPIRE')
    }
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
    prefillDigTarget()
    prefillPlaceTarget()
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
  // Phase 4C：背包切片（Place 面板要显示主手物品；离线时后端也回 200/online=false）
  try {
    inventory.value = await minecraftApi.inventory()
  } catch {
    /* 保留上一份数据 */
  }
  // Phase 4D：原始槽位表（Move Test 的输入参考；同样是只读、恒 200）
  try {
    inventorySlots.value = await minecraftApi.inventorySlots()
  } catch {
    /* 保留上一份数据 */
  }
  prefillInventoryTargets()
}

/** Move Test 默认值：第一个有东西的槽 → 第一个空槽（都是 9~44 里的真实槽位）。 */
function prefillInventoryTargets(): void {
  if (!equipTarget.item && equipSuggestion.value) equipTarget.item = equipSuggestion.value
  const rows = slotRows.value
  if (!rows.length) return
  const occupied = new Set(rows.map((row) => row.slot))
  if (invMove.source === '') invMove.source = String(rows[0]!.slot)
  if (invMove.item === '') invMove.item = rows[0]!.name
  if (invMove.destination === '') {
    const empty: number[] = []
    for (let slot = inventoryStart.value; slot <= hotbarStart.value + 8; slot += 1) {
      if (!occupied.has(slot)) empty.push(slot)
    }
    if (empty.length) invMove.destination = String(empty[0]!)
  }
}

/** 照着槽位表点一行：填进 Move Test 的 source / item（目标槽仍需人自己选）。 */
function useSlot(row: MinecraftInventorySlot): void {
  invMove.source = String(row.slot)
  invMove.item = row.name
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
          <p
            v-if="pathfinder?.goal === 'GoalFollow' && pathfinder.target"
            class="cb-caption"
            data-test="mc-following"
          >
            Following: {{ pathfinder.target.username ?? '—' }} · Distance:
            {{ display(pathfinder.distance) }}
            <template v-if="pathfinder.moving">（正在移动）</template>
          </p>
          <div class="minecraft__form" data-test="mc-follow-form">
            <label class="minecraft__field minecraft__field--port">
              <span>Player</span>
              <input
                v-model="followTarget.username"
                type="text"
                placeholder="空凛"
                :disabled="working || !isOnline"
                data-test="mc-follow-username"
              />
            </label>
            <label class="minecraft__field minecraft__field--coord">
              <span>Distance</span>
              <input
                v-model="followTarget.distance"
                type="number"
                step="0.5"
                min="1.5"
                max="6"
                :disabled="working || !isOnline"
                data-test="mc-follow-distance"
              />
            </label>
            <button
              type="button"
              class="minecraft__button minecraft__button--primary"
              :disabled="working || !isOnline"
              data-test="mc-follow"
              @click="followPlayer"
            >
              FOLLOW
            </button>
          </div>
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

        <section class="minecraft__card cb-card" data-test="mc-dig">
          <SectionHeader
            title="Dig Test（Phase 4B · MEDIUM）"
            description="破坏一个指定方块：真实修改世界，必须用户确认。WebUI 只能发起（拿到 confirmation_required），真正的确认由用户在对话里做出。"
          />
          <div class="minecraft__form" data-test="mc-dig-form">
            <label class="minecraft__field">
              <span>X</span>
              <input v-model="digTarget.x" type="text" inputmode="numeric" data-test="mc-dig-x" />
            </label>
            <label class="minecraft__field">
              <span>Y</span>
              <input v-model="digTarget.y" type="text" inputmode="numeric" data-test="mc-dig-y" />
            </label>
            <label class="minecraft__field">
              <span>Z</span>
              <input v-model="digTarget.z" type="text" inputmode="numeric" data-test="mc-dig-z" />
            </label>
            <label class="minecraft__field">
              <span>Expected Block</span>
              <input
                v-model="digTarget.expectedBlock"
                type="text"
                placeholder="minecraft:stone"
                data-test="mc-dig-block"
              />
            </label>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !isOnline"
              data-test="mc-dig-run"
              @click="digBlock"
            >
              DIG
            </button>
            <button
              type="button"
              class="minecraft__button minecraft__button--danger"
              :disabled="working"
              data-test="mc-dig-stop"
              @click="stopAction"
            >
              STOP
            </button>
          </div>
          <p class="cb-caption">
            只会挖掉指定的那一个方块：不找矿、不换目标、不连续挖、不导航、不换工具、不捡掉落物。
            方块和用 minecraft_world 看到的不一致时会拒绝（block_changed）。
          </p>
        </section>

        <section class="minecraft__card cb-card" data-test="mc-place">
          <SectionHeader
            title="Place Test（Phase 4C · MEDIUM）"
            description="放置一个方块：真实修改世界，必须用户确认。expected_item 是主手物品的硬约束（不会自动装备或切槽）。"
          />
          <dl class="minecraft__facts" data-test="mc-place-facts">
            <div>
              <dt>主手物品</dt>
              <dd data-test="mc-place-held">
                {{
                  heldItem
                    ? `${heldItem.name} × ${heldItem.count}`
                    : inventory?.online
                      ? '空手'
                      : '不在世界里'
                }}
              </dd>
            </div>
            <div>
              <dt>快捷栏槽</dt>
              <dd data-test="mc-place-slot">
                {{
                  inventory?.selected_hotbar_slot === null ||
                  inventory?.selected_hotbar_slot === undefined
                    ? '—'
                    : Number(inventory.selected_hotbar_slot) + 1
                }}
              </dd>
            </div>
            <div>
              <dt>背包</dt>
              <dd data-test="mc-place-inventory">{{ inventorySummary }}</dd>
            </div>
          </dl>
          <div class="minecraft__form" data-test="mc-place-form">
            <label class="minecraft__field">
              <span>X</span>
              <input v-model="placeTarget.x" type="text" inputmode="numeric" data-test="mc-place-x" />
            </label>
            <label class="minecraft__field">
              <span>Y</span>
              <input v-model="placeTarget.y" type="text" inputmode="numeric" data-test="mc-place-y" />
            </label>
            <label class="minecraft__field">
              <span>Z</span>
              <input v-model="placeTarget.z" type="text" inputmode="numeric" data-test="mc-place-z" />
            </label>
            <label class="minecraft__field">
              <span>Face</span>
              <select v-model="placeTarget.face" data-test="mc-place-face">
                <option v-for="face in PLACE_FACES" :key="face" :value="face">{{ face }}</option>
              </select>
            </label>
            <label class="minecraft__field">
              <span>Expected Item</span>
              <input
                v-model="placeTarget.expectedItem"
                type="text"
                placeholder="dirt"
                data-test="mc-place-item"
              />
            </label>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working"
              data-test="mc-place-use-held"
              @click="useHeldItem"
            >
              用手持物品填入
            </button>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !isOnline"
              data-test="mc-place-run"
              @click="placeBlock"
            >
              PLACE
            </button>
            <button
              type="button"
              class="minecraft__button minecraft__button--danger"
              :disabled="working"
              data-test="mc-place-stop"
              @click="stopAction"
            >
              STOP
            </button>
          </div>
          <p class="cb-caption">
            只会放掉指定的那一个方块：只往空气里放（不覆盖草/水/雪）、只用主手物品、不导航、
            不自动找放置面、不换快捷栏、不自动补货、不连续建造。参考方块 = 目标沿 face 反方向一格，
            必须是实心方块；主手物品与 expected_item 不一致时会拒绝（held_item_changed）。
          </p>
        </section>

        <section class="minecraft__card cb-card" data-test="mc-inventory-control">
          <SectionHeader
            title="Inventory Control（Phase 4D · MEDIUM）"
            description="一次只操作一个明确物品 / 一个明确槽位：EQUIP 换主手、MOVE 搬一格。两者都是 MEDIUM，必须用户确认（这里只能发起）。"
          />
          <dl class="minecraft__facts" data-test="mc-inventory-facts">
            <div>
              <dt>主手物品</dt>
              <dd data-test="mc-inventory-held">
                {{
                  heldItem
                    ? `${heldItem.name} × ${heldItem.count}`
                    : inventory?.online
                      ? '空手'
                      : '不在世界里'
                }}
              </dd>
            </div>
            <div>
              <dt>槽位范围</dt>
              <dd data-test="mc-inventory-range">
                {{ inventoryStart }}–{{ hotbarStart + 8 }}（快捷栏从 {{ hotbarStart }} 起）
              </dd>
            </div>
            <div>
              <dt>背包</dt>
              <dd data-test="mc-inventory-summary">{{ inventorySummary }}</dd>
            </div>
          </dl>

          <div class="minecraft__form" data-test="mc-equip-form">
            <label class="minecraft__field">
              <span>Item</span>
              <input
                v-model="equipTarget.item"
                type="text"
                placeholder="dirt"
                data-test="mc-equip-item"
              />
            </label>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !equipSuggestion"
              data-test="mc-equip-suggest"
              @click="equipTarget.item = equipSuggestion"
            >
              填一个不在手里的物品
            </button>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !isOnline"
              data-test="mc-equip-run"
              @click="equipItem"
            >
              EQUIP
            </button>
            <button
              type="button"
              class="minecraft__button minecraft__button--danger"
              :disabled="working"
              data-test="mc-equip-stop"
              @click="stopAction"
            >
              STOP
            </button>
          </div>

          <div class="minecraft__form" data-test="mc-invmove-form">
            <label class="minecraft__field">
              <span>Source Slot</span>
              <input
                v-model="invMove.source"
                type="text"
                inputmode="numeric"
                data-test="mc-invmove-source"
              />
            </label>
            <label class="minecraft__field">
              <span>Destination Slot</span>
              <input
                v-model="invMove.destination"
                type="text"
                inputmode="numeric"
                data-test="mc-invmove-destination"
              />
            </label>
            <label class="minecraft__field">
              <span>Item</span>
              <input
                v-model="invMove.item"
                type="text"
                placeholder="dirt"
                data-test="mc-invmove-item"
              />
            </label>
            <label class="minecraft__field">
              <span>Count</span>
              <input
                v-model="invMove.count"
                type="text"
                inputmode="numeric"
                data-test="mc-invmove-count"
              />
            </label>
            <button
              type="button"
              class="minecraft__button"
              :disabled="working || !isOnline"
              data-test="mc-invmove-run"
              @click="moveSlot"
            >
              MOVE
            </button>
            <button
              type="button"
              class="minecraft__button minecraft__button--danger"
              :disabled="working"
              data-test="mc-invmove-stop"
              @click="stopAction"
            >
              STOP
            </button>
          </div>

          <table class="minecraft__table" data-test="mc-slot-table">
            <thead>
              <tr>
                <th>槽位</th>
                <th>物品</th>
                <th>数量</th>
                <th>区域</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in slotRows" :key="row.slot" :data-test="`mc-slot-${row.slot}`">
                <td>{{ row.slot }}</td>
                <td>{{ row.name }}</td>
                <td>{{ row.count }}</td>
                <td>{{ row.hotbar ? '快捷栏' : '主背包' }}</td>
                <td>
                  <button
                    type="button"
                    class="minecraft__button"
                    :disabled="working"
                    :data-test="`mc-slot-use-${row.slot}`"
                    @click="useSlot(row)"
                  >
                    用作 source
                  </button>
                </td>
              </tr>
              <tr v-if="!slotRows.length">
                <td colspan="5" class="cb-caption">
                  没有可显示的槽位（不在世界里，或背包是空的）。
                </td>
              </tr>
            </tbody>
          </table>

          <p class="cb-caption">
            一次只动一个物品、一个来源槽、一个目标槽、一个数量：EQUIP 只换主手（不碰盔甲/副手、不切快捷栏、
            不挑更合适的 stack，按槽位顺序取第一个匹配项）；MOVE 目标槽被别的物品占用时直接拒绝，
            <strong>绝不隐式交换</strong>。两张表都不是模型的数据源——模型只看 minecraft_inventory 的聚合切片，
            槽位只在这里（和 smoke）出现。不批量整理、不自动补货、不操作箱子/熔炉（后续阶段）。
          </p>
        </section>

        <section class="minecraft__card cb-card" data-test="mc-agent">
          <SectionHeader
            title="LLM Tool Debug（只读）"
            description="模型能用的 Minecraft 工具、风险分级与当前是否允许；上下文随 action 事件更新，此面板只读。"
          />
          <dl class="minecraft__facts" data-test="mc-agent-context">
            <div>
              <dt>Online</dt>
              <dd data-test="mc-agent-online">{{ agentContext.online ? '在线' : '不在世界' }}</dd>
            </div>
            <div>
              <dt>Dimension</dt>
              <dd data-test="mc-agent-dimension">{{ display(agentContext.dimension) }}</dd>
            </div>
            <div>
              <dt>Position</dt>
              <dd data-test="mc-agent-position">
                {{
                  agentContext.position
                    ? `${Math.round(agentContext.position.x)} ${Math.round(agentContext.position.y)} ${Math.round(agentContext.position.z)}`
                    : '—'
                }}
              </dd>
            </div>
            <div>
              <dt>Nearby Players</dt>
              <dd data-test="mc-agent-players">
                {{
                  agentContext.players && agentContext.players.length
                    ? agentContext.players.map(playerLabel).join('、')
                    : '—'
                }}
              </dd>
            </div>
            <div>
              <dt>Current Action</dt>
              <dd data-test="mc-agent-current">
                {{
                  agentContext.current_action
                    ? `${agentContext.current_action.action} · ${agentContext.current_action.status}`
                    : '—'
                }}
              </dd>
            </div>
            <div>
              <dt>Last Action</dt>
              <dd data-test="mc-agent-last">
                {{
                  agentContext.last_action
                    ? `${agentContext.last_action.action} · ${agentContext.last_action.status}${
                        agentContext.last_action.code ? `（${agentContext.last_action.code}）` : ''
                      }`
                    : '—'
                }}
              </dd>
            </div>
            <div>
              <dt>Activity</dt>
              <dd data-test="mc-agent-activity">{{ display(agentContext.activity) }}</dd>
            </div>
          </dl>

          <table class="minecraft__table" data-test="mc-agent-tools">
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Risk</th>
                <th scope="col">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in agentTools" :key="row.name" :data-test="`mc-agent-tool-${row.name}`">
                <td><code>{{ row.name }}</code></td>
                <td><StatusBadge :state="riskState(row.risk)" :label="row.risk" /></td>
                <td><StatusBadge :state="toolState(row)" :label="toolLabel(row)" /></td>
              </tr>
              <tr v-if="!agentTools.length">
                <td colspan="3" class="cb-caption">Minecraft 未启用：所有 Minecraft 工具都不可用。</td>
              </tr>
            </tbody>
          </table>
          <p class="cb-caption">
            LOW 动作（移动 / 跟随）只有在用户明确要求的对话里才会执行；模型自己想动也会被拒。
            会修改世界的动作有四个：minecraft_dig / minecraft_place（各一个方块）、
            minecraft_equip（换主手）、minecraft_inventory_move（搬一格）——它们都是 MEDIUM，
            除了用户明确要求，还必须经过确认门。连续挖矿/建造、攻击、合成、容器、
            批量整理背包等能力都还没有。
          </p>
        </section>

        <section class="minecraft__card cb-card" data-test="mc-confirmation">
          <SectionHeader
            title="Pending Confirmation（Phase 4A）"
            description="MEDIUM/HIGH 动作需要用户确认：确认绑定 用户 + 会话 + 参数指纹，一次性、限时；这里只能取消或置为过期，不能代替用户确认。"
          />
          <dl class="minecraft__facts" data-test="mc-confirmation-facts">
            <div>
              <dt>Trusted Players</dt>
              <dd data-test="mc-trusted-players">
                {{ trustedPlayers.length ? trustedPlayers.join('、') : '（未配置：游戏内 LOW 动作不执行）' }}
              </dd>
            </div>
            <div>
              <dt>TTL</dt>
              <dd data-test="mc-confirmation-ttl">
                {{ overview?.agent?.confirmations?.ttl_seconds ?? '—' }} 秒
              </dd>
            </div>
          </dl>
          <table class="minecraft__table" data-test="mc-confirmation-table">
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">Risk</th>
                <th scope="col">Target</th>
                <th scope="col">User</th>
                <th scope="col">Expires</th>
                <th scope="col">Status</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in confirmations"
                :key="row.confirmation_id"
                :data-test="`mc-confirmation-${row.confirmation_id}`"
              >
                <td><code>{{ row.tool }}</code></td>
                <td><StatusBadge :state="riskState(row.risk)" :label="row.risk" /></td>
                <td>{{ row.summary }}</td>
                <td>{{ row.user_id }}</td>
                <td>{{ formatTime(row.expires_at) }}</td>
                <td><StatusBadge state="warn" :label="confirmStatusLabel(row)" /></td>
                <td>
                  <button
                    type="button"
                    class="minecraft__button"
                    :disabled="working"
                    :data-test="`mc-confirmation-cancel-${row.confirmation_id}`"
                    @click="cancelConfirmation(row, 'cancel')"
                  >
                    CANCEL
                  </button>
                  <button
                    type="button"
                    class="minecraft__button"
                    :disabled="working"
                    :data-test="`mc-confirmation-expire-${row.confirmation_id}`"
                    @click="cancelConfirmation(row, 'expire')"
                  >
                    EXPIRE
                  </button>
                </td>
              </tr>
              <tr v-if="!confirmations.length">
                <td colspan="7" class="cb-caption">
                  没有待确认的动作（MEDIUM 动作发起后才会出现待确认）。可用下面的按钮造一条测试确认。
                </td>
              </tr>
            </tbody>
          </table>
          <button
            type="button"
            class="minecraft__button"
            :disabled="working || !overview?.enabled"
            data-test="mc-confirmation-create-test"
            @click="createTestConfirmation"
          >
            CREATE TEST CONFIRMATION
          </button>
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
