<script setup lang="ts">
/**
 * 世界时间线页（W5 §16、§62、§71-§75、§145）。
 *
 * `worldStore.timeline` 是变更驱动的事件记录（不是每 tick 的噪声）；
 * limit 50/100/200 保存在 URL query 里；话题动作 resolve/forget/delete
 * 全部先 ConfirmDialog 二次确认，确认后走 `worldStore.topicAction`。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import { useWorldStore } from '@/stores/world'
import type { TopicRow, WorldTimelineRow } from '@/types/domain'

const route = useRoute()
const router = useRouter()
const store = useWorldStore()

const LIMITS = [50, 100, 200]
const limit = ref(100)
const loading = ref(false)

function readLimit(): number {
  const raw = typeof route.query.limit === 'string' ? Number(route.query.limit) : NaN
  return LIMITS.includes(raw) ? raw : 100
}

async function loadTimeline(): Promise<void> {
  loading.value = true
  await store.loadTimeline(limit.value)
  loading.value = false
}

watch(
  () => route.query.limit,
  () => {
    limit.value = readLimit()
    void loadTimeline()
  },
  { immediate: true },
)

onMounted(() => {
  void store.loadTopics()
})

function retry(): void {
  void loadTimeline()
  void store.loadTopics()
}

function onLimitChange(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  const next = LIMITS.includes(value) ? value : 100
  void router.replace({
    query: { ...route.query, limit: next === 100 ? undefined : String(next) },
  })
}

// ---------------------------------------------------------------- 事件展示

const EVENT_LABELS: Record<string, string> = {
  world: '世界',
  decision: '决策',
  space: '空间',
  object: '物件',
  pet: '宠物',
  qq: 'QQ',
  bible: '设定',
  external_event: '外部事件',
}

function eventText(type: string): string {
  if (!type) return '—'
  return EVENT_LABELS[type] ?? type
}

function formatTime(ts: number | null): string {
  if (ts === null || ts === undefined || ts <= 0) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

function revisionText(row: WorldTimelineRow): string {
  const parts: string[] = []
  const world = row.revisions?.world
  const cognitive = row.revisions?.cognitive
  if (world !== null && world !== undefined) parts.push(`世界 #${world}`)
  if (cognitive !== null && cognitive !== undefined) parts.push(`认知 #${cognitive}`)
  return parts.length > 0 ? parts.join(' · ') : '—'
}

// ---------------------------------------------------------------- 话题动作

type TopicAction = 'resolve' | 'forget' | 'delete'

interface TopicActionMeta {
  key: TopicAction
  label: string
  title: string
  message: string
  detail: string
  confirmText: string
  danger: boolean
}

const TOPIC_ACTIONS: TopicActionMeta[] = [
  {
    key: 'resolve',
    label: '解决',
    title: '解决话题',
    message: '确定将该话题标记为已解决吗？',
    detail: '话题会闭环，不再出现在未闭环列表中。',
    confirmText: '解决',
    danger: false,
  },
  {
    key: 'forget',
    label: '忘记',
    title: '忘记话题',
    message: '确定让角色忘记该话题吗？',
    detail: '忘记后该话题不再参与后续对话上下文。',
    confirmText: '忘记',
    danger: true,
  },
  {
    key: 'delete',
    label: '删除',
    title: '删除话题',
    message: '确定删除该话题吗？',
    detail: '删除不可撤销：话题记录会被移除。',
    confirmText: '删除',
    danger: true,
  },
]

const pendingTopic = ref<{ topic: TopicRow; action: TopicAction } | null>(null)
const topicError = ref('')

function askTopic(topic: TopicRow, action: TopicAction): void {
  topicError.value = ''
  pendingTopic.value = { topic, action }
}

function pendingMeta(): TopicActionMeta | null {
  const pending = pendingTopic.value
  if (!pending) return null
  return TOPIC_ACTIONS.find((meta) => meta.key === pending.action) ?? null
}

const dialogTitle = computed(() => pendingMeta()?.title ?? '')
const dialogMessage = computed(() => {
  const pending = pendingTopic.value
  const meta = pendingMeta()
  if (!pending || !meta) return ''
  return `${meta.message}（${pending.topic.text || pending.topic.topic_id}）`
})
const dialogDetail = computed(() => pendingMeta()?.detail ?? '')
const dialogConfirmText = computed(() => pendingMeta()?.confirmText ?? '确认')
const dialogDanger = computed(() => pendingMeta()?.danger ?? false)

async function confirmTopicAction(): Promise<void> {
  const pending = pendingTopic.value
  pendingTopic.value = null
  if (!pending) return
  const ok = await store.topicAction(pending.topic.topic_id, pending.action)
  if (ok) {
    toast.success('话题操作已完成')
  } else {
    topicError.value = store.error
    toast.error(store.error || '话题操作失败')
  }
}
</script>

<template>
  <div class="cb-world-timeline" data-test="world-timeline">
    <SectionHeader
      title="世界时间线"
      description="按真实事件记录，不做每秒一条；只记录世界变化，不含每 tick 噪声。"
    >
      <template #actions>
        <label class="cb-world-timeline__limit">
          <span class="cb-caption">显示条数</span>
          <select data-test="timeline-limit" :value="limit" @change="onLimitChange">
            <option v-for="value in LIMITS" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
      </template>
    </SectionHeader>

    <ErrorState
      v-if="store.error && store.timeline.length === 0 && !loading"
      :message="store.error"
      @retry="retry"
    />

    <LoadingState
      v-else-if="loading && store.timeline.length === 0"
      label="正在读取世界时间线…"
      :rows="4"
    />

    <EmptyState
      v-else-if="store.timeline.length === 0"
      title="暂无世界事件"
      description="世界尚未产生变更驱动的事件记录。"
    />

    <ol v-else class="cb-world-timeline__list">
      <li
        v-for="(row, index) in store.timeline"
        :key="`${row.ts}-${index}`"
        class="cb-card cb-world-timeline__item"
        data-test="timeline-item"
      >
        <div class="cb-world-timeline__head">
          <time
            class="cb-world-timeline__time"
            data-test="timeline-time"
            :datetime="row.ts ? new Date(row.ts * 1000).toISOString() : undefined"
          >
            {{ formatTime(row.ts) }}
          </time>
          <span class="cb-world-timeline__event" data-test="timeline-event">
            {{ eventText(row.event_type) }}
          </span>
          <span class="cb-world-timeline__revisions" data-test="timeline-revisions">
            {{ revisionText(row) }}
          </span>
        </div>
        <p class="cb-world-timeline__summary" data-test="timeline-summary">
          {{ row.summary || '—' }}
        </p>
        <p class="cb-world-timeline__meta">
          <span data-test="timeline-location">地点：{{ row.location || '—' }}</span>
          <span data-test="timeline-action">动作：{{ row.action || '—' }}</span>
        </p>
      </li>
    </ol>

    <section class="cb-card cb-world-timeline__topics" data-test="world-topics">
      <SectionHeader title="话题" description="未闭环话题；解决 / 忘记 / 删除都需要先确认" />

      <p
        v-if="topicError"
        class="cb-world-timeline__error"
        data-test="topic-error"
        role="alert"
      >
        {{ topicError }}
      </p>

      <EmptyState
        v-if="store.topics.length === 0"
        title="暂无话题"
        description="当前没有需要闭环的话题。"
      />

      <ul v-else class="cb-world-timeline__topic-list">
        <li
          v-for="topic in store.topics"
          :key="topic.topic_id"
          class="cb-world-timeline__topic"
          data-test="topic-item"
        >
          <p class="cb-world-timeline__topic-text" data-test="topic-text">
            {{ topic.text || '—' }}
          </p>
          <p class="cb-world-timeline__topic-meta">
            <span data-test="topic-scope">{{ topic.scope || '—' }}</span>
            <span data-test="topic-status">{{ topic.status || '—' }}</span>
            <span data-test="topic-updated">{{ formatTime(topic.updated_at) }}</span>
          </p>
          <div class="cb-world-timeline__topic-actions">
            <button
              v-for="meta in TOPIC_ACTIONS"
              :key="meta.key"
              type="button"
              class="cb-world-timeline__topic-button"
              :class="{ 'cb-world-timeline__topic-button--danger': meta.danger }"
              :data-test="`topic-${meta.key}-${topic.topic_id}`"
              @click="askTopic(topic, meta.key)"
            >
              {{ meta.label }}
            </button>
          </div>
        </li>
      </ul>
    </section>

    <ConfirmDialog
      :show="pendingTopic !== null"
      :title="dialogTitle"
      :message="dialogMessage"
      :detail="dialogDetail"
      :confirm-text="dialogConfirmText"
      :danger="dialogDanger"
      @confirm="confirmTopicAction"
      @cancel="pendingTopic = null"
    />
  </div>
</template>

<style scoped>
.cb-world-timeline {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-world-timeline__limit {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.cb-world-timeline__limit select {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-world-timeline__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-world-timeline__item {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-world-timeline__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--cb-space-3);
}

.cb-world-timeline__time {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
}

.cb-world-timeline__event {
  padding: 0 var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-world-timeline__revisions {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
}

.cb-world-timeline__summary {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-world-timeline__meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-4);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-world-timeline__topics {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.cb-world-timeline__error {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-world-timeline__topic-list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-world-timeline__topic {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.cb-world-timeline__topic-text {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-world-timeline__topic-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-3);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-world-timeline__topic-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
}

.cb-world-timeline__topic-button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-world-timeline__topic-button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-world-timeline__topic-button--danger {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}

.cb-world-timeline__topic-button--danger:hover {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}
</style>
