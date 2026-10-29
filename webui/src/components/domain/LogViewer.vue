<script lang="ts">
/** 日志行（W5 §45-§50）：历史行只有 time，实时行才有 ts（秒或毫秒）。 */
export interface LogViewerEntry {
  ts: number | null
  /** 服务器日志行里的 `HH:MM:SS`；`ts` 缺失时的回退展示。 */
  time?: string
  level: string
  channel?: string | null
  message: string
}
</script>

<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

/** 有界缓冲：最多 500 条，超出丢最旧（§47、§61-§62）。 */
const MAX_ENTRIES = 500

const props = withDefaults(
  defineProps<{
    entries: LogViewerEntry[]
    paused?: boolean
    autoScroll?: boolean
  }>(),
  { paused: false, autoScroll: true },
)

const emit = defineEmits<{
  clear: []
  'update:autoScroll': [value: boolean]
}>()

const buffer = ref<LogViewerEntry[]>([])
/** 暂停期间到达、尚未追加的行；计数给「已暂停 · 新消息 N 条」。 */
const pending = ref<LogViewerEntry[]>([])
/** 已消费的行对象（按引用），避免父级列表前移后被重复追加。 */
const consumed = new Set<LogViewerEntry>()
const listEl = ref<HTMLElement | null>(null)

function append(items: LogViewerEntry[]): void {
  if (items.length === 0) return
  buffer.value = [...buffer.value, ...items].slice(-MAX_ENTRIES)
}

watch(
  () => props.entries,
  (next) => {
    const fresh = next.filter((entry) => !consumed.has(entry))
    if (fresh.length === 0) return
    for (const entry of fresh) consumed.add(entry)
    if (props.paused) {
      pending.value = [...pending.value, ...fresh].slice(-MAX_ENTRIES)
      return
    }
    append(fresh)
  },
  { immediate: true },
)

watch(
  () => props.paused,
  (isPaused) => {
    if (isPaused) return
    const queued = pending.value
    pending.value = []
    append(queued)
  },
)

watch(
  () => buffer.value.length,
  () => {
    if (!props.autoScroll || props.paused) return
    void nextTick(() => {
      const element = listEl.value
      if (element) element.scrollTop = element.scrollHeight
    })
  },
)

function clearView(): void {
  buffer.value = []
  pending.value = []
  // 把当前父级列表里的行都标记为已消费：清空后不再被重新追加
  for (const entry of props.entries) consumed.add(entry)
  emit('clear')
}

function toggleAutoScroll(): void {
  emit('update:autoScroll', !props.autoScroll)
}

function levelClass(level: string): string {
  const normalized = level.toLowerCase()
  if (normalized === 'debug' || normalized === 'info' || normalized === 'warning' || normalized === 'error') {
    return normalized
  }
  return 'other'
}

function formatTimestamp(entry: LogViewerEntry): string {
  if (typeof entry.ts === 'number' && Number.isFinite(entry.ts)) {
    const millis = entry.ts > 1e12 ? entry.ts : entry.ts * 1000
    const date = new Date(millis)
    if (!Number.isNaN(date.getTime())) {
      const pad = (value: number): string => String(value).padStart(2, '0')
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
        date.getHours(),
      )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
    }
  }
  return entry.time || '—'
}
</script>

<template>
  <div class="log-viewer" data-test="log-viewer">
    <div class="log-viewer__toolbar">
      <span class="cb-caption" data-test="log-bound-note">本地最多保留 {{ MAX_ENTRIES }} 条</span>
      <div class="log-viewer__actions">
        <button
          type="button"
          class="log-viewer__button"
          role="switch"
          :aria-checked="autoScroll ? 'true' : 'false'"
          data-test="log-autoscroll"
          @click="toggleAutoScroll"
        >
          自动滚动：{{ autoScroll ? '开' : '关' }}
        </button>
        <button
          type="button"
          class="log-viewer__button log-viewer__button--danger"
          data-test="log-clear"
          title="仅清空当前视图，不会删除服务器日志"
          @click="clearView"
        >
          清空视图
        </button>
      </div>
    </div>

    <p class="log-viewer__notice cb-caption">
      「清空视图」只清空浏览器里的本地缓冲，不会删除服务器日志。
    </p>

    <p v-if="paused" class="log-viewer__paused" data-test="log-paused">
      已暂停 · 新消息 {{ pending.length }} 条
    </p>

    <ol
      ref="listEl"
      class="log-viewer__list"
      role="log"
      aria-live="polite"
      aria-relevant="additions"
      data-test="log-list"
    >
      <li
        v-for="(entry, index) in buffer"
        :key="`${index}-${entry.ts ?? entry.time ?? ''}`"
        class="log-viewer__row"
        :class="`log-viewer__row--${levelClass(entry.level)}`"
        data-test="log-row"
      >
        <span class="log-viewer__time" data-test="log-time">{{ formatTimestamp(entry) }}</span>
        <span class="log-viewer__level" data-test="log-level">{{ entry.level || '—' }}</span>
        <span class="log-viewer__channel" data-test="log-channel">{{
          entry.channel ? `[${entry.channel}]` : '—'
        }}</span>
        <span class="log-viewer__message" data-test="log-message">{{ entry.message }}</span>
      </li>
    </ol>

    <p v-if="buffer.length === 0" class="log-viewer__empty cb-muted" data-test="log-empty">
      暂无日志行
    </p>
  </div>
</template>

<style scoped>
.log-viewer {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  min-width: 0;
}

.log-viewer__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.log-viewer__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.log-viewer__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.log-viewer__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.log-viewer__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.log-viewer__notice {
  color: var(--cb-text-faint);
}

.log-viewer__paused {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
}

.log-viewer__list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: var(--cb-space-2);
  list-style: none;
  max-height: 460px;
  overflow-y: auto;
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.log-viewer__row {
  display: grid;
  grid-template-columns: 148px 62px minmax(84px, auto) 1fr;
  gap: var(--cb-space-2);
  align-items: baseline;
  padding: 2px var(--cb-space-2);
  border-radius: var(--cb-radius-sm);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  line-height: var(--cb-line);
}

.log-viewer__row:hover {
  background: var(--cb-surface-raised);
}

.log-viewer__time {
  color: var(--cb-text-faint);
  white-space: nowrap;
}

.log-viewer__level {
  font-weight: 600;
  white-space: nowrap;
}

.log-viewer__row--debug .log-viewer__level {
  color: var(--cb-text-faint);
}
.log-viewer__row--info .log-viewer__level {
  color: var(--cb-text-muted);
}
.log-viewer__row--warning .log-viewer__level {
  color: var(--cb-warning);
}
.log-viewer__row--error .log-viewer__level {
  color: var(--cb-danger);
}
.log-viewer__row--other .log-viewer__level {
  color: var(--cb-text-muted);
}

.log-viewer__channel {
  color: var(--cb-info);
  white-space: nowrap;
}

.log-viewer__message {
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.log-viewer__empty {
  padding: var(--cb-space-4);
  border: 1px dashed var(--cb-border-strong);
  border-radius: var(--cb-radius-md);
  text-align: center;
  font-size: var(--cb-text-sm);
}
</style>
