<script setup lang="ts">
/**
 * 记忆卡片（W5 §26/§29/§67-§75）：时间、摘要/内容（截断 + 展开）、类型、
 * scope 展示名、重要性；`provenance.episode_key` 只在 Expert（或详情页）显示。
 */
import { computed, ref } from 'vue'

import type { MemoryRow } from '@/types/domain'

const props = withDefaults(defineProps<{ memory: MemoryRow; expert?: boolean }>(), {
  expert: false,
})

/** 超过这个长度就折叠；展开/收起只影响展示，不改数据。 */
const TRUNCATE_AT = 140

const LAYER_LABELS: Record<string, string> = { semantic: '语义', episodic: '情景' }
const CATEGORY_LABELS: Record<string, string> = {
  fact: '事实',
  preference: '偏好',
  profile: '画像',
  project: '项目',
  interest: '兴趣',
  habit: '习惯',
  event: '事件',
  relationship: '关系',
  instruction: '指示',
}

const expanded = ref(false)

const text = computed(() => props.memory.summary?.trim() || props.memory.content || '')
const truncated = computed(() => text.value.length > TRUNCATE_AT)
const visibleText = computed(() =>
  expanded.value || !truncated.value ? text.value : `${text.value.slice(0, TRUNCATE_AT)}…`,
)

function label(map: Record<string, string>, value: string, fallback: string): string {
  if (!value) return fallback
  return map[value] ?? value
}

const layerLabel = computed(() => label(LAYER_LABELS, props.memory.layer, '—'))
const categoryLabel = computed(() => label(CATEGORY_LABELS, props.memory.category, '—'))

const scopeLabel = computed(() => {
  const raw = props.memory.scope_key ?? ''
  if (!raw) return '—'
  const [kind, ...rest] = raw.split(':')
  const ref = rest.join(':')
  if (kind === 'user') return ref ? `用户 ${ref}` : '用户'
  if (kind === 'group') return ref ? `群 ${ref}` : '群'
  if (kind === 'character') return '角色'
  if (kind === 'global') return '全局'
  return raw
})

const importanceLabel = computed(() => {
  const value = props.memory.importance
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(value * 100)}%`
})

const episodeKey = computed(() => {
  const value = props.memory.provenance?.episode_key
  if (typeof value === 'string') return value
  if (typeof value === 'number') return String(value)
  return ''
})

function formatTime(value: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—'
  const date = new Date(value * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

const timeLabel = computed(() => formatTime(props.memory.created_at))
</script>

<template>
  <article class="cb-memory-card cb-card" data-test="memory-card">
    <header class="cb-memory-card__head">
      <time class="cb-memory-card__time" data-test="memory-time">{{ timeLabel }}</time>
      <span class="cb-memory-card__tags">
        <span class="cb-memory-card__tag" data-test="memory-layer">{{ layerLabel }}</span>
        <span class="cb-memory-card__tag" data-test="memory-category">{{ categoryLabel }}</span>
      </span>
    </header>

    <p class="cb-memory-card__text" data-test="memory-text">{{ visibleText }}</p>
    <button
      v-if="truncated"
      type="button"
      class="cb-memory-card__toggle"
      data-test="memory-toggle"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开' }}
    </button>

    <footer class="cb-memory-card__foot">
      <span class="cb-memory-card__meta" data-test="memory-scope">{{ scopeLabel }}</span>
      <span class="cb-memory-card__meta" data-test="memory-importance">
        重要性 {{ importanceLabel }}
      </span>
      <span
        v-if="expert && episodeKey"
        class="cb-memory-card__meta cb-memory-card__meta--expert"
        data-test="memory-episode"
      >
        来源 episode：{{ episodeKey }}
      </span>
    </footer>
  </article>
</template>

<style scoped>
.cb-memory-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-2);
  flex-wrap: wrap;
}

.cb-memory-card__time {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  font-variant-numeric: tabular-nums;
}

.cb-memory-card__tags {
  display: inline-flex;
  gap: var(--cb-space-1);
}

.cb-memory-card__tag {
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-memory-card__text {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.cb-memory-card__toggle {
  align-self: flex-start;
  padding: 2px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-primary-strong);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-memory-card__toggle:hover {
  border-color: var(--cb-primary);
}

.cb-memory-card__foot {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-3);
  padding-top: var(--cb-space-2);
  border-top: 1px solid var(--cb-border);
}

.cb-memory-card__meta {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.cb-memory-card__meta--expert {
  font-family: var(--cb-font-mono);
  color: var(--cb-text-faint);
  overflow-wrap: anywhere;
}
</style>
