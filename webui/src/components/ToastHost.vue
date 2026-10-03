<script setup lang="ts">
/** Toast 宿主：唯一的通知出口，带 aria-live（§37）。 */
import { onMounted, onUnmounted } from 'vue'

import { useToast } from '@/composables/toast'

const { items, dismiss } = useToast()
const timers = new Map<number, number>()

function schedule(): void {
  for (const item of items.value) {
    if (timers.has(item.id)) continue
    const handle = window.setTimeout(() => {
      timers.delete(item.id)
      dismiss(item.id)
    }, item.timeout)
    timers.set(item.id, handle)
  }
}

let interval = 0
onMounted(() => {
  schedule()
  interval = window.setInterval(schedule, 500)
})
onUnmounted(() => {
  if (interval) window.clearInterval(interval)
  for (const handle of timers.values()) window.clearTimeout(handle)
  timers.clear()
})
</script>

<template>
  <div class="toast-host" role="status" aria-live="polite">
    <TransitionGroup name="toast">
      <div
        v-for="item in items"
        :key="item.id"
        class="toast"
        :class="`toast--${item.kind}`"
        :data-kind="item.kind"
      >
        <div class="toast__body">
          <p class="toast__message">{{ item.message }}</p>
          <p v-if="item.detail" class="toast__detail">{{ item.detail }}</p>
        </div>
        <button class="toast__close" type="button" aria-label="关闭通知" @click="dismiss(item.id)">
          ×
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  right: var(--cb-space-5);
  bottom: var(--cb-space-5);
  z-index: 4000;
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  max-width: min(380px, 90vw);
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3) var(--cb-space-4);
  border-radius: var(--cb-radius-md);
  border: 1px solid var(--cb-border);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
  border-left-width: 3px;
}

.toast--success {
  border-left-color: var(--cb-success);
}
.toast--info {
  border-left-color: var(--cb-info);
}
.toast--warning {
  border-left-color: var(--cb-warning);
}
.toast--error {
  border-left-color: var(--cb-danger);
}

.toast__body {
  flex: 1;
  min-width: 0;
}

.toast__message {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
  word-break: break-word;
}

.toast__detail {
  margin-top: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  word-break: break-word;
}

.toast__close {
  border: 0;
  background: transparent;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-lg);
  line-height: 1;
  cursor: pointer;
  padding: 0 var(--cb-space-1);
}

.toast__close:hover {
  color: var(--cb-text);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
