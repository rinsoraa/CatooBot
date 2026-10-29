<script setup lang="ts">
/**
 * 设置搜索（W4 §85-§86）：200ms 防抖，匹配 label / description / key。
 *
 * 结果行展示 名称 / 分类 / 级别 / 状态；↑/↓ 移动、Enter 选中、
 * Esc 关闭；选中只 emit(key)，定位与高亮由页面负责。
 */
import { computed, onBeforeUnmount, ref } from 'vue'

import type { ConfigFieldMeta } from '@/types/config'
import { USAGE_LABELS } from '@/types/config'

const props = defineProps<{ fields: ConfigFieldMeta[] }>()

const emit = defineEmits<{ select: [key: string] }>()

const LEVEL_LABELS: Record<string, string> = {
  basic: '基础',
  advanced: '高级',
  expert: '专家',
}

const keyword = ref('')
const debounced = ref('')
const open = ref(false)
const activeIndex = ref(-1)
let timer: ReturnType<typeof setTimeout> | null = null

function onInput(value: string): void {
  keyword.value = value
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    debounced.value = value.trim()
    activeIndex.value = -1
    open.value = true
  }, 200)
}

const results = computed<ConfigFieldMeta[]>(() => {
  const query = debounced.value.toLowerCase()
  if (!query) return []
  return props.fields
    .filter((field) => {
      const haystack = `${field.label}\n${field.description}\n${field.key}`.toLowerCase()
      return haystack.includes(query)
    })
    .slice(0, 20)
})

const showEmpty = computed(() => open.value && debounced.value.length > 0 && results.value.length === 0)

function statusLabel(field: ConfigFieldMeta): string {
  return USAGE_LABELS[field.usage_status] ?? field.usage_status
}

function levelLabel(field: ConfigFieldMeta): string {
  return LEVEL_LABELS[field.level] ?? field.level
}

function choose(field: ConfigFieldMeta): void {
  emit('select', field.key)
  open.value = false
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    open.value = false
    return
  }
  if (!results.value.length) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    open.value = true
    activeIndex.value = (activeIndex.value + 1) % results.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    open.value = true
    activeIndex.value = activeIndex.value <= 0 ? results.value.length - 1 : activeIndex.value - 1
  } else if (event.key === 'Enter') {
    const target = results.value[activeIndex.value] ?? results.value[0]
    if (target) {
      event.preventDefault()
      choose(target)
    }
  }
}

function onBlur(): void {
  // 让点击结果行先完成；列表由点击/键盘负责关闭。
  window.setTimeout(() => {
    open.value = false
  }, 120)
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})
</script>

<template>
  <div class="cb-config-search" data-test="config-search">
    <div class="cb-config-search__box">
      <label class="cb-config-search__label" for="cb-config-search-input">搜索设置</label>
      <input
        id="cb-config-search-input"
        class="cb-config-search__input"
        type="search"
        role="combobox"
        aria-label="搜索设置"
        aria-autocomplete="list"
        :aria-expanded="open"
        aria-controls="cb-config-search-results"
        autocomplete="off"
        placeholder="按名称、说明或内部键搜索…"
        data-test="config-search-input"
        :value="keyword"
        @input="onInput(($event.target as HTMLInputElement).value)"
        @keydown="onKeydown"
        @blur="onBlur"
      />
    </div>

    <ul
      v-if="open && results.length > 0"
      id="cb-config-search-results"
      class="cb-config-search__results"
      role="listbox"
      aria-label="搜索结果"
      data-test="config-search-results"
    >
      <li
        v-for="(field, index) in results"
        :key="field.key"
        class="cb-config-search__result"
        :class="{ 'cb-config-search__result--active': index === activeIndex }"
        role="option"
        :aria-selected="index === activeIndex"
        data-test="config-search-result"
        @mousedown.prevent="choose(field)"
      >
        <span class="cb-config-search__name">{{ field.label }}</span>
        <span class="cb-config-search__meta">{{ field.area }}</span>
        <span class="cb-config-search__meta">{{ levelLabel(field) }}</span>
        <span class="cb-config-search__status">{{ statusLabel(field) }}</span>
        <code class="cb-config-search__key">{{ field.key }}</code>
      </li>
    </ul>

    <p v-else-if="showEmpty" class="cb-config-search__empty" data-test="config-search-empty" role="status">
      没有匹配的设置
    </p>
  </div>
</template>

<style scoped>
.cb-config-search {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  max-width: 640px;
}

.cb-config-search__box {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.cb-config-search__label {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-config-search__input {
  width: 100%;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-config-search__input:focus {
  border-color: var(--cb-primary);
  outline: none;
  box-shadow: var(--cb-focus);
}

.cb-config-search__results {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  z-index: 40;
  margin: var(--cb-space-1) 0 0;
  padding: var(--cb-space-1);
  list-style: none;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
}

.cb-config-search__result {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) auto auto auto;
  gap: var(--cb-space-2);
  align-items: baseline;
  padding: var(--cb-space-2) var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  cursor: pointer;
  font-size: var(--cb-text-sm);
}

.cb-config-search__result--active,
.cb-config-search__result:hover {
  background: var(--cb-primary-soft);
}

.cb-config-search__name {
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.cb-config-search__meta,
.cb-config-search__status {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-config-search__key {
  grid-column: 1 / -1;
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  overflow-wrap: anywhere;
}

.cb-config-search__empty {
  padding: var(--cb-space-3);
  border: 1px dashed var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}
</style>
