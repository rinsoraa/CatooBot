/** `/api/v1/prompts`：人设系统提示 + 记忆提取提示的读写（PATCH 只需带要改的键）。 */

import { api } from '@/api/client'
import type { PromptPair } from '@/types/prompts'

export const promptsApi = {
  get() {
    return api.get<PromptPair>('/prompts')
  },

  /** 空对象会被后端以 `prompts.empty` 拒绝；调用方只提交改动键。 */
  save(data: Partial<PromptPair>) {
    return api.patch<PromptPair>('/prompts', data)
  },
}
