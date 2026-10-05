/** Minecraft 连接层 API（契约 §7.5）。页面只调这里，不写 fetch。 */

import { api } from '@/api/client'

import type {
  MinecraftFollowPlayerResult,
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
  /** Phase 4A：确认门 Debug（只能缩小授权：造测试条 / 取消 / 置过期）。 */
  confirm(action: 'create_test' | 'cancel' | 'expire', body: Record<string, unknown> = {}) {
    return api.post<Record<string, unknown>>('/minecraft/agent/confirm', { action, ...body })
  },
}
