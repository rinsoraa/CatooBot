/** Minecraft 连接层 API（契约 §7.5）。页面只调这里，不写 fetch。 */

import { api } from '@/api/client'

import type {
  MinecraftJoinResult,
  MinecraftLeaveResult,
  MinecraftLookAtResult,
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
  /** 最高优先级安全停止（幂等）：取消进行中动作，返回被取消的 action_id 列表。 */
  stop() {
    return api.post<MinecraftStopResult>('/minecraft/stop', {})
  },
}
