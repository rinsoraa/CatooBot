/**
 * 世界（Sandbox）读取模型（W5 §18/§62/§104）。
 *
 * 首屏 REST 快照 + 由 realtime 的 `world` 主题触发**重新拉取**；
 * WebSocket 断线时保留最后已知状态，只标记「可能不是最新」，绝不清空。
 */

import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

import { errorMessage } from '@/api/client'
import { worldApi } from '@/api/world'
import { useRealtimeStore } from '@/stores/realtime'
import type { TopicRow, WorldData, WorldTimelineRow } from '@/types/domain'

export const useWorldStore = defineStore('world', () => {
  const world = ref<WorldData | null>(null)
  const timeline = ref<WorldTimelineRow[]>([])
  const topics = ref<TopicRow[]>([])
  const loading = ref(false)
  const error = ref('')
  const lastLoadedAt = ref(0)
  const stale = ref(false)

  const realtime = useRealtimeStore()

  const enabled = computed(() => world.value !== null && world.value.phase !== undefined)
  const actionName = computed(() => world.value?.action?.name ?? '')
  const needsPressing = computed(() => world.value?.needs?.pressing ?? [])
  const goals = computed(() => world.value?.goals ?? world.value?.goals_full ?? [])

  async function loadWorld(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      world.value = await worldApi.world()
      lastLoadedAt.value = Date.now()
      stale.value = !realtime.connected
    } catch (caught) {
      error.value = errorMessage(caught)
    } finally {
      loading.value = false
    }
  }

  async function loadTimeline(limit = 100): Promise<void> {
    try {
      const data = await worldApi.timeline(limit)
      timeline.value = data.items
    } catch (caught) {
      error.value = errorMessage(caught)
    }
  }

  async function loadTopics(): Promise<void> {
    try {
      const data = await worldApi.topics()
      topics.value = data.items
    } catch (caught) {
      error.value = errorMessage(caught)
    }
  }

  async function topicAction(topicId: string, action: 'resolve' | 'forget' | 'delete'): Promise<boolean> {
    try {
      await worldApi.topicAction(topicId, action)
    } catch (caught) {
      error.value = errorMessage(caught)
      return false
    }
    await loadTopics()
    return true
  }

  async function control(
    action: 'pause' | 'resume' | 'reset' | 'reinitialize',
    confirm?: string,
  ): Promise<boolean> {
    try {
      await worldApi.control(action, confirm)
    } catch (caught) {
      error.value = errorMessage(caught)
      return false
    }
    await loadWorld()
    return true
  }

  async function refresh(): Promise<void> {
    await loadWorld()
  }

  // 变化驱动的轻量刷新：realtime 收到 world 主题后，节流重新取一次真实快照。
  let pending: number | null = null
  watch(
    () => realtime.topics.world ?? 0,
    () => {
      if (pending !== null) window.clearTimeout(pending)
      pending = window.setTimeout(() => {
        pending = null
        void loadWorld()
      }, 1500)
    },
  )

  watch(
    () => realtime.connected,
    (connected) => {
      if (!connected && world.value) stale.value = true
      if (connected && world.value) stale.value = false
    },
  )

  function clear(): void {
    world.value = null
    timeline.value = []
    topics.value = []
    error.value = ''
    stale.value = false
  }

  return {
    world,
    timeline,
    topics,
    loading,
    error,
    lastLoadedAt,
    stale,
    enabled,
    actionName,
    needsPressing,
    goals,
    loadWorld,
    loadTimeline,
    loadTopics,
    topicAction,
    control,
    refresh,
    clear,
  }
})
