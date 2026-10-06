/** Minecraft 连接层 API（契约 §7.5）。页面只调这里，不写 fetch。 */

import { api } from '@/api/client'

import type {
  MinecraftContainerDirection,
  MinecraftCraftingTable,
  MinecraftCraftResult,
  MinecraftRecipeLookupResponse,
  MinecraftContainerInspectResponse,
  MinecraftContainerTransferResult,
  MinecraftFollowPlayerResult,
  MinecraftInventoryActionResult,
  MinecraftInventorySlotsView,
  MinecraftInventoryView,
  MinecraftPlaceFace,
  MinecraftJoinResult,
  MinecraftLeaveResult,
  MinecraftLookAtResult,
  MinecraftMoveToResult,
  MinecraftOverview,
  MinecraftStopResult,
  MinecraftWorldView,
} from '@/types/minecraft'

export const minecraftApi = {
  /** 连接层一屏投影（状态机 + 世界状态 + runtime 健康 + 动作视图）。 */
  overview() {
    return api.get<MinecraftOverview>('/minecraft')
  },
  /** World Debug 只读视图（Phase 2）：语义模型 + raw snapshot。 */
  world() {
    return api.get<MinecraftWorldView>('/minecraft/world')
  },
  /** 加入服务器；进世界由事件异步确认（页面靠轮询看到 ONLINE）。 */
  join(host: string, port: number) {
    return api.post<MinecraftJoinResult>('/minecraft/join', { host, port })
  },
  /** 主动离开（幂等）。 */
  leave() {
    return api.post<MinecraftLeaveResult>('/minecraft/leave', {})
  },
  /** 让罐头看向世界坐标（Phase 3B SAFE 动作：不改世界、不移动）。 */
  lookAt(x: number, y: number, z: number) {
    return api.post<MinecraftLookAtResult>('/minecraft/look_at', { x, y, z })
  },
  /** Phase 3C：非破坏性导航到世界坐标（禁挖/禁放；不可达 → path_not_found）。 */
  moveTo(x: number, y: number, z: number) {
    return api.post<MinecraftMoveToResult>('/minecraft/move_to', { x, y, z })
  },
  /** Phase 3D：动态跟随玩家（持续型动作：启动即返回 RUNNING，终态经事件/轮询呈现）。 */
  followPlayer(username: string, distance: number) {
    return api.post<MinecraftFollowPlayerResult>('/minecraft/follow_player', {
      username,
      distance,
    })
  },
  /** 最高优先级安全停止（幂等）：取消进行中动作，返回被取消的 action_id 列表。 */
  stop() {
    return api.post<MinecraftStopResult>('/minecraft/stop', {})
  },
  /** Phase 4B：破坏一个指定方块（开发者调试入口；**必须**过 MEDIUM 确认门）。 */
  dig(x: number, y: number, z: number, expectedBlock: string) {
    return api.post<Record<string, unknown>>('/minecraft/dig', {
      x,
      y,
      z,
      expected_block: expectedBlock,
    })
  },
  /** Phase 4C：只读背包切片（选中的槽 / 手持物品 / 聚合物品清单）。 */
  inventory() {
    return api.get<MinecraftInventoryView>('/minecraft/inventory')
  },
  /** Phase 4C：放置一个方块（开发者调试入口；**必须**过 MEDIUM 确认门）。 */
  place(x: number, y: number, z: number, face: MinecraftPlaceFace, expectedItem: string) {
    return api.post<Record<string, unknown>>('/minecraft/place', {
      x,
      y,
      z,
      face,
      expected_item: expectedItem,
    })
  },
  /** Phase 4D：调试用的原始槽位表（只读；LLM 工具不读它）。 */
  inventorySlots() {
    return api.get<MinecraftInventorySlotsView>('/minecraft/inventory/slots')
  },
  /** Phase 4D：把指定物品拿到主手（开发者调试入口；**必须**过 MEDIUM 确认门）。 */
  equip(item: string) {
    return api.post<MinecraftInventoryActionResult>('/minecraft/equip', { item })
  },
  /** Phase 4D：单物品单槽位搬运（开发者调试入口；**必须**过 MEDIUM 确认门）。 */
  inventoryMove(sourceSlot: number, destinationSlot: number, item: string, count: number) {
    return api.post<MinecraftInventoryActionResult>('/minecraft/inventory_move', {
      source_slot: sourceSlot,
      destination_slot: destinationSlot,
      item,
      count,
    })
  },
  /** Phase 4E：读一个 Chest / Barrel 的内容（SAFE 只读，同步返回快照）。 */
  containerInspect(x: number, y: number, z: number) {
    return api.post<MinecraftContainerInspectResponse>('/minecraft/container_inspect', { x, y, z })
  },
  /** Phase 4E：单物品在容器槽 ↔ 背包槽之间搬一次（必须过 MEDIUM 确认门）。 */
  containerTransfer(
    x: number,
    y: number,
    z: number,
    direction: MinecraftContainerDirection,
    containerSlot: number,
    inventorySlot: number,
    item: string,
    count: number,
  ) {
    return api.post<MinecraftContainerTransferResult>('/minecraft/container_transfer', {
      x,
      y,
      z,
      direction,
      container_slot: containerSlot,
      inventory_slot: inventorySlot,
      item,
      count,
    })
  },
  /** Phase 4F/4G：查配方（不带 craftingTable = 玩家 2×2；带坐标 = 那张工作台的 3×3）。 */
  recipeLookup(item: string, craftingTable?: MinecraftCraftingTable | null) {
    return api.post<MinecraftRecipeLookupResponse>('/minecraft/recipe_lookup', {
      item,
      ...(craftingTable ? { crafting_table: craftingTable } : {}),
    })
  },
  /** Phase 4F/4G：执行一次配方（必须过 MEDIUM 确认门）。 */
  craft(recipeId: string, craftingTable?: MinecraftCraftingTable | null) {
    return api.post<MinecraftCraftResult>('/minecraft/craft', {
      recipe_id: recipeId,
      ...(craftingTable ? { crafting_table: craftingTable } : {}),
    })
  },
  /** Phase 4A：确认门 Debug（只能缩小授权：造测试条 / 取消 / 置过期）。 */
  confirm(action: 'create_test' | 'cancel' | 'expire', body: Record<string, unknown> = {}) {
    return api.post<Record<string, unknown>>('/minecraft/agent/confirm', { action, ...body })
  },
}
