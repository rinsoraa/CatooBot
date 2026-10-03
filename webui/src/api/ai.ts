/** `/api/v1/ai/*` 的薄封装：只做路径与类型，不含业务规则。 */

import { api } from '@/api/client'
import type {
  AiStatus,
  ModelItem,
  ProviderItem,
  RoleItem,
  TestResult,
  UsageGroupBy,
  UsageRow,
} from '@/types/ai'

export interface ProviderPayload {
  type?: string
  base_url?: string
  api_key_env?: string
}

export interface ModelPayload {
  provider?: string
  model?: string
  enabled?: boolean
}

export const aiApi = {
  status(): Promise<AiStatus> {
    return api.get<AiStatus>('/ai/status')
  },

  providers(): Promise<ProviderItem[]> {
    return api.get<{ items: ProviderItem[] }>('/ai/providers').then((data) => data.items)
  },

  saveProvider(name: string, payload: ProviderPayload): Promise<ProviderItem> {
    return api.put<ProviderItem>(`/ai/providers/${encodeURIComponent(name)}`, payload)
  },

  deleteProvider(name: string, force = false): Promise<{ deleted: boolean; models_removed?: number }> {
    return api.del(`/ai/providers/${encodeURIComponent(name)}`, {
      query: force ? { force: 1 } : undefined,
      body: { confirm: force ? 'force' : name },
    })
  },

  models(): Promise<ModelItem[]> {
    return api.get<{ items: ModelItem[] }>('/ai/models').then((data) => data.items)
  },

  saveModel(name: string, payload: ModelPayload): Promise<ModelItem> {
    return api.put<ModelItem>(`/ai/models/${encodeURIComponent(name)}`, payload)
  },

  deleteModel(name: string, force = false): Promise<{ deleted: boolean }> {
    return api.del(`/ai/models/${encodeURIComponent(name)}`, {
      query: force ? { force: 1 } : undefined,
      body: { confirm: force ? 'force' : name },
    })
  },

  reorder(order: string[]): Promise<{ order: string[] }> {
    return api.put<{ order: string[] }>('/ai/models/order', { order })
  },

  roles(): Promise<RoleItem[]> {
    return api.get<{ items: RoleItem[] }>('/ai/roles').then((data) => data.items)
  },

  setRole(role: string, model: string): Promise<RoleItem> {
    return api.put<RoleItem>(`/ai/roles/${encodeURIComponent(role)}`, { model })
  },

  usage(days = 7, groupBy: UsageGroupBy = 'model'): Promise<UsageRow[]> {
    return api
      .get<{ items: UsageRow[] }>('/ai/usage', { query: { days, group_by: groupBy } })
      .then((data) => data.items)
  },

  testModel(name: string, prompt = 'ping'): Promise<TestResult> {
    return api.post<TestResult>(`/ai/models/${encodeURIComponent(name)}/test`, { prompt })
  },

  resetRouter(): Promise<{ reset: boolean }> {
    return api.post<{ reset: boolean }>('/ai/router/reset', { confirm: 'reset' })
  },
}
