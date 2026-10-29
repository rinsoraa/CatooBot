<script setup lang="ts">
/**
 * 日志控制台（W5 §45-§50）：历史走 `systemApi.logsTail`（服务端过滤），
 * 实时走唯一的 realtime store（WS，绝不轮询）；同一条消息按 ts+message 只显示一次。
 *
 * 「清空视图」只清本地缓冲，不会删除服务器日志；本地最多保留 500 条（LogViewer）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { errorMessage } from '@/api/client'
import { systemApi } from '@/api/system'
import ErrorState from '@/components/ErrorState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import LogViewer, { type LogViewerEntry } from '@/components/domain/LogViewer.vue'
import { useRealtimeStore } from '@/stores/realtime'
import { LOG_LEVELS, type LogChannel } from '@/types/domain'
import type { RealtimeLogEntry } from '@/types/runtime'

const LIMITS = [100, 200, 500]

const realtime = useRealtimeStore()

const level = ref('ALL')
const channel = ref('')
const keyword = ref('')
const limit = ref(200)
const paused = ref(false)
const autoScroll = ref(true)
/** 清空视图的时间点（秒）：早于它的实时消息不再重新出现。 */
const clearedBefore = ref(0)

const channels = ref<LogChannel[]>([])
const history = ref<LogViewerEntry[]>([])
const loading = ref(false)
const error = ref('')

let keywordTimer: number | null = null

/** 实时 feed 里的行对象稳定（store 只前插），用它缓存解析结果，避免去重/追加重复。 */
const liveCache = new WeakMap<RealtimeLogEntry, LogViewerEntry>()

const levelOptions = computed(() => LOG_LEVELS.map((item) => ({ value: item, label: item === 'ALL' ? '全部等级' : item })))

function parseLiveText(text: string): { channel: string | null; level: string; message: string } {
  const match = /^\[([^\]]+)\]\s*(?:\(([^)]+)\)\s*)?([\s\S]*)$/.exec(text)
  if (match) {
    return { channel: match[1] ?? null, level: match[2] ?? '', message: match[3] ?? '' }
  }
  return { channel: null, level: '', message: text }
}

function toLiveEntry(item: RealtimeLogEntry): LogViewerEntry {
  const cached = liveCache.get(item)
  if (cached) return cached
  const parsed = parseLiveText(item.text)
  const entry: LogViewerEntry = {
    ts: item.ts,
    level: parsed.level,
    channel: parsed.channel,
    message: parsed.message,
  }
  liveCache.set(item, entry)
  return entry
}

function liveMatches(entry: LogViewerEntry): boolean {
  if (level.value !== 'ALL' && entry.level.toUpperCase() !== level.value) return false
  if (channel.value && entry.channel !== channel.value) return false
  const query = keyword.value.trim().toLowerCase()
  if (query && !entry.message.toLowerCase().includes(query)) return false
  return true
}

const liveEntries = computed<LogViewerEntry[]>(() => {
  const output: LogViewerEntry[] = []
  const feed = realtime.feed
  for (let index = feed.length - 1; index >= 0; index -= 1) {
    const item = feed[index]
    if (!item || (item.topic !== 'log' && item.topic !== 'narration')) continue
    if (item.ts <= clearedBefore.value) continue
    const entry = toLiveEntry(item)
    if (!liveMatches(entry)) continue
    output.push(entry)
  }
  return output
})

/** 历史（旧→新）+ 实时（旧→新），按 ts+message 去重。 */
const entries = computed<LogViewerEntry[]>(() => {
  const seen = new Set<string>()
  const merged: LogViewerEntry[] = []
  for (const entry of [...history.value, ...liveEntries.value]) {
    const key = `${entry.ts ?? entry.time ?? ''}|${entry.message}`
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(entry)
  }
  return merged
})

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const data = await systemApi.logsTail({
      level: level.value,
      channel: channel.value,
      q: keyword.value.trim(),
      limit: limit.value,
    })
    // 后端 tail 按「新→旧」返回；控制台按时间顺序追加，这里翻转为「旧→新」。
    history.value = (data.items ?? [])
      .map<LogViewerEntry>((row) => ({
        ts: row.ts ?? null,
        time: row.time,
        level: row.level ?? '',
        channel: row.channel ?? null,
        message: row.message,
      }))
      .reverse()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function loadChannels(): Promise<void> {
  try {
    const data = await systemApi.logChannels()
    channels.value = data.channels ?? []
  } catch {
    channels.value = []
  }
}

function togglePause(): void {
  paused.value = !paused.value
}

function clearView(): void {
  history.value = []
  clearedBefore.value = Math.floor(Date.now() / 1000)
}

function onKeywordInput(): void {
  if (keywordTimer !== null) window.clearTimeout(keywordTimer)
  keywordTimer = window.setTimeout(() => {
    keywordTimer = null
    void load()
  }, 300)
}

watch([level, channel, limit], () => void load())

onMounted(() => {
  void load()
  void loadChannels()
})

onBeforeUnmount(() => {
  if (keywordTimer !== null) window.clearTimeout(keywordTimer)
})
</script>

<template>
  <div class="logs" data-test="system-logs">
    <section class="logs__section">
      <SectionHeader
        title="日志"
        description="历史由服务端过滤；实时通过唯一的 WebSocket 通道推送，不做轮询。"
      >
        <template #actions>
          <span class="cb-caption" data-test="logs-connection">
            {{ realtime.label }}{{ realtime.connected ? ` · 收到 ${realtime.topics.log ?? realtime.topics.narration ?? 0} 条实时日志` : '' }}
          </span>
        </template>
      </SectionHeader>

      <form class="logs__filters" data-test="logs-filters" @submit.prevent="load">
        <label class="logs__field">
          <span>等级</span>
          <select v-model="level" data-test="logs-level">
            <option v-for="option in levelOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="logs__field">
          <span>通道</span>
          <select v-model="channel" data-test="logs-channel">
            <option value="">全部通道</option>
            <option v-for="item in channels" :key="item.key" :value="item.key">
              {{ item.icon }} {{ item.label }}
            </option>
          </select>
        </label>
        <label class="logs__field logs__field--grow">
          <span>关键词</span>
          <input
            v-model="keyword"
            type="search"
            placeholder="输入后自动过滤（300ms 防抖）"
            data-test="logs-keyword"
            @input="onKeywordInput"
          />
        </label>
        <label class="logs__field">
          <span>读取条数</span>
          <select v-model.number="limit" data-test="logs-limit">
            <option v-for="value in LIMITS" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
        <button type="submit" class="logs__button logs__button--primary" :disabled="loading" data-test="logs-refresh">
          {{ loading ? '读取中…' : '刷新' }}
        </button>
      </form>

      <div class="logs__actions">
        <button type="button" class="logs__button" data-test="logs-pause" @click="togglePause">
          {{ paused ? '恢复' : '暂停' }}
        </button>
        <span class="cb-caption">
          {{ paused ? '已暂停：新消息只计数，不追加到视图。' : '实时追加中。' }}
        </span>
        <span class="cb-caption">本地最多保留 500 条；「清空视图」不会删除服务器日志。</span>
      </div>

      <ErrorState v-if="error" :message="error" @retry="load" />
      <LogViewer
        v-else
        v-model:auto-scroll="autoScroll"
        :entries="entries"
        :paused="paused"
        @clear="clearView"
      />
    </section>
  </div>
</template>

<style scoped>
.logs {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.logs__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.logs__filters {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.logs__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.logs__field--grow {
  flex: 1;
  min-width: 200px;
}

.logs__field input,
.logs__field select {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.logs__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.logs__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
  white-space: nowrap;
}

.logs__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.logs__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.logs__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
