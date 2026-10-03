/**
 * AI Store（W4 §74-§76、§137）：只做 fetch / cache / 失效，不含业务规则。
 *
 * 所有 mutation 都先落服务端、再重新读取真实状态——绝不把本地猜测当结果。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { aiApi, type ModelPayload, type ProviderPayload } from '@/api/ai'
import { errorMessage } from '@/api/client'
import type { AiStatus, ModelItem, ProviderItem, RoleItem, TestResult, UsageGroupBy, UsageRow } from '@/types/ai'

export const useAiStore = defineStore('ai', () => {
  const status = ref<AiStatus | null>(null)
  const providers = ref<ProviderItem[]>([])
  const models = ref<ModelItem[]>([])
  const roles = ref<RoleItem[]>([])
  const usage = ref<UsageRow[]>([])
  const usageGroupBy = ref<UsageGroupBy>('model')
  const usageDays = ref(7)

  const loading = ref(false)
  const error = ref('')
  const lastLoadedAt = ref(0)

  const chatModel = computed(() => status.value?.chat_model || models.value[0]?.name || '')
  const enabledModels = computed(() => models.value.filter((model) => model.enabled))
  const providerNames = computed(() =>
    providers.value.filter((provider) => provider.has_key).map((provider) => provider.name),
  )

  function fail(caught: unknown): void {
    error.value = errorMessage(caught)
  }

  async function loadStatus(): Promise<void> {
    try {
      status.value = await aiApi.status()
      if (status.value) lastLoadedAt.value = Date.now()
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadProviders(): Promise<void> {
    try {
      providers.value = await aiApi.providers()
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadModels(): Promise<void> {
    try {
      models.value = await aiApi.models()
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadRoles(): Promise<void> {
    try {
      roles.value = await aiApi.roles()
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadUsage(days = usageDays.value, groupBy = usageGroupBy.value): Promise<void> {
    usageDays.value = days
    usageGroupBy.value = groupBy
    try {
      usage.value = await aiApi.usage(days, groupBy)
    } catch (caught) {
      fail(caught)
    }
  }

  /** 全量刷新（AI 概览 / 首屏）。 */
  async function loadAll(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      await Promise.all([loadStatus(), loadProviders(), loadModels(), loadRoles()])
    } finally {
      loading.value = false
    }
  }

  // ------------------------------------------------------------------ mutations

  async function saveProvider(name: string, payload: ProviderPayload): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.saveProvider(name, payload)
    } catch (caught) {
      fail(caught)
      return false
    }
    // §137: provider mutation → providers → models → overview
    await Promise.all([loadProviders(), loadModels(), loadStatus()])
    return true
  }

  async function deleteProvider(name: string, force = false): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.deleteProvider(name, force)
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadProviders(), loadModels(), loadStatus()])
    return true
  }

  async function saveModel(name: string, payload: ModelPayload): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.saveModel(name, payload)
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadModels(), loadRoles(), loadProviders(), loadStatus()])
    return true
  }

  async function deleteModel(name: string, force = false): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.deleteModel(name, force)
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadModels(), loadRoles(), loadStatus()])
    return true
  }

  async function reorder(order: string[]): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.reorder(order)
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadModels(), loadRoles(), loadStatus()])
    return true
  }

  async function setRole(role: string, model: string): Promise<RoleItem | null> {
    error.value = ''
    try {
      const item = await aiApi.setRole(role, model)
      await Promise.all([loadRoles(), loadModels(), loadStatus()])
      return item
    } catch (caught) {
      fail(caught)
      return null
    }
  }

  async function testModel(name: string, prompt = 'ping'): Promise<TestResult | null> {
    error.value = ''
    try {
      return await aiApi.testModel(name, prompt)
    } catch (caught) {
      fail(caught)
      return null
    }
  }

  async function resetRouter(): Promise<boolean> {
    error.value = ''
    try {
      await aiApi.resetRouter()
    } catch (caught) {
      fail(caught)
      return false
    }
    await Promise.all([loadModels(), loadStatus()])
    return true
  }

  function clear(): void {
    status.value = null
    providers.value = []
    models.value = []
    roles.value = []
    usage.value = []
    error.value = ''
  }

  return {
    status,
    providers,
    models,
    roles,
    usage,
    usageGroupBy,
    usageDays,
    loading,
    error,
    lastLoadedAt,
    chatModel,
    enabledModels,
    providerNames,
    loadStatus,
    loadProviders,
    loadModels,
    loadRoles,
    loadUsage,
    loadAll,
    saveProvider,
    deleteProvider,
    saveModel,
    deleteModel,
    reorder,
    setRole,
    testModel,
    resetRouter,
    clear,
  }
})
