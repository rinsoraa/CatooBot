<script setup lang="ts">
/**
 * 重启待办横幅（W4 §52、§90）：只读 store 的 restartPending，挂载时补一次 GET。
 *
 * 挂在系统分区的每一页顶部；没有待重启项时什么都不渲染，
 * 也绝不提供「自动重启」之类的动作（§53）。
 */
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { useConfigStore } from '@/stores/config'

const store = useConfigStore()

const count = computed(() => store.restartPending.pending.length)
const keysText = computed(() => store.restartPending.pending.join('、'))

onMounted(() => {
  void store.loadRestartPending()
})
</script>

<template>
  <div v-if="count > 0" class="cb-restart-banner" data-test="restart-banner" role="status">
    <p class="cb-restart-banner__text">
      <span class="cb-restart-banner__icon" aria-hidden="true">⚠</span>
      {{ count }} 项设置将在重启后生效
    </p>
    <p class="cb-restart-banner__keys" :title="keysText">{{ keysText }}</p>
    <RouterLink
      class="cb-restart-banner__link"
      data-test="restart-banner-link"
      :to="{ name: 'system-restart-pending' }"
    >
      查看
    </RouterLink>
  </div>
</template>

<style scoped>
.cb-restart-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-3);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-md);
  background: var(--cb-warning-soft);
}

.cb-restart-banner__text {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-1);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-restart-banner__icon {
  color: var(--cb-warning);
}

.cb-restart-banner__keys {
  flex: 1;
  min-width: 0;
  color: var(--cb-text-muted);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cb-restart-banner__link {
  flex: none;
  font-size: var(--cb-text-sm);
}
</style>
