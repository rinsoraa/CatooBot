/**
 * `/api/v1` 信封与错误（严格对应 W2 实际实现，不按文档猜）。
 *
 * 成功：`{ ok: true, data, meta: { request_id } }`
 * 失败：`{ ok: false, error: { code, message, field?, detail? }, meta }`
 */

export interface ApiMeta {
  request_id: string
}

export interface ApiSuccess<T> {
  ok: true
  data: T
  meta: ApiMeta
}

export interface ApiErrorBody {
  code: string
  message: string
  field?: string
  detail?: unknown
}

export interface ApiFailure {
  ok: false
  error: ApiErrorBody
  meta: ApiMeta
}

export type ApiResponse<T> = ApiSuccess<T> | ApiFailure

/** 分页信封（列表端点统一形状）。 */
export interface Paged<T> {
  items: T[]
  next_cursor?: string | null
  total?: number | null
}
