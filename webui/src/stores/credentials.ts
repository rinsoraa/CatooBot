/**
 * Credentials Store（W4 §58-§59）：只持有 masked 状态；明文永不进入前端状态。
 */

import { ref } from 'vue'
import { defineStore } from 'pinia'

import { credentialsApi, type CredentialItem } from '@/api/config'
import { errorMessage } from '@/api/client'
import type { TestResult } from '@/types/ai'

export const useCredentialsStore = defineStore('credentials', () => {
  const items = ref<CredentialItem[]>([])
  const loading = ref(false)
  const error = ref('')

  async function load(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      items.value = await credentialsApi.list()
    } catch (caught) {
      error.value = errorMessage(caught)
    } finally {
      loading.value = false
    }
  }

  async function save(domain: string, ref_: string, value: string): Promise<boolean> {
    error.value = ''
    try {
      await credentialsApi.save(domain, ref_, value)
    } catch (caught) {
      error.value = errorMessage(caught)
      return false
    }
    await load()
    return true
  }

  async function remove(domain: string, ref_: string, force = false): Promise<boolean> {
    error.value = ''
    try {
      await credentialsApi.remove(domain, ref_, force)
    } catch (caught) {
      error.value = errorMessage(caught)
      return false
    }
    await load()
    return true
  }

  async function test(payload: { provider: string; model?: string; value?: string }): Promise<TestResult | null> {
    error.value = ''
    try {
      return await credentialsApi.test(payload)
    } catch (caught) {
      error.value = errorMessage(caught)
      return null
    }
  }

  function clear(): void {
    items.value = []
    error.value = ''
  }

  return { items, loading, error, load, save, remove, test, clear }
})
