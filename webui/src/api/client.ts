/**
 * 唯一的 HTTP 出口（WebUI v1.0 · W3 §7-§13）。
 *
 * 页面与 Store 只调用这里；CSRF 注入、401 处理、信封解析、错误映射
 * 全部集中在本文件，业务代码不写 `fetch`、不写 `X-CSRF-Token`。
 */

import type { ApiErrorBody, ApiFailure, ApiSuccess } from '@/types/api'

export const API_BASE = '/api/v1'

/** 后端返回的结构化错误；`message` 优先直接展示给用户（§13）。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly field?: string
  readonly detail?: unknown

  constructor(status: number, body: ApiErrorBody) {
    super(body.message || `请求失败（HTTP ${status}）`)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code || 'internal.error'
    if (body.field !== undefined) this.field = body.field
    this.detail = body.detail
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }

  get isForbidden(): boolean {
    return this.status === 403
  }

  get isConflict(): boolean {
    return this.status === 409
  }

  get isValidation(): boolean {
    return this.status === 422
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
  /** 401 时不触发全局登出（用于登录前的 session 探测）。 */
  skipAuthHandler?: boolean
}

const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = path.startsWith('/') ? `${API_BASE}${path}` : `${API_BASE}/${path}`
  if (!query) return url
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `${url}?${encoded}` : url
}

function isFailure(value: unknown): value is ApiFailure {
  return typeof value === 'object' && value !== null && (value as ApiFailure).ok === false
}

class ApiClient {
  private csrfToken = ''
  private unauthorizedHandler: (() => void) | null = null

  /** 由 auth store 在引导/登录成功后写入。 */
  setCsrf(token: string): void {
    this.csrfToken = token
  }

  clearCsrf(): void {
    this.csrfToken = ''
  }

  get csrfReady(): boolean {
    return this.csrfToken.length > 0
  }

  /** 注册 401 的全局反应（清会话 + 回登录页）；由 auth store 装配。 */
  onUnauthorized(handler: (() => void) | null): void {
    this.unauthorizedHandler = handler
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const method = options.method ?? 'GET'
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (MUTATING.has(method) && this.csrfToken) headers['X-CSRF-Token'] = this.csrfToken

    let response: Response
    try {
      response = await fetch(buildUrl(path, options.query), {
        method,
        headers,
        credentials: 'include',
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      })
    } catch (error) {
      throw new ApiError(0, {
        code: 'network.error',
        message: '无法连接到 CatooBot 服务，请确认程序仍在运行',
        detail: error instanceof Error ? error.message : String(error),
      })
    }

    let payload: unknown = null
    const text = await response.text()
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = null
      }
    }

    if (!response.ok || isFailure(payload)) {
      const body: ApiErrorBody =
        isFailure(payload)
          ? payload.error
          : { code: 'internal.error', message: `请求失败（HTTP ${response.status}）` }
      const failure = new ApiError(response.status, body)
      if (failure.isUnauthorized && !options.skipAuthHandler) {
        this.unauthorizedHandler?.()
      }
      throw failure
    }

    if (payload === null) {
      // 204 / 空体：调用方按 T = void 处理
      return undefined as T
    }
    return (payload as ApiSuccess<T>).data
  }

  get<T>(path: string, options: Omit<RequestOptions, 'method' | 'body'> = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'GET' })
  }

  post<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'POST', body })
  }

  put<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PUT', body })
  }

  patch<T>(path: string, body?: unknown, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'PATCH', body })
  }

  del<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return this.request<T>(path, { ...options, method: 'DELETE' })
  }
}

export const api = new ApiClient()

/** 把任意异常变成可展示的中文文案（后端 message 优先）。 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return '发生未知错误'
}
