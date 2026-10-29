/** 配置中心：`/api/v1/config/*` 的真实响应形状。 */

export type ConfigLevel = 'basic' | 'advanced' | 'expert'

export type ConfigSource = 'env' | 'overrides' | 'models' | 'yaml' | 'default'

export type UsageStatus =
  | 'ACTIVE'
  | 'ACTIVE_WITH_RESTART'
  | 'CONDITIONALLY_USED'
  | 'DEFINED_BUT_UNUSED'
  | 'LEGACY'

export interface FieldConstraints {
  min?: number
  max?: number
  exclusive_min?: number
  exclusive_max?: number
  min_length?: number
  max_length?: number
  pattern?: string
}

/** `GET /api/v1/config/schema` 的单项。 */
export interface ConfigFieldMeta {
  key: string
  label: string
  description: string
  type: 'str' | 'int' | 'float' | 'bool' | 'list' | 'dict' | 'obj' | string
  default: unknown
  constraints: FieldConstraints
  area: string
  level: ConfigLevel
  hot_reload: boolean
  restart_required: boolean
  usage_status: UsageStatus
  sensitive: boolean
  choices: string[]
  hidden: boolean
  /** 所属配置类（点分路径，如 behavior.initiative）与中文类名 —— 导航栏按它分组 */
  section: string
  section_label: string
}

export interface ConfigSchema {
  items: ConfigFieldMeta[]
  areas: string[]
  levels: ConfigLevel[]
  usage_status: UsageStatus[]
  legends: {
    source: Record<string, string>
    usage_status: Record<string, string>
  }
}

/** `GET /api/v1/config/effective` 的单项（敏感项没有 value，只有 masked）。 */
export interface EffectiveField {
  key: string
  label: string
  area: string
  level: ConfigLevel
  type: string
  usage_status: UsageStatus
  hot_reload: boolean
  restart_required: boolean
  hidden: boolean
  value?: unknown
  source?: ConfigSource
  sensitive?: boolean
  configured?: boolean
  masked?: string
}

export interface ConfigWarning {
  code: string
  message: string
  keys: string[]
}

/** `PATCH /api/v1/config` 的结果。 */
export interface ApplyResult {
  saved: string[]
  hot_reload: string[]
  restart_required: string[]
  effective: boolean[]
  values: EffectiveField[]
  notes: string[]
  warnings: ConfigWarning[]
}

/** `POST /api/v1/config/validate` 的预览结果。 */
export interface ValidateResult {
  valid: boolean
  changes: Record<string, unknown>
  hot_reload: string[]
  restart_required: string[]
  notes: string[]
}

export interface RestartPending {
  pending: string[]
  since: number | null
}

export interface RawConfig {
  yaml: string
  path: string
}

/** 配置层级来源的中文说明（与后端 legends 对齐）。 */
export const SOURCE_LABELS: Record<string, string> = {
  env: '环境变量',
  overrides: 'WebUI 覆盖',
  models: 'models 段',
  yaml: '配置文件',
  default: '默认值',
}

export const USAGE_LABELS: Record<UsageStatus, string> = {
  ACTIVE: '运行期使用',
  ACTIVE_WITH_RESTART: '仅启动时使用',
  CONDITIONALLY_USED: '条件生效',
  DEFINED_BUT_UNUSED: '当前未使用',
  LEGACY: '旧语义（已废弃）',
}
