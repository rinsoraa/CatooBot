<script setup lang="ts">
/**
 * 配置来源徽标（W4 §42-§44）：env / overrides / models / yaml / default。
 *
 * 图标 + 文字，绝不只靠颜色；点击展开一行明细（来源文件 + 内部键），
 * 让用户知道「这个值是从哪儿来的」。
 */
import { computed, ref, useId } from 'vue'

import { SOURCE_LABELS } from '@/types/config'

const props = withDefaults(
  defineProps<{
    source?: string
    fieldKey: string
  }>(),
  { source: 'default' },
)

/** 来源 → 真正承载它的文件/位置（与后端 source_map 的层级一一对应）。 */
const SOURCE_FILES: Record<string, string> = {
  env: '.env',
  overrides: 'overrides.yaml',
  models: 'config.yaml 的 models 段',
  yaml: 'config.yaml',
  default: '内置默认值',
}

const resolved = computed(() => props.source || 'default')
const label = computed(() => SOURCE_LABELS[resolved.value] ?? resolved.value)
const file = computed(() => SOURCE_FILES[resolved.value] ?? resolved.value)

const expanded = ref(false)
const detailId = `cb-source-detail-${useId()}`
</script>

<template>
  <span class="cb-source-badge">
    <button
      type="button"
      class="cb-source-badge__button"
      data-test="config-source-badge"
      :aria-expanded="expanded"
      :aria-controls="detailId"
      :title="`来源：${file}`"
      @click="expanded = !expanded"
    >
      <span class="cb-source-badge__icon" aria-hidden="true">◆</span>
      <span class="cb-source-badge__label">{{ label }}</span>
    </button>
    <span v-if="expanded" :id="detailId" class="cb-source-badge__detail" data-test="config-source-detail">
      来源文件：{{ file }} · 内部键：{{ fieldKey }}
    </span>
  </span>
</template>

<style scoped>
.cb-source-badge {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-1);
  min-width: 0;
}

.cb-source-badge__button {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  line-height: 1.6;
  cursor: pointer;
  white-space: nowrap;
}

.cb-source-badge__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-source-badge__icon {
  color: var(--cb-info);
  font-size: var(--cb-text-xs);
}

.cb-source-badge__detail {
  flex-basis: 100%;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
  overflow-wrap: anywhere;
}
</style>
