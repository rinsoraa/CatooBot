/** Minecraft 连接层（Phase 1）的类型，与 docs/WEBUI_API_CONTRACT.md §7.5 对齐。 */

/** 显式状态机（任务书 §生命周期），镜像自 Bridge runtime。 */
export type MinecraftPhase =
  | 'DISCONNECTED'
  | 'CONNECTING'
  | 'AUTHENTICATING'
  | 'CONNECTED'
  | 'SPAWNING'
  | 'ONLINE'
  | 'DISCONNECTING'
  | 'ERROR'

export interface MinecraftPosition {
  x: number
  y: number
  z: number
}

export interface MinecraftConnection {
  status: MinecraftPhase
  session_id: string | null
  host: string | null
  port: number | null
  username: string | null
  auth_mode: string | null
  dimension: string | null
  position: MinecraftPosition | null
  health: number | null
  last_error: string | null
  kicked_reason: string | null
  connected_at: number | null
}

export interface MinecraftRuntimeInfo {
  running: boolean
  pid: number | null
  managed: boolean
  restarts: number
  down: boolean
  log_tail: string[]
}

export interface MinecraftOverview {
  enabled: boolean
  auth_configured: boolean
  runtime: MinecraftRuntimeInfo
  connection: MinecraftConnection
  /** Phase 3B：当前/最近一次动作（IDLE = 从未有动作）。 */
  action?: MinecraftActionView
  /** Phase 3C：Pathfinder 诊断（goal 类型 / 目标坐标 / 是否在移动）。 */
  pathfinder?: MinecraftPathfinderInfo
  /** Phase 3E：LLM Tool Debug（每个工具的风险/开关/是否允许 + Agent 上下文）。 */
  agent?: MinecraftAgentView
  last_event: Record<string, unknown> | null
}

// ---------------- Phase 3B · Action Runtime ----------------

export type MinecraftActionStatus =
  | 'IDLE'
  | 'QUEUED'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMEOUT'

export interface MinecraftActionView {
  action: string | null
  action_id: string | null
  status: MinecraftActionStatus
  started_at: number | null
  finished_at: number | null
  elapsed_ms: number | null
  /** runtime 里进行中的动作数（含非互斥 chat）。 */
  active_count?: number
}

export interface MinecraftLookAtResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
}

export interface MinecraftStopResult {
  status: string
  cancelled: string[]
}

// ---------------- Phase 3C · Navigation (move_to) ----------------

export interface MinecraftPathfinderInfo {
  goal: string | null
  /** GoalFollow 时带 username；GoalNear 时只有坐标。 */
  target: { username?: string | null; x: number; y: number; z: number } | null
  distance: number | null
  moving: boolean
}

export interface MinecraftFollowPlayerResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
}

export interface MinecraftMoveToResult {
  action_id: string
  action: string
  status: MinecraftActionStatus
  result?: {
    target: { x: number; y: number; z: number }
    final_position: { x: number; y: number; z: number }
    /** Phase 4H.1：到达判定的口径（罐头占的方块格 → 目标方块格），completed 时必 <= 1.5 */
    distance_to_target: number
    /** Phase 4H.1：同一瞬间重新读到的实际位置算出的浮点三维距离（含站立高度差） */
    raw_distance_to_target?: number
  }
}

export interface MinecraftJoinResult {
  session_id: string
  status: MinecraftPhase
}

export interface MinecraftLeaveResult {
  ok?: boolean
  status: MinecraftPhase
}

// ---------------- Phase 2 · World Perception ----------------

export interface MinecraftWorldPlayer {
  name: string
  direction: string
  distance: number
  compass?: string
}

export interface MinecraftWorldEntity {
  type: string
  count: number
  direction?: string | null
  distance?: number
}

export interface MinecraftWorldPoi {
  type: string
  direction: string
  distance: number
  compass?: string
  pos?: { x: number; y: number; z: number } | null
}

export interface MinecraftTerrainItem {
  type: string
  direction: string
  distance?: number
  samples?: number
}

export interface MinecraftSemantic {
  captured_at: number
  self?: {
    location?: string | null
    dimension?: string | null
    position?: { x: number; y: number; z: number } | null
    health?: number | null
    food?: number | null
    game_mode?: string | null
    held_item?: string | null
    yaw?: number | null
  } | null
  environment?: Record<string, unknown> | null
  terrain?: MinecraftTerrainItem[]
  players?: MinecraftWorldPlayer[]
  entities?: MinecraftWorldEntity[]
  points_of_interest?: MinecraftWorldPoi[]
}

// ---------------- Phase 4C · Inventory / Place ----------------

/** 六个放置方向（与 runtime 同一张表；绝不接受任意向量）。 */
export type MinecraftPlaceFace = 'up' | 'down' | 'north' | 'south' | 'east' | 'west'

export interface MinecraftInventoryItem {
  name: string
  count: number
}

/** 只读背包切片（按物品名聚合；无 slot / NBT / window）。 */
export interface MinecraftInventoryView {
  ok: boolean
  online: boolean
  selected_hotbar_slot: number | null
  held_item: MinecraftInventoryItem | null
  items: MinecraftInventoryItem[]
}

// ---------------- Phase 4D · Inventory Control（Equip / Move） ----------------

/** 玩家窗口的一个绝对槽位（主背包 9-35 + 快捷栏 36-44）。 */
export interface MinecraftInventorySlot {
  slot: number
  /** 物品名（与 runtime / held_item / items 同形，如 dirt）。 */
  name: string
  count: number
  hotbar: boolean
}

/**
 * 调试用的**原始槽位**视图（WebUI Move Test / smoke 用）。
 *
 * 它不是 LLM 的数据源：`minecraft_inventory` 工具只给按物品名聚合的切片，模型看不到槽位号 ——
 * 这个面板的用途是「开发者照着槽位表做一次明确的操作」。
 */
export interface MinecraftInventorySlotsView {
  ok: boolean
  online: boolean
  hotbar_start: number | null
  inventory_start: number | null
  slots: MinecraftInventorySlot[]
}

/** equip / inventory_move 的启动响应（持续型：启动即 RUNNING，终态由事件送达）。 */
export interface MinecraftInventoryActionResult {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  [key: string]: unknown
}

// ---------------- Phase 4E · Container（Chest / Barrel） ----------------

/** 容器里的一个非空格子（只读 projection：没有 NBT / 内部 id / window）。 */
export interface MinecraftContainerSlot {
  slot: number
  name: string
  count: number
}

/** 容器语义状态（`minecraft_container_inspect` 的 inspection 结果）。 */
export interface MinecraftContainerView {
  type: string
  label: string
  position: MinecraftPosition
  size: number
}

export interface MinecraftContainerSnapshot {
  ok: boolean
  container: MinecraftContainerView
  slots: MinecraftContainerSlot[]
}

/** INSPECT 端点的响应：标准信封 + 快照在 result 里。 */
export interface MinecraftContainerInspectResponse {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  result: MinecraftContainerSnapshot
}

/** withdraw = 从箱子拿到背包；deposit = 从背包放进箱子。 */
export type MinecraftContainerDirection = 'withdraw' | 'deposit'

/** container_transfer 的启动响应（持续型：终态由事件送达）。 */
export interface MinecraftContainerTransferResult {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  [key: string]: unknown
}

// ---------------- Phase 4F · Crafting（玩家 2×2） ----------------

export interface MinecraftRecipeIngredient {
  name: string
  count: number
}

export interface MinecraftRecipeEntry {
  /** 稳定可读签名（例如 stick*4=oak_planks*2）；minecraft_craft 只接受它。 */
  recipe_id: string
  result: { name: string; count_per_craft: number }
  requires_table: boolean
  available: boolean
  ingredients: MinecraftRecipeIngredient[]
}

/** 配方查询的四种状态（§九）。 */
export type MinecraftRecipeStatus =
  | 'available'
  | 'insufficient_material'
  | 'crafting_table_required'
  | 'recipe_not_found'

/** 工作台的方块坐标（Phase 4G：3×3 必须由调用方明确指定，绝不接受 nearest/auto）。 */
export interface MinecraftCraftingTable {
  x: number
  y: number
  z: number
}

export interface MinecraftRecipeLookupResult {
  ok: boolean
  item: string
  status: MinecraftRecipeStatus
  total: number
  recipes: MinecraftRecipeEntry[]
  /** 只在指定了工作台时出现（2×2 查询不带这个字段）。 */
  crafting_table?: MinecraftCraftingTable
}

/** INSPECT 式的响应信封：快照在 ``result`` 里。 */
export interface MinecraftRecipeLookupResponse {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  result: MinecraftRecipeLookupResult
}

/** craft 的启动响应（持续型：终态由事件送达）。 */
export interface MinecraftCraftResult {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  [key: string]: unknown
}

// ---------------- Phase 4H · 掉落物实体（感知 / 拾取） ----------------

/** 地上的一个掉落物实体（只读语义投影：没有 raw metadata / UUID / velocity）。 */
export interface MinecraftDroppedItem {
  entity_id: number
  item: { name: string; count: number }
  position: MinecraftPosition
  distance: number
}

export interface MinecraftDroppedItemsView {
  ok: boolean
  online: boolean
  total: number
  /** 超过上限（32）时只列前 32 条，这里会标 true。 */
  truncated: boolean
  items: MinecraftDroppedItem[]
}

export interface MinecraftDroppedItemsResponse {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  result: MinecraftDroppedItemsView
}

/**
 * Phase 4J：挖掘能力查询的语义投影（只读）。
 *
 * 注意两种距离口径是不同的量：`goal_near` 是"罐头占的方块格 → 目标方块格"，
 * `raw` 是眼睛 → 方块中心的浮点距离（与 minecraft_dig 的门禁同一个量）。
 */
export interface MinecraftDigCapabilityView {
  ok: boolean
  position: MinecraftPosition
  block: { name: string }
  held_item: { name: string; count: number } | null
  distance: { goal_near: number | null; raw: number | null }
  can_dig: boolean
  /** 能挖时是预计毫秒数（可能为 null = 运行时算不出来）；不着急挖不动时一定是 null。 */
  dig_time_ms: number | null
  /** null | air | too_far | not_diggable（不会推测"工具等级不够"）。 */
  reason: string | null
}

export interface MinecraftBlockMatch {
  block: { name: string }
  position: MinecraftPosition
  distance: { goal_near: number | null; raw: number | null }
}

export interface MinecraftFindBlocksView {
  ok: boolean
  query: { block_names: string[]; max_distance: number; max_results: number }
  matches: MinecraftBlockMatch[]
  /** 命中条数被上限截断（不是失败）。 */
  truncated: boolean
}

export interface MinecraftFindBlocksResponse {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  result: MinecraftFindBlocksView
}

export interface MinecraftDigCapabilityResponse {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  result: MinecraftDigCapabilityView
}

/** pickup 的启动响应（持续型：终态由事件送达）。 */
export interface MinecraftPickupItemResult {
  ok: boolean
  action: string
  status: MinecraftActionStatus
  action_id?: string
  [key: string]: unknown
}

// ---------------- Phase 3E · LLM Agent（Tool Debug + Context） ----------------

export type MinecraftActionRisk = 'SAFE' | 'LOW' | 'MEDIUM' | 'HIGH' | 'DESTRUCTIVE'

/** 一个 Minecraft Tool 的只读行：风险 / 是否注册启用 / 现在是否允许（§三十）。 */
export interface MinecraftAgentToolRow {
  name: string
  risk: MinecraftActionRisk
  enabled: boolean
  allowed: boolean
  /** 被拒时的稳定错误码（minecraft.offline / minecraft.action_busy …）。 */
  reason: string
}

export interface MinecraftAgentLastAction {
  action: string | null
  action_id: string | null
  status: MinecraftActionStatus
  code: string
  error: string
  result: Record<string, unknown> | null
  at: number
}

/** Agent 当前上下文（§十九/§四十八）：只读，来自事件 + 感知层实时读取。 */
export interface MinecraftAgentContext {
  online: boolean
  username: string
  current_action: { action: string | null; action_id: string | null; status: string } | null
  last_action: MinecraftAgentLastAction | null
  /** 最近一次成功动作的一句话描述（§二十二）。 */
  activity: string
  updated_at: number
  available?: boolean
  dimension?: string | null
  position?: MinecraftPosition | null
  biome?: string | null
  players?: { name: string; distance: number | null; direction: string | null }[]
}

export interface MinecraftAgentPolicy {
  enabled: boolean
  risk_flags: Record<MinecraftActionRisk, boolean>
  registered: Record<string, MinecraftActionRisk>
}

/** Phase 4A：一条待确认/已确认的 Minecraft 动作授权（只读投影）。 */
export interface MinecraftConfirmationView {
  confirmation_id: string
  session_id: string
  user_id: string
  tool: string
  risk: MinecraftActionRisk
  arguments_hash: string
  arguments: Record<string, unknown>
  summary: string
  created_at: number
  expires_at: number
  status: 'PENDING' | 'CONFIRMED' | 'EXPIRED' | 'CANCELLED' | 'CONSUMED'
}

export interface MinecraftConfirmationStoreView {
  ttl_seconds: number
  max_pending: number
  pending: MinecraftConfirmationView[]
  total: number
}

export interface MinecraftAgentView {
  enabled: boolean
  context: Partial<MinecraftAgentContext>
  policy: Partial<MinecraftAgentPolicy>
  tools: MinecraftAgentToolRow[]
  /** Phase 4A：待确认列表（只读）。 */
  confirmations?: MinecraftConfirmationStoreView
  /** Phase 4A：可信 Minecraft 玩家（LOW 及以上动作只对这些人执行）。 */
  trusted_players?: string[]
}

export interface MinecraftWorldView {
  available: boolean
  online: boolean
  reason?: string
  captured_at?: number | null
  age_seconds?: number | null
  layers?: Record<string, { age_seconds: number | null }>
  semantic?: MinecraftSemantic | null
  raw?: Record<string, unknown> | null
}

/** Phase 5A：多步骤任务（TaskRuntime）的只读投影（GET /minecraft/task）。 */
export interface MinecraftTaskStepView {
  step_id: string
  tool: string
  risk: MinecraftActionRisk
  state: string
  /** 一句人话（例如「走到 (12,64,9) 附近」）。 */
  label: string
}

export interface MinecraftTaskPlanView {
  plan_hash: string
  steps: MinecraftTaskStepView[]
  expected_final_state: { inventory_delta?: Record<string, number> }
}

export interface MinecraftTaskView {
  task_id: string
  session_id: string
  origin: string
  objective: string
  state: string
  progress: { completed: number; total: number }
  current_step: {
    step_id: string
    tool: string
    risk: MinecraftActionRisk
    state: string
    arguments: Record<string, unknown>
  } | null
  current_action: string | null
  plan: MinecraftTaskPlanView
  confirmation_required: boolean
  confirmation_id: string | null
  last_result: {
    tool: string
    status: string
    summary: string
    result: Record<string, unknown>
  } | null
  failure: { reason: string; step: string; message: string } | null
  verification: Record<string, unknown>
  result: Record<string, unknown>
  summary: string
  replans: number
  expires_at: number
  /** Phase 5A：不提供通用回滚。 */
  rollback_supported: boolean
  updated_at: number
}

export interface MinecraftTaskCurrentResponse {
  task: MinecraftTaskView | null
  session_id: string | null
}
