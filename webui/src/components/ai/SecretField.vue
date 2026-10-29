<script setup lang="ts">
/**
 * 密钥输入框（§85 密钥 UX）：只承载「用户刚输入的明文」，已保存的 Key 绝不回显。
 *
 * - 默认 `type="password"`；显示/隐藏按钮带 `aria-label`（显示 / 隐藏）。
 * - 已保存的凭据只以 `masked` 字符串作为 placeholder 出现，永不作为 value。
 * - 错误信息由父级用 `#error` 插槽传入，贴在字段旁（§134）。
 */
import { computed, ref, useId, useSlots } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: string
    label: string
    hint?: string
    configured?: boolean
    masked?: string
    placeholder?: string
    disabled?: boolean
  }>(),
  {
    hint: '',
    configured: false,
    masked: '',
    placeholder: '',
    disabled: false,
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const slots = useSlots()
const uid = useId()
const inputId = `cb-secret-${uid}`
const hintId = `cb-secret-hint-${uid}`
const errorId = `cb-secret-error-${uid}`

const revealed = ref(false)

const inputType = computed(() => (revealed.value ? 'text' : 'password'))
const toggleLabel = computed(() => (revealed.value ? '隐藏' : '显示'))
const hasError = computed(() => Boolean(slots.error))
const hasHint = computed(() => Boolean(props.hint))
/** 已保存的掩码只做 placeholder；用户开始输入后不再显示。 */
const placeholderText = computed(() => {
  if (props.placeholder) return props.placeholder
  if (props.masked) return props.masked
  return ''
})
const describedBy = computed(() => {
  const ids: string[] = []
  if (hasHint.value) ids.push(hintId)
  if (hasError.value) ids.push(errorId)
  return ids.length > 0 ? ids.join(' ') : undefined
})

function onInput(event: Event): void {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="secret-field" :data-configured="configured ? 'true' : undefined">
    <div class="secret-field__head">
      <label class="secret-field__label" :for="inputId">{{ label }}</label>
      <span v-if="configured" class="secret-field__flag">已配置</span>
    </div>

    <div class="secret-field__control">
      <input
        :id="inputId"
        class="secret-field__input"
        data-test="secret-input"
        :type="inputType"
        :value="modelValue"
        :placeholder="placeholderText"
        :disabled="disabled"
        :aria-describedby="describedBy"
        :aria-invalid="hasError ? 'true' : undefined"
        autocomplete="new-password"
        spellcheck="false"
        @input="onInput"
      />
      <button
        type="button"
        class="secret-field__toggle"
        data-test="secret-toggle"
        :disabled="disabled"
        :aria-label="toggleLabel"
        :aria-pressed="revealed ? 'true' : 'false'"
        @click="revealed = !revealed"
      >
        {{ toggleLabel }}
      </button>
    </div>

    <p v-if="hint" :id="hintId" class="secret-field__hint">{{ hint }}</p>
    <p v-if="hasError" :id="errorId" class="secret-field__error" role="alert">
      <slot name="error" />
    </p>
  </div>
</template>

<style scoped>
.secret-field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.secret-field__head {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.secret-field__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.secret-field__flag {
  font-size: var(--cb-text-xs);
  color: var(--cb-success);
}

.secret-field__control {
  display: flex;
  align-items: stretch;
  gap: var(--cb-space-1);
}

.secret-field__input {
  flex: 1;
  min-width: 0;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
}

.secret-field__input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.secret-field__toggle {
  flex: none;
  padding: 0 var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text-muted);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.secret-field__toggle:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.secret-field__toggle:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.secret-field__hint {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.secret-field__error {
  font-size: var(--cb-text-xs);
  color: var(--cb-danger);
}
</style>
