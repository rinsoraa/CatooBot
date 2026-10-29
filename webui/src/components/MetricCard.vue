<script setup lang="ts">
/**
 * 指标卡（§31）：空值显示「—」而不是假数字；加载态用骨架条（§37 aria-busy）。
 */
import { computed } from 'vue'

import type { StatusState } from '@/components/StatusBadge.vue'

const props = withDefaults(
  defineProps<{
    label: string
    value?: string | number | null
    hint?: string
    state?: StatusState
    loading?: boolean
  }>(),
  { value: null, hint: '', state: 'idle', loading: false },
)

const display = computed(() => {
  if (props.value === null || props.value === undefined) return '—'
  return String(props.value)
})

const isEmpty = computed(() => props.value === null || props.value === undefined)
</script>

<template>
  <div
    class="cb-metric cb-card"
    :class="`cb-metric--${state}`"
    :data-state="state"
    :aria-busy="loading ? 'true' : undefined"
  >
    <div class="cb-metric__head">
      <p class="cb-metric__label">{{ label }}</p>
      <div v-if="$slots.actions" class="cb-metric__actions">
        <slot name="actions" />
      </div>
    </div>

    <p v-if="loading" class="cb-metric__skeleton" aria-hidden="true">
      <span class="cb-metric__bar" />
      <span class="cb-visually-hidden">加载中…</span>
    </p>
    <p v-else class="cb-metric__value" :data-empty="isEmpty ? 'true' : undefined">
      {{ display }}
    </p>

    <p v-if="hint" class="cb-metric__hint">{{ hint }}</p>

    <div v-if="$slots.footer" class="cb-metric__footer">
      <slot name="footer" />
    </div>
  </div>
</template>

<style scoped>
.cb-metric {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-metric__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-2);
  min-height: 24px;
}

.cb-metric__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.cb-metric__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-1);
  flex: none;
}

.cb-metric__value {
  font-size: var(--cb-text-2xl);
  font-weight: 600;
  color: var(--cb-text);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.cb-metric__value[data-empty='true'] {
  color: var(--cb-text-faint);
}

.cb-metric--ok .cb-metric__value {
  color: var(--cb-success);
}
.cb-metric--warn .cb-metric__value {
  color: var(--cb-warning);
}
.cb-metric--error .cb-metric__value {
  color: var(--cb-danger);
}

.cb-metric__hint {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.cb-metric__skeleton {
  display: flex;
  align-items: center;
  min-height: 34px;
}

.cb-metric__bar {
  display: block;
  width: 96px;
  height: 18px;
  border-radius: var(--cb-radius-sm);
  background: var(--cb-border);
  animation: cb-metric-shimmer 1.4s ease-in-out infinite;
}

.cb-metric__footer {
  margin-top: auto;
  padding-top: var(--cb-space-2);
  border-top: 1px solid var(--cb-border);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

@keyframes cb-metric-shimmer {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}
</style>
