<script setup lang="ts">
/** 错误态（§32）：消息永远可见；重试按钮只在父级监听 retry 时渲染。 */
import { computed, getCurrentInstance } from 'vue'

withDefaults(defineProps<{ message: string; detail?: string; retryLabel?: string }>(), {
  detail: '',
  retryLabel: '重试',
})

const emit = defineEmits<{ retry: [] }>()

const instance = getCurrentInstance()
const hasRetry = computed(() => Boolean(instance?.vnode.props?.onRetry))
</script>

<template>
  <div class="cb-error" role="alert">
    <div class="cb-error__body">
      <p class="cb-error__message">{{ message }}</p>
      <p v-if="detail" class="cb-error__detail">{{ detail }}</p>
    </div>
    <button
      v-if="hasRetry"
      type="button"
      class="cb-error__retry"
      data-test="retry"
      @click="emit('retry')"
    >
      {{ retryLabel }}
    </button>
  </div>
</template>

<style scoped>
.cb-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-4);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-md);
  background: var(--cb-danger-soft);
}

.cb-error__message {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.cb-error__detail {
  margin-top: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  overflow-wrap: anywhere;
}

.cb-error__retry {
  flex: none;
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  font-family: inherit;
  cursor: pointer;
}

.cb-error__retry:hover {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}
</style>
