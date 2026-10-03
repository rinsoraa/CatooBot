<script setup lang="ts">
/**
 * 配置生效状态徽标（W4 §44-§46、§91-§92）：图标 + 文字，永不只靠颜色。
 *
 * 状态机（优先级从高到低）：
 *   当前未使用（DEFINED_BUT_UNUSED / LEGACY）→ 条件生效 → 重启后生效 → 未保存 → 已生效
 * 草稿未保存时额外挂一个 `● 未保存` 标记；`pendingRestart` 表示该键已在
 * overrides.yaml 里保存、但运行期还没拿到新值。
 */
import { computed } from 'vue'

import type { ConfigFieldMeta, EffectiveField } from '@/types/config'

const props = withDefaults(
  defineProps<{
    field: ConfigFieldMeta
    effective?: EffectiveField | null
    dirty?: boolean
    pendingRestart?: boolean
  }>(),
  { effective: null, dirty: false, pendingRestart: false },
)

type StatusKind = 'effective' | 'restart' | 'unused' | 'conditional' | 'unsaved'

const usageStatus = computed(() => props.effective?.usage_status ?? props.field.usage_status)

const status = computed<StatusKind>(() => {
  const usage = usageStatus.value
  if (usage === 'DEFINED_BUT_UNUSED' || usage === 'LEGACY') return 'unused'
  if (usage === 'CONDITIONALLY_USED') return 'conditional'
  if (props.pendingRestart || props.field.restart_required || props.effective?.restart_required) {
    return 'restart'
  }
  if (props.dirty) return 'unsaved'
  return 'effective'
})

const TEXT: Record<StatusKind, string> = {
  effective: '已生效',
  restart: '重启后生效',
  unused: '当前未使用',
  conditional: '条件生效',
  unsaved: '未保存',
}

const ICON: Record<StatusKind, string> = {
  effective: '✓',
  restart: '⚠',
  unused: '⚠',
  conditional: 'ⓘ',
  unsaved: '●',
}

const HINT: Record<StatusKind, string> = {
  effective: '保存后立即生效',
  restart: '保存后需要重启 CatooBot 才会生效',
  unused: '该配置项当前没有被 Runtime 使用，修改它不会改变 Bot 行为',
  conditional: '该配置项仅在相关开关打开时生效',
  unsaved: '修改尚未保存',
}

const showDirtyMarker = computed(() => props.dirty && status.value !== 'unsaved')
</script>

<template>
  <span
    class="cb-config-status"
    :class="`cb-config-status--${status}`"
    :data-status="status"
    data-test="config-status-badge"
    :title="HINT[status]"
  >
    <span class="cb-config-status__main">
      <span class="cb-config-status__icon" aria-hidden="true">{{ ICON[status] }}</span>
      <span class="cb-config-status__text">{{ TEXT[status] }}</span>
    </span>
    <span
      v-if="showDirtyMarker"
      class="cb-config-status__dirty"
      data-test="config-status-dirty"
      title="修改尚未保存"
    >
      <span aria-hidden="true">●</span> 未保存
    </span>
  </span>
</template>

<style scoped>
.cb-config-status {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-2);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-config-status__main {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border);
  border-radius: 999px;
  line-height: 1.6;
}

.cb-config-status--effective {
  border-color: transparent;
}
.cb-config-status--effective .cb-config-status__main {
  border-color: var(--cb-success);
  background: var(--cb-success-soft);
  color: var(--cb-success);
}

.cb-config-status--restart .cb-config-status__main {
  border-color: var(--cb-warning);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
}

.cb-config-status--unused .cb-config-status__main {
  border-color: var(--cb-border-strong);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
}

.cb-config-status--conditional .cb-config-status__main {
  border-color: var(--cb-info);
  background: var(--cb-info-soft);
  color: var(--cb-info);
}

.cb-config-status--unsaved .cb-config-status__main {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.cb-config-status__dirty {
  color: var(--cb-primary-strong);
}
</style>
