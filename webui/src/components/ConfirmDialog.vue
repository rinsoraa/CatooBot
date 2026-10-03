<script setup lang="ts">
/**
 * 确认对话框（§32）：纯 HTML 覆盖层，不依赖 naive-ui。
 *
 * 供后续阶段的破坏性操作使用：Esc = 取消、打开时焦点进入对话框、
 * 关闭时焦点回到打开前的元素；`v-model:show` 与 `v-model` 都可用。
 */
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'

const props = defineProps<{
  modelValue?: boolean
  show?: boolean
  title: string
  message: string
  detail?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  'update:show': [value: boolean]
  confirm: []
  cancel: []
}>()

const visible = computed(() => Boolean(props.show) || Boolean(props.modelValue))
const confirmLabel = computed(() => props.confirmText ?? '确认')
const cancelLabel = computed(() => props.cancelText ?? '取消')

const uid = useId()
const titleId = `cb-dialog-title-${uid}`
const messageId = `cb-dialog-message-${uid}`
const panel = ref<HTMLElement | null>(null)
let restoreFocus: HTMLElement | null = null

function close(): void {
  emit('update:modelValue', false)
  emit('update:show', false)
}

function confirm(): void {
  close()
  emit('confirm')
}

function cancel(): void {
  close()
  emit('cancel')
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return
  event.stopPropagation()
  cancel()
}

watch(
  visible,
  (open) => {
    if (open) {
      restoreFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      void nextTick(() => panel.value?.focus())
      document.addEventListener('keydown', onKeydown, true)
    } else {
      document.removeEventListener('keydown', onKeydown, true)
      restoreFocus?.focus()
      restoreFocus = null
    }
  },
  { immediate: true, flush: 'post' },
)

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown, true))
</script>

<template>
  <div v-if="visible" class="cb-dialog">
    <div class="cb-dialog__backdrop" data-test="backdrop" aria-hidden="true" @click="cancel" />
    <div
      ref="panel"
      class="cb-dialog__panel"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="messageId"
      tabindex="-1"
    >
      <h2 :id="titleId" class="cb-dialog__title">{{ title }}</h2>
      <p :id="messageId" class="cb-dialog__message">{{ message }}</p>
      <p v-if="detail" class="cb-dialog__detail">{{ detail }}</p>
      <div class="cb-dialog__actions">
        <button
          type="button"
          class="cb-dialog__button"
          data-test="cancel"
          @click="cancel"
        >
          {{ cancelLabel }}
        </button>
        <button
          type="button"
          class="cb-dialog__button"
          :class="{ 'cb-dialog__button--danger': danger }"
          data-test="confirm"
          @click="confirm"
        >
          {{ confirmLabel }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cb-dialog {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: grid;
  place-items: center;
  padding: var(--cb-space-4);
}

.cb-dialog__backdrop {
  position: absolute;
  inset: 0;
  background: var(--cb-bg);
  opacity: 0.72;
}

.cb-dialog__panel {
  position: relative;
  width: min(420px, 100%);
  padding: var(--cb-space-5);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-lg);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
}

.cb-dialog__panel:focus {
  outline: none;
}

.cb-dialog__title {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
}

.cb-dialog__message {
  margin-top: var(--cb-space-3);
  font-size: var(--cb-text-md);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.cb-dialog__detail {
  margin-top: var(--cb-space-2);
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  overflow-wrap: anywhere;
}

.cb-dialog__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cb-space-2);
  margin-top: var(--cb-space-5);
}

.cb-dialog__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  font-family: inherit;
  cursor: pointer;
}

.cb-dialog__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-dialog__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-dialog__button--danger:hover {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}
</style>
