/**
 * Runtime 读取模型缓存（§18、§19）。
 *
 * 这里只是 WebUI 的 UI cache：页面刷新后一切重新从 `/api/v1` 取；
 * 绝不把浏览器里的旧状态当成真相。
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import { api, errorMessage } from '@/api/client'
import type {
  CountsSnapshot,
  OverviewData,
  QqSnapshot,
  RuntimeData,
  RuntimeSnapshot,
  SchedulerSnapshot,
  WorldSnapshot,
} from '@/types/runtime'

export const useRuntimeStore = defineStore('runtime', () => {
  const overview = ref<OverviewData | null>(null)
  const runtime = ref<RuntimeData | null>(null)
  const world = ref<WorldSnapshot | null>(null)
  const scheduler = ref<SchedulerSnapshot | null>(null)
  const qq = ref<QqSnapshot | null>(null)

  const loading = ref(false)
  const error = ref('')
  const lastLoadedAt = ref(0)

  const counts = computed<CountsSnapshot>(() => overview.value?.counts ?? {})
  const online = computed<boolean>(() => Boolean(overview.value?.qq?.online))

  /** 进程运行时长：`/runtime` 把它放在 `process.uptime_seconds`（W2 契约）。 */
  function uptimeOf(snapshot: RuntimeSnapshot | null | undefined): number | null {
    if (!snapshot) return null
    const fromProcess = snapshot.process?.uptime_seconds
    if (typeof fromProcess === 'number') return fromProcess
    const legacy = snapshot.uptime_seconds
    return typeof legacy === 'number' ? legacy : null
  }

  const uptimeSeconds = computed<number | null>(
    () => uptimeOf(runtime.value) ?? uptimeOf(overview.value?.runtime),
  )

  /** 首屏快照：两个 GET，然后交给 WebSocket 增量（§16）。 */
  async function loadInitial(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const snapshot = await api.get<OverviewData>('/overview')
      applyOverview(snapshot)
      const detail = await api.get<RuntimeData>('/runtime')
      runtime.value = detail
      lastLoadedAt.value = Date.now()
    } catch (caught) {
      error.value = errorMessage(caught)
    } finally {
      loading.value = false
    }
  }

  async function refresh(): Promise<void> {
    await loadInitial()
  }

  function applyOverview(data: OverviewData): void {
    overview.value = data
    if (data.world && Object.keys(data.world).length > 0) world.value = data.world
    if (data.qq) qq.value = data.qq
    if (data.runtime) runtime.value = { ...(runtime.value ?? {}), ...data.runtime }
    if (data.runtime?.scheduler) scheduler.value = data.runtime.scheduler
    lastLoadedAt.value = Date.now()
  }

  /** `status` topic：W2 的 status 载荷带 qq / world / runtime 段。 */
  function applyStatus(data: Record<string, unknown>): void {
    const qqPart = data.qq as QqSnapshot | undefined
    const worldPart = data.world as WorldSnapshot | undefined
    const runtimePart = data.runtime as RuntimeData | undefined
    if (qqPart) qq.value = { ...(qq.value ?? {}), ...qqPart }
    if (worldPart && Object.keys(worldPart).length > 0) world.value = worldPart
    if (runtimePart) {
      runtime.value = { ...(runtime.value ?? {}), ...runtimePart }
      if (runtimePart.scheduler) scheduler.value = runtimePart.scheduler
    }
    lastLoadedAt.value = Date.now()
  }

  /** `world` topic：变化驱动，直接替换。 */
  function applyWorld(data: WorldSnapshot): void {
    world.value = data
    lastLoadedAt.value = Date.now()
  }

  /** `scheduler` topic。 */
  function applyScheduler(data: SchedulerSnapshot): void {
    scheduler.value = data
    runtime.value = { ...(runtime.value ?? {}), scheduler: data }
    lastLoadedAt.value = Date.now()
  }

  function clear(): void {
    overview.value = null
    runtime.value = null
    world.value = null
    scheduler.value = null
    qq.value = null
    error.value = ''
  }

  return {
    overview,
    runtime,
    world,
    scheduler,
    qq,
    counts,
    online,
    uptimeSeconds,
    loading,
    error,
    lastLoadedAt,
    loadInitial,
    refresh,
    applyOverview,
    applyStatus,
    applyWorld,
    applyScheduler,
    clear,
  }
})
