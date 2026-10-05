/** `/api/v1/behavior/*`：行为调试（试跑回复 / 模拟器 / 手动触发）。全是 dry-run。 */

import { api } from '@/api/client'
import type {
  BehaviorPreviewInput,
  BehaviorPreviewResult,
  BehaviorTestResponseResult,
  BehaviorTriggerAction,
  BehaviorTriggerResult,
} from '@/types/behavior'

export const behaviorApi = {
  /** 试跑一次回复链路：生成但不发送。 */
  testResponse(text: string) {
    return api.post<BehaviorTestResponseResult>('/behavior/test-response', { text })
  },

  /** 行为模拟器：延迟 / 分条 / 主动判定，永不发送。 */
  preview(input: BehaviorPreviewInput = {}) {
    return api.post<BehaviorPreviewResult>('/behavior/preview', input)
  },

  /** 手动触发状态变化；reset_state 会重置叙事状态，页面必须先确认。 */
  trigger(action: BehaviorTriggerAction) {
    return api.post<BehaviorTriggerResult>(`/behavior/triggers/${action}`)
  },
}
