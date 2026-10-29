/** `/api/v1/config/*` 与 `/api/v1/credentials/*` 的薄封装。 */

import { api } from '@/api/client'
import type {
  ApplyResult,
  ConfigLevel,
  ConfigSchema,
  EffectiveField,
  RawConfig,
  RestartPending,
  ValidateResult,
} from '@/types/config'
import type { TestResult } from '@/types/ai'

export const configApi = {
  schema(options: { area?: string; level?: ConfigLevel | ''; includeUnused?: boolean } = {}) {
    return api.get<ConfigSchema>('/config/schema', {
      query: {
        area: options.area,
        level: options.level,
        include: options.includeUnused ? 'unused' : undefined,
      },
    })
  },

  effective(options: { area?: string; level?: ConfigLevel | ''; includeUnused?: boolean; keys?: string[] } = {}) {
    return api
      .get<{ items: EffectiveField[]; count: number }>('/config/effective', {
        query: {
          area: options.area,
          level: options.level,
          include: options.includeUnused ? 'unused' : undefined,
          keys: options.keys?.join(','),
        },
      })
      .then((data) => data.items)
  },

  validate(values: Record<string, unknown>): Promise<ValidateResult> {
    return api.post<ValidateResult>('/config/validate', { values })
  },

  apply(values: Record<string, unknown>): Promise<ApplyResult> {
    return api.patch<ApplyResult>('/config', { values })
  },

  restartPending(): Promise<RestartPending> {
    return api.get<RestartPending>('/config/restart-pending')
  },

  reset(): Promise<{ reset: boolean }> {
    return api.post<{ reset: boolean }>('/config/reset', { confirm: 'reset' })
  },

  raw(): Promise<RawConfig> {
    return api.get<RawConfig>('/config/raw')
  },

  saveRaw(yaml: string): Promise<{ saved: boolean; notes: string[] }> {
    return api.put<{ saved: boolean; notes: string[] }>('/config/raw', { yaml, confirm: 'raw' })
  },
}

export type CredentialDomain = 'ai' | 'embedding' | 'onebot' | 'web' | 'tool'

export interface CredentialItem {
  domain: CredentialDomain | string
  ref: string
  masked: string
  configured: boolean
  source?: string
}

export const credentialsApi = {
  list(): Promise<CredentialItem[]> {
    return api.get<{ items: CredentialItem[] }>('/credentials').then((data) => data.items)
  },

  save(domain: string, ref: string, value: string): Promise<{ saved: boolean; masked: string }> {
    return api.put(`/credentials/${domain}/${encodeURIComponent(ref)}`, { value })
  },

  remove(domain: string, ref: string, force = false): Promise<{ deleted: boolean }> {
    return api.del(`/credentials/${domain}/${encodeURIComponent(ref)}`, {
      query: force ? { force: 1 } : undefined,
    })
  },

  test(payload: { provider: string; model?: string; value?: string }): Promise<TestResult> {
    return api.post<TestResult>('/credentials/test', payload)
  },
}
