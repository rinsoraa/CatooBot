/** 会话状态：bootstrap / login / logout / 401 反应（§9、§12）。 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { ApiError, api, errorMessage } from '@/api/client'
import type { SessionData, SessionStatus, SessionUser } from '@/types/auth'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<SessionUser | null>(null)
  const status = ref<SessionStatus>('unknown')
  const loading = ref(false)
  const error = ref('')

  const isAuthenticated = computed(() => status.value === 'authenticated')

  function applySession(data: SessionData): void {
    user.value = data.user
    status.value = 'authenticated'
    error.value = ''
    api.setCsrf(data.csrf_token)
  }

  function handleUnauthorized(): void {
    user.value = null
    status.value = 'anonymous'
    api.clearCsrf()
  }

  // 任何写请求拿到 401 → 清会话（路由守卫随后把用户送回登录页）。
  api.onUnauthorized(handleUnauthorized)

  /** 打开页面先问后端「我是谁」；网络故障不当作未登录。 */
  async function bootstrap(): Promise<boolean> {
    loading.value = true
    try {
      const data = await api.get<SessionData>('/session', { skipAuthHandler: true })
      applySession(data)
      return true
    } catch (caught) {
      if (caught instanceof ApiError && caught.isUnauthorized) {
        handleUnauthorized()
        return false
      }
      error.value = errorMessage(caught)
      status.value = 'unknown'
      return false
    } finally {
      loading.value = false
    }
  }

  /** bootstrap 的强制版：路由守卫在首次进入时等它。 */
  async function ensureLoaded(): Promise<void> {
    if (status.value === 'unknown' && !loading.value) {
      await bootstrap()
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    loading.value = true
    error.value = ''
    try {
      const data = await api.post<SessionData>('/session', { username, password })
      applySession(data)
      return true
    } catch (caught) {
      error.value = errorMessage(caught)
      return false
    } finally {
      loading.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await api.del<{ logged_out: boolean }>('/session')
    } catch {
      /* 已经失效的会话也算登出成功 */
    } finally {
      handleUnauthorized()
    }
  }

  return {
    user,
    status,
    loading,
    error,
    isAuthenticated,
    bootstrap,
    ensureLoaded,
    login,
    logout,
    handleUnauthorized,
  }
})
