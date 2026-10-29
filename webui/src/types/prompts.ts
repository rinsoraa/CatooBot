/** 提示词（`GET/PATCH /api/v1/prompts`）——v0.8 提示词页迁移。 */

export interface PromptPair {
  /** 人设系统提示（角色页可编辑；本页只读展示 + 链接）。 */
  persona_system_prompt: string
  /** 记忆提取提示覆盖；空串表示未设置覆盖。 */
  memory_extraction_prompt: string
}
