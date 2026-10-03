<script lang="ts">
/** 五个语义状态（§21）；MetricCard / StatusBadge 共用。 */
export type StatusState = 'ok' | 'warn' | 'error' | 'off' | 'idle'
</script>

<script setup lang="ts">
/** 状态徽标（§31）：文字标签永远渲染，颜色不是唯一信号（§37）。 */
withDefaults(defineProps<{ state: StatusState; label: string; pulse?: boolean }>(), {
  pulse: false,
})
</script>

<template>
  <span
    class="cb-status"
    :class="[`cb-status--${state}`, { 'cb-status--pulse': pulse }]"
    :data-state="state"
    role="status"
  >
    <span class="cb-status__dot" aria-hidden="true" />
    <span class="cb-status__label">{{ label }}</span>
  </span>
</template>

<style scoped>
.cb-status {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  padding: 2px var(--cb-space-2);
  border: 1px solid var(--cb-border);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  font-size: var(--cb-text-xs);
  line-height: 1.5;
  white-space: nowrap;
}

.cb-status__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--cb-text-faint);
  flex: none;
}

.cb-status__label {
  color: var(--cb-text-muted);
}

.cb-status--ok {
  border-color: var(--cb-success);
  background: var(--cb-success-soft);
}
.cb-status--ok .cb-status__dot {
  background: var(--cb-success);
}
.cb-status--ok .cb-status__label {
  color: var(--cb-success);
}

.cb-status--warn {
  border-color: var(--cb-warning);
  background: var(--cb-warning-soft);
}
.cb-status--warn .cb-status__dot {
  background: var(--cb-warning);
}
.cb-status--warn .cb-status__label {
  color: var(--cb-warning);
}

.cb-status--error {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
}
.cb-status--error .cb-status__dot {
  background: var(--cb-danger);
}
.cb-status--error .cb-status__label {
  color: var(--cb-danger);
}

.cb-status--off .cb-status__dot {
  background: var(--cb-text-faint);
}
.cb-status--off .cb-status__label {
  color: var(--cb-text-faint);
}

.cb-status--idle {
  border-color: var(--cb-border-strong);
}
.cb-status--idle .cb-status__dot {
  background: var(--cb-info);
}
.cb-status--idle .cb-status__label {
  color: var(--cb-text-muted);
}

.cb-status--pulse .cb-status__dot {
  animation: cb-status-pulse 1.6s ease-in-out infinite;
}

@keyframes cb-status-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}
</style>
