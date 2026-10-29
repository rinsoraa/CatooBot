/** AI 与模型：`/api/v1/ai/*` 的真实响应形状（W4 统一后）。 */

export interface ProviderItem {
  name: string
  type: string
  base_url: string
  api_key_env: string
  has_key: boolean
  models: string[]
  restart_required: boolean
}

export interface ModelUsage {
  calls: number
  failures: number
  prompt_tokens: number
  completion_tokens: number
  avg_latency_ms: number
  max_latency_ms: number
}

export interface ModelItem {
  name: string
  provider: string
  model: string
  enabled: boolean
  order: number
  roles: string[]
  live: boolean
  in_cooldown: boolean
  /** 路由器单调时钟上的截止值（保真用，不要当墙钟时间） */
  cooldown_until: number
  /** 冷却剩余秒数：倒计时唯一可用的字段 */
  cooldown_remaining_seconds: number
  failure_count: number
  last_error: string | null
  usage: ModelUsage
}

export interface RoleItem {
  role: string
  key: string
  model: string
  source: 'env' | 'overrides' | 'models' | 'yaml' | 'default' | string
  restart_required: boolean
}

/** `/api/v1/ai/status` 的 data —— AI 概览的唯一数据源。 */
export type AiHealth = 'ready' | 'degraded' | 'unavailable' | 'not_configured'

export interface AiStatus {
  status: AiHealth
  enabled: boolean
  configured: boolean
  checks: {
    has_provider: boolean
    has_credential: boolean
    has_model: boolean
    chat_bound: boolean
  }
  providers: { total: number; with_key: number; missing_key: string[] }
  models: { total: number; enabled: number; disabled: number; usable: number; cooldown: number }
  chat_model: string
  fallback_chain: string[]
  errors: { rate_limited: number; server_errors: number }
  cooldown_models: { name: string; cooldown_until: number; remaining_seconds: number }[]
}

/** 唯一的测试结果形状（Provider 测试与 Model 测试共用语义）。 */
export interface TestResult {
  ok: boolean
  /** 配置里的别名（请求的那个） */
  model: string
  requested_model: string
  /** 真正作答的服务商模型 id（故障转移后与别名不同，Provider 测试可能为空） */
  provider_model?: string
  provider?: string
  latency_ms: number
  http_status: number | null
  http_status_source: 'upstream' | 'error_class' | '' | string
  error_type: string
  message: string
  response?: string
  /** Provider 测试（凭据域）专有 */
  reply?: string
}

export interface UsageRow {
  key: string
  calls: number
  failures: number
  rate_limited: number
  server_errors: number
  tokens: number
  avg_latency_ms: number
  max_latency_ms: number
}

export type UsageGroupBy = 'model' | 'provider' | 'purpose' | 'error_type'

/** 模型/角色的中文名与职责说明（§27/§66）：来自 Core 的真实能力。 */
export interface RoleDescriptor {
  role: string
  label: string
  description: string
  restartHint: string
}

export const ROLE_DESCRIPTORS: RoleDescriptor[] = [
  {
    role: 'chat',
    label: '聊天回复',
    description: '负责普通 QQ 对话的主要回复生成；它就是故障转移链的第一个模型。',
    restartHint: '改顺序即时生效（路由器按当前顺序挑选）。',
  },
  {
    role: 'vision',
    label: '图片理解',
    description: '看图时使用：识别图片内容、决定表情与回复。',
    restartHint: '媒体组件在启动时读取该绑定，修改后需要重启。',
  },
  {
    role: 'decision',
    label: '决策',
    description: '生活沙盒在需要判断“做什么”时的裁决模型。',
    restartHint: '沙盒运行期读取配置，改后即时生效。',
  },
  {
    role: 'conversation',
    label: '会话判断',
    description: '对话运行期生成“这一轮怎么回”，受回复长度上限约束。',
    restartHint: '对话组件运行期读取，改后即时生效。',
  },
  {
    role: 'extraction',
    label: '记忆抽取',
    description: '从高价值互动里提炼可长期保存的事实。',
    restartHint: '提取器运行期读取，改后即时生效。',
  },
  {
    role: 'social',
    label: '社交判断',
    description: '群聊里判断“该不该说话、说什么更合适”。',
    restartHint: '社交观察者运行期读取，改后即时生效。',
  },
  {
    role: 'planner',
    label: '任务规划（Planner）',
    description: 'Agent 把一句话拆成可执行步骤时使用。',
    restartHint: 'Agent 组件在启动时读取该绑定，修改后需要重启。',
  },
  {
    role: 'evaluator',
    label: '任务评估（Evaluator）',
    description: 'Agent 判断任务是否完成、要不要重规划。',
    restartHint: 'Agent 组件在启动时读取该绑定，修改后需要重启。',
  },
  {
    role: 'embedding',
    label: '向量检索（Embedding）',
    description: '把记忆转成向量以支持语义检索；需与向量模型匹配。',
    restartHint: '向量服务在启动时建立，修改后需要重启。',
  },
]

export function roleDescriptor(role: string): RoleDescriptor | undefined {
  return ROLE_DESCRIPTORS.find((item) => item.role === role)
}
