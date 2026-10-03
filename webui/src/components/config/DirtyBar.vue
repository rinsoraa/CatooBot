<script setup lang="ts">
/**
 * 批量未保存提示条（W4 §48、§135）：吸底显示未保存数量 + 取消 / 预览 / 应用。
 *
 * `applying` 时按钮禁用并显示「保存中…」，避免重复提交；组件不做任何
 * 网络请求，落到 store 的动作由页面负责。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    count: number
    applying?: boolean
    dirtyKeys: string[]
  }>(),
  { applying: false },
)

const emit = defineEmits<{
  discard: []
  apply: []
  preview: []
}>()

const summary = computed(() => `有 ${props.count} 项未保存修改`)
const keysText = computed(() => props.dirtyKeys.join('、'))
</script>

<template>
  <div class="cb-dirty-bar" data-test="dirty-bar" role="status" aria-live="polite">
    <div class="cb-dirty-bar__text">
      <p class="cb-dirty-bar__count" data-test="dirty-count">{{ summary }}</p>
      <p v-if="keysText" class="cb-dirty-bar__keys" :title="keysText">{{ keysText }}</p>
    </div>
    <div class="cb-dirty-bar__actions">
      <button
        type="button"
        class="cb-dirty-bar__button"
        data-test="dirty-discard"
        :disabled="applying"
        @click="emit('discard')"
      >
        取消
      </button>
      <button
        type="button"
        class="cb-dirty-bar__button"
        data-test="dirty-preview"
        :disabled="applying"
        @click="emit('preview')"
      >
        预览
      </button>
      <button
        type="button"
        class="cb-dirty-bar__button cb-dirty-bar__button--primary"
        data-test="dirty-apply"
        :disabled="applying"
        @click="emit('apply')"
      >
        {{ applying ? '保存中…' : '应用修改' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.cb-dirty-bar {
  position: sticky;
  bottom: var(--cb-space-3);
  z-index: 20;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-primary);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
}

.cb-dirty-bar__text {
  min-width: 0;
}

.cb-dirty-bar__count {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-dirty-bar__keys {
  margin-top: 2px;
  color: var(--cb-text-faint);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 60ch;
}

.cb-dirty-bar__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex: none;
}

.cb-dirty-bar__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-dirty-bar__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-dirty-bar__button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.cb-dirty-bar__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary);
  color: var(--cb-bg);
}

.cb-dirty-bar__button--primary:hover:not(:disabled) {
  background: var(--cb-primary-strong);
  color: var(--cb-bg);
}
</style>
