/** Minecraft 连接层 API（契约 §7.5）。页面只调这里，不写 fetch。 */

import { api } from '@/api/client'

import type {
  MinecraftJoinResult,
  MinecraftLeaveResult,
  MinecraftOverview,
} from '@/types/minecraft'

export const minecraftApi = {
  /** 连接层一屏投影（状态机 + 世界状态 + runtime 健康）。 */
  overview() {
    return api.get<MinecraftOverview>('/minecraft')
  },
  /** 加入服务器；进世界由事件异步确认（页面靠轮询看到 ONLINE）。 */
  join(host: string, port: number) {
    return api.post<MinecraftJoinResult>('/minecraft/join', { host, port })
  },
  /** 主动离开（幂等）。 */
  leave() {
    return api.post<MinecraftLeaveResult>('/minecraft/leave', {})
  },
}
