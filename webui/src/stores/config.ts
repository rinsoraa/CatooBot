/**
 * Config Store（W4 §74-§76、§108）：schema / effective / 草稿 / 应用 / 重启待办。
 *
 * 草稿只活在内存里（§127：localStorage 不放真实配置），应用前必须经后端
 * `validate`；应用后重新读取 effective 与 restart-pending，不做假热更新。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { configApi } from '@/api/config'
import { errorMessage } from '@/api/client'
import type {
  ApplyResult,
  ConfigFieldMeta,
  ConfigLevel,
  ConfigSchema,
  EffectiveField,
  RawConfig,
  RestartPending,
  ValidateResult,
} from '@/types/config'

export const useConfigStore = defineStore('config', () => {
  const schema = ref<ConfigSchema | null>(null)
  const effective = ref<EffectiveField[]>([])
  const restartPending = ref<RestartPending>({ pending: [], since: null })
  const level = ref<ConfigLevel>('basic')
  const area = ref('')
  const includeUnused = ref(false)

  const draft = ref<Record<string, unknown>>({})
  const loading = ref(false)
  const applying = ref(false)
  const error = ref('')
  const lastApply = ref<ApplyResult | null>(null)
  const appliedAt = ref(0)

  const dirtyKeys = computed(() => Object.keys(draft.value))
  const isDirty = computed(() => dirtyKeys.value.length > 0)

  const effectiveByKey = computed(() => {
    const map = new Map<string, EffectiveField>()
    for (const row of effective.value) map.set(row.key, row)
    return map
  })

  const fields = computed<ConfigFieldMeta[]>(() => schema.value?.items ?? [])

  function fail(caught: unknown): void {
    error.value = errorMessage(caught)
  }

  async function loadSchema(options: { includeUnused?: boolean; level?: ConfigLevel } = {}): Promise<void> {
    if (options.includeUnused !== undefined) includeUnused.value = options.includeUnused
    if (options.level) level.value = options.level
    try {
      schema.value = await configApi.schema({
        includeUnused: includeUnused.value,
      })
    } catch (caught) {
      fail(caught)
    }
  }

  async function loadEffective(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      effective.value = await configApi.effective({
        includeUnused: includeUnused.value,
      })
    } catch (caught) {
      fail(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadRestartPending(): Promise<void> {
    try {
      restartPending.value = await configApi.restartPending()
    } catch (caught) {
      fail(caught)
    }
  }

  async function refresh(): Promise<void> {
    await Promise.all([loadSchema(), loadEffective(), loadRestartPending()])
  }

  // ------------------------------------------------------------------- draft

  function setValue(key: string, value: unknown): void {
    const current = effectiveByKey.value.get(key)?.value
    if (JSON.stringify(current) === JSON.stringify(value)) {
      const next = { ...draft.value }
      delete next[key]
      draft.value = next
      return
    }
    draft.value = { ...draft.value, [key]: value }
  }

  function discard(): void {
    draft.value = {}
  }

  function isDirtyKey(key: string): boolean {
    return key in draft.value
  }

  function draftValue(key: string): unknown {
    return key in draft.value ? draft.value[key] : effectiveByKey.value.get(key)?.value
  }

  async function validate(): Promise<ValidateResult | null> {
    if (!isDirty.value) return null
    try {
      return await configApi.validate(draft.value)
    } catch (caught) {
      fail(caught)
      return null
    }
  }

  async function apply(): Promise<ApplyResult | null> {
    if (!isDirty.value) return null
    applying.value = true
    error.value = ''
    try {
      const result = await configApi.apply(draft.value)
      lastApply.value = result
      appliedAt.value = Date.now()
      draft.value = {}
      await Promise.all([loadEffective(), loadRestartPending()])
      return result
    } catch (caught) {
      fail(caught)
      return null
    } finally {
      applying.value = false
    }
  }

  async function reset(): Promise<boolean> {
    try {
      await configApi.reset()
      draft.value = {}
      await Promise.all([loadEffective(), loadRestartPending()])
      return true
    } catch (caught) {
      fail(caught)
      return false
    }
  }

  async function loadRaw(): Promise<RawConfig | null> {
    try {
      return await configApi.raw()
    } catch (caught) {
      fail(caught)
      return null
    }
  }

  async function saveRaw(yaml: string): Promise<boolean> {
    try {
      await configApi.saveRaw(yaml)
      await Promise.all([loadEffective(), loadRestartPending()])
      return true
    } catch (caught) {
      fail(caught)
      return false
    }
  }

  function setLevel(next: ConfigLevel): void {
    level.value = next
  }

  function setArea(next: string): void {
    area.value = next
  }

  return {
    schema,
    effective,
    restartPending,
    level,
    area,
    includeUnused,
    draft,
    loading,
    applying,
    error,
    lastApply,
    appliedAt,
    dirtyKeys,
    isDirty,
    fields,
    effectiveByKey,
    loadSchema,
    loadEffective,
    loadRestartPending,
    refresh,
    setValue,
    discard,
    isDirtyKey,
    draftValue,
    validate,
    apply,
    reset,
    loadRaw,
    saveRaw,
    setLevel,
    setArea,
  }
})
