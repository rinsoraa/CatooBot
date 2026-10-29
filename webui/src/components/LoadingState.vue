<script setup lang="ts">
/** 加载态（§32）：骨架屏，不阻塞布局；用 aria-busy 告知辅助技术。 */
import { computed } from 'vue'

const props = withDefaults(defineProps<{ label?: string; rows?: number }>(), {
  label: '加载中…',
  rows: 3,
})

const barCount = computed(() => Math.max(1, Math.round(props.rows)))
</script>

<template>
  <div class="cb-loading" role="status" aria-busy="true">
    <p class="cb-loading__label">{{ label }}</p>
    <div class="cb-loading__bars" aria-hidden="true">
      <span v-for="row in barCount" :key="row" class="cb-loading__bar" />
    </div>
  </div>
</template>

<style scoped>
.cb-loading {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  width: 100%;
}

.cb-loading__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.cb-loading__bars {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
}

.cb-loading__bar {
  display: block;
  height: 12px;
  border-radius: var(--cb-radius-sm);
  background: var(--cb-border);
  animation: cb-loading-pulse 1.4s ease-in-out infinite;
}

.cb-loading__bar:nth-child(2n) {
  width: 82%;
}

.cb-loading__bar:nth-child(3n) {
  width: 64%;
}

@keyframes cb-loading-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}
</style>
