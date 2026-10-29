<script setup lang="ts">
/**
 * 记忆筛选（W5 §27）：关键词 300ms 防抖；模式/人物/类型/层/状态/每页条数。
 *
 * 组件不请求、不写 URL：所有变更通过 `update:model` 交给页面写进地址栏。
 * 语义模式始终可选——后端不知道向量是否可用，失败时由后端返回错误。
 */
import { onBeforeUnmount, ref, watch } from 'vue'

import type { MemoryFilters } from '@/types/domain'

const props = defineProps<{ model: MemoryFilters }>()
const emit = defineEmits<{ 'update:model': [model: MemoryFilters] }>()

const DEBOUNCE_MS = 300

const MODE_OPTIONS: { value: string; label: string }[] = [
  { value: 'hybrid', label: '混合' },
  { value: 'keyword', label: '关键词' },
  { value: 'semantic', label: '语义' },
]

const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: 'fact', label: '事实' },
  { value: 'preference', label: '偏好' },
  { value: 'profile', label: '画像' },
  { value: 'project', label: '项目' },
  { value: 'interest', label: '兴趣' },
  { value: 'habit', label: '习惯' },
  { value: 'event', label: '事件' },
  { value: 'relationship', label: '关系' },
  { value: 'instruction', label: '指示' },
]

const LAYER_OPTIONS: { value: string; label: string }[] = [
  { value: 'semantic', label: '语义' },
  { value: 'episodic', label: '情景' },
]

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'active', label: '活跃' },
  { value: 'archived', label: '已归档' },
]

const LIMIT_OPTIONS = [10, 20, 50, 100]

const form = ref<MemoryFilters>({ mode: 'hybrid', ...props.model })

watch(
  () => props.model,
  (value) => {
    form.value = { mode: 'hybrid', ...value }
  },
  { deep: true },
)

let qTimer: ReturnType<typeof setTimeout> | null = null
let personTimer: ReturnType<typeof setTimeout> | null = null

function emitModel(next: MemoryFilters): void {
  emit('update:model', next)
}

function commitText(key: 'q' | 'person'): void {
  const raw = form.value[key]
  const trimmed = typeof raw === 'string' ? raw.trim() : ''
  form.value = { ...form.value, [key]: trimmed || undefined }
  emitModel({ ...form.value })
}

function onText(key: 'q' | 'person', event: Event): void {
  const value = (event.target as HTMLInputElement).value
  form.value = { ...form.value, [key]: value }
  if (key === 'q') {
    if (qTimer) clearTimeout(qTimer)
    qTimer = setTimeout(() => commitText('q'), DEBOUNCE_MS)
  } else {
    if (personTimer) clearTimeout(personTimer)
    personTimer = setTimeout(() => commitText('person'), DEBOUNCE_MS)
  }
}

function onSelect(key: string, event: Event): void {
  const raw = (event.target as HTMLSelectElement).value
  if (key === 'limit') {
    form.value = { ...form.value, limit: raw === '' ? undefined : Number(raw) }
  } else {
    form.value = { ...form.value, [key]: raw || undefined }
  }
  emitModel({ ...form.value })
}

onBeforeUnmount(() => {
  if (qTimer) clearTimeout(qTimer)
  if (personTimer) clearTimeout(personTimer)
})
</script>

<template>
  <div class="cb-memory-filters" data-test="memory-filters">
    <label class="cb-memory-filters__field cb-memory-filters__field--wide">
      <span class="cb-caption">关键词</span>
      <input
        type="search"
        class="cb-memory-filters__input"
        placeholder="搜索记忆内容…"
        autocomplete="off"
        data-test="filter-q"
        :value="form.q ?? ''"
        @input="onText('q', $event)"
      />
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">模式</span>
      <select
        class="cb-memory-filters__select"
        data-test="filter-mode"
        :value="form.mode ?? 'hybrid'"
        @change="onSelect('mode', $event)"
      >
        <option v-for="option in MODE_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">人物</span>
      <input
        type="text"
        class="cb-memory-filters__input"
        placeholder="QQ 号或展示名"
        autocomplete="off"
        data-test="filter-person"
        :value="form.person ?? ''"
        @input="onText('person', $event)"
      />
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">类型</span>
      <select
        class="cb-memory-filters__select"
        data-test="filter-category"
        :value="form.category ?? ''"
        @change="onSelect('category', $event)"
      >
        <option value="">全部类型</option>
        <option v-for="option in CATEGORY_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">层</span>
      <select
        class="cb-memory-filters__select"
        data-test="filter-layer"
        :value="form.layer ?? ''"
        @change="onSelect('layer', $event)"
      >
        <option value="">全部层</option>
        <option v-for="option in LAYER_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">状态</span>
      <select
        class="cb-memory-filters__select"
        data-test="filter-status"
        :value="form.status ?? 'active'"
        @change="onSelect('status', $event)"
      >
        <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </label>

    <label class="cb-memory-filters__field">
      <span class="cb-caption">每页条数</span>
      <select
        class="cb-memory-filters__select"
        data-test="filter-limit"
        :value="String(form.limit ?? 20)"
        @change="onSelect('limit', $event)"
      >
        <option v-for="value in LIMIT_OPTIONS" :key="value" :value="String(value)">
          {{ value }} 条
        </option>
      </select>
    </label>

    <p class="cb-memory-filters__hint">
      语义模式依赖向量服务；如果不可用，后端会返回错误而不是静默降级。
    </p>
  </div>
</template>

<style scoped>
.cb-memory-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--cb-space-3);
}

.cb-memory-filters__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 150px;
}

.cb-memory-filters__field--wide {
  flex: 1 1 240px;
}

.cb-memory-filters__input,
.cb-memory-filters__select {
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-memory-filters__input:focus,
.cb-memory-filters__select:focus {
  border-color: var(--cb-primary);
  outline: none;
  box-shadow: var(--cb-focus);
}

.cb-memory-filters__hint {
  flex: 1 1 100%;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}
</style>
