<script setup lang="ts">
/**
 * 动态配置字段渲染器（W4 §47、§86、§93）。
 *
 * 控件完全由 `field.type` + `field.constraints` + `field.sensitive` 决定：
 *   bool → Switch；int/float → InputNumber（显示取值范围）；
 *   choices → Select；list → 可增删行编辑器；dict/obj → JSON 文本域；
 *   长文本 → Textarea；其余 str → Input；sensitive → 掩码只读 + 说明。
 *
 * 敏感项在后端是只读的（`config.readonly_key`），所以这里直接禁用并
 * 告诉用户去凭据页改；绝不把明文渲染出来。
 */
import { NInput, NInputNumber, NSelect, NSwitch } from 'naive-ui'
import { computed, ref, watch } from 'vue'

import type { ConfigFieldMeta } from '@/types/config'

const props = withDefaults(
  defineProps<{
    field: ConfigFieldMeta
    value: unknown
    disabled?: boolean
    disabledReason?: string
  }>(),
  { disabled: false, disabledReason: '' },
)

const emit = defineEmits<{ 'update:value': [value: unknown] }>()

type WidgetKind = 'bool' | 'number' | 'select' | 'list' | 'dict' | 'textarea' | 'text' | 'sensitive'

const constraints = computed(() => props.field.constraints ?? {})

function isLongText(field: ConfigFieldMeta): boolean {
  if (field.type !== 'str') return false
  const max = field.constraints?.max_length
  if (typeof max === 'number' && max >= 256) return true
  return typeof field.default === 'string' && field.default.includes('\n')
}

const kind = computed<WidgetKind>(() => {
  if (props.field.sensitive) return 'sensitive'
  const type = props.field.type
  if (type === 'bool') return 'bool'
  if (type === 'int' || type === 'float') return 'number'
  if (type === 'list') return 'list'
  if (type === 'dict' || type === 'obj') return 'dict'
  if ((props.field.choices?.length ?? 0) > 0) return 'select'
  if (isLongText(props.field)) return 'textarea'
  return 'text'
})

const isDisabled = computed(() => props.disabled || props.field.sensitive)

const ariaLabel = computed(() => props.field.label || props.field.key)

const inputProps = computed(() => ({ 'aria-label': ariaLabel.value }))

/** 约束文本（§93）：范围 / 长度 / 格式，原样展示给用户。 */
const constraintText = computed(() => {
  const c = constraints.value
  const parts: string[] = []
  if (c.min !== undefined || c.max !== undefined) {
    parts.push(`取值范围 ${c.min ?? '不限'} ~ ${c.max ?? '不限'}`)
  }
  if (c.exclusive_min !== undefined) parts.push(`必须大于 ${c.exclusive_min}`)
  if (c.exclusive_max !== undefined) parts.push(`必须小于 ${c.exclusive_max}`)
  if (c.min_length !== undefined || c.max_length !== undefined) {
    parts.push(`长度 ${c.min_length ?? '不限'} ~ ${c.max_length ?? '不限'}`)
  }
  if (c.pattern) parts.push(`格式：${c.pattern}`)
  return parts.join('；')
})

// ------------------------------------------------------------------ bool
const boolValue = computed(() => props.value === true)

function onBool(value: boolean): void {
  if (isDisabled.value) return
  emit('update:value', value)
}

// ---------------------------------------------------------------- number
const numberValue = computed<number | null>(() => {
  if (typeof props.value === 'number') return props.value
  if (typeof props.value === 'string' && props.value.trim() !== '') {
    const parsed = Number(props.value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
})

function onNumber(value: number | null): void {
  if (isDisabled.value || value === null) return
  emit('update:value', props.field.type === 'int' ? Math.round(value) : value)
}

// ---------------------------------------------------------------- select
const selectOptions = computed(() =>
  (props.field.choices ?? []).map((choice) => ({ label: choice, value: choice })),
)

const selectValue = computed<string | null>(() =>
  typeof props.value === 'string' ? props.value : null,
)

function onSelect(value: string | null): void {
  if (isDisabled.value || value === null) return
  emit('update:value', value)
}

// ---------------------------------------------------------------- string
const textValue = computed(() => (props.value == null ? '' : String(props.value)))

function onText(value: string): void {
  if (isDisabled.value) return
  emit('update:value', value)
}

// ------------------------------------------------------------------ list
function toRows(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
}

const rows = ref<string[]>(toRows(props.value))

watch(
  () => props.value,
  (value) => {
    const next = toRows(value)
    if (JSON.stringify(next) !== JSON.stringify(rows.value)) rows.value = next
  },
  { immediate: true, deep: true },
)

function emitRows(): void {
  emit('update:value', [...rows.value])
}

function updateRow(index: number, text: string): void {
  if (isDisabled.value) return
  rows.value = rows.value.map((row, position) => (position === index ? text : row))
  emitRows()
}

function addRow(): void {
  if (isDisabled.value) return
  rows.value = [...rows.value, '']
  emitRows()
}

function removeRow(index: number): void {
  if (isDisabled.value) return
  rows.value = rows.value.filter((_row, position) => position !== index)
  emitRows()
}

// ------------------------------------------------------------------ dict
function formatJson(value: unknown): string {
  if (value === undefined || value === null) return ''
  try {
    return JSON.stringify(value, null, 2) ?? ''
  } catch {
    return ''
  }
}

function parseJson(text: string): { ok: true; value: unknown } | { ok: false } {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: {} }
  try {
    return { ok: true, value: JSON.parse(trimmed) }
  } catch {
    return { ok: false }
  }
}

const jsonText = ref(formatJson(props.value))
const jsonError = ref('')

watch(
  () => props.value,
  (value) => {
    const current = parseJson(jsonText.value)
    const matches =
      current.ok && JSON.stringify(current.value) === JSON.stringify(value ?? {})
    if (!matches) {
      jsonText.value = formatJson(value)
      jsonError.value = ''
    }
  },
  { immediate: true, deep: true },
)

function onJson(value: string): void {
  jsonText.value = value
  const parsed = parseJson(value)
  if (!parsed.ok) {
    jsonError.value = 'JSON 格式无效，请检查括号、引号与逗号'
    return
  }
  jsonError.value = ''
  if (!isDisabled.value) emit('update:value', parsed.value)
}

// -------------------------------------------------------------- sensitive
const revealed = ref(false)

const sensitiveText = computed(() => {
  if (props.value == null) return ''
  return String(props.value)
})
</script>

<template>
  <div class="cb-config-field" :data-key="field.key" data-test="config-field" role="group" :aria-label="ariaLabel">
    <div class="cb-config-field__head">
      <span class="cb-config-field__label">{{ field.label }}</span>
      <code class="cb-config-field__key">{{ field.key }}</code>
    </div>

    <!-- bool -->
    <div v-if="kind === 'bool'" class="cb-config-field__control" data-test="field-bool">
      <NSwitch
        :value="boolValue"
        :disabled="isDisabled"
        :aria-label="ariaLabel"
        data-test="field-bool-switch"
        @update:value="onBool"
      />
      <span class="cb-config-field__bool-text">{{ boolValue ? '已开启' : '已关闭' }}</span>
    </div>

    <!-- int / float -->
    <div v-else-if="kind === 'number'" class="cb-config-field__control" data-test="field-number">
      <NInputNumber
        :value="numberValue"
        :disabled="isDisabled"
        :min="constraints.min"
        :max="constraints.max"
        :step="field.type === 'float' ? 0.1 : 1"
        :input-props="inputProps"
        data-test="field-number-input"
        @update:value="onNumber"
      />
      <span v-if="constraintText" class="cb-config-field__constraints" data-test="field-constraints">
        {{ constraintText }}
      </span>
    </div>

    <!-- str with choices -->
    <div v-else-if="kind === 'select'" class="cb-config-field__control" data-test="field-select">
      <NSelect
        :value="selectValue"
        :options="selectOptions"
        :disabled="isDisabled"
        :aria-label="ariaLabel"
        data-test="field-select-input"
        @update:value="onSelect"
      />
    </div>

    <!-- list -->
    <div v-else-if="kind === 'list'" class="cb-config-field__control" data-test="field-list">
      <div v-for="(row, index) in rows" :key="index" class="cb-config-field__list-row" data-test="field-list-row">
        <NInput
          :value="row"
          :disabled="isDisabled"
          :input-props="inputProps"
          @update:value="(value: string) => updateRow(index, value)"
        />
        <button
          type="button"
          class="cb-config-field__row-button"
          data-test="field-list-remove"
          :disabled="isDisabled"
          :aria-label="`删除第 ${index + 1} 项`"
          @click="removeRow(index)"
        >
          删除
        </button>
      </div>
      <button
        type="button"
        class="cb-config-field__row-button"
        data-test="field-list-add"
        :disabled="isDisabled"
        @click="addRow"
      >
        添加一项
      </button>
      <p v-if="constraintText" class="cb-config-field__constraints">{{ constraintText }}</p>
    </div>

    <!-- dict / obj -->
    <div v-else-if="kind === 'dict'" class="cb-config-field__control cb-config-field__control--block" data-test="field-dict">
      <NInput
        type="textarea"
        :value="jsonText"
        :disabled="isDisabled"
        :autosize="{ minRows: 3, maxRows: 10 }"
        :input-props="{ spellcheck: 'false', 'aria-label': `${ariaLabel}（JSON）` }"
        class="cb-config-field__json"
        data-test="field-dict-input"
        @update:value="onJson"
      />
      <p v-if="jsonError" class="cb-config-field__error" data-test="field-dict-error" role="alert">
        {{ jsonError }}
      </p>
    </div>

    <!-- long str -->
    <div v-else-if="kind === 'textarea'" class="cb-config-field__control cb-config-field__control--block" data-test="field-textarea">
      <NInput
        type="textarea"
        :value="textValue"
        :disabled="isDisabled"
        :autosize="{ minRows: 3, maxRows: 12 }"
        :input-props="{ 'aria-label': ariaLabel }"
        @update:value="onText"
      />
    </div>

    <!-- sensitive -->
    <div v-else-if="kind === 'sensitive'" class="cb-config-field__control" data-test="field-sensitive">
      <NInput
        :type="revealed ? 'text' : 'password'"
        :value="sensitiveText"
        disabled
        :input-props="{ 'aria-label': ariaLabel, autocomplete: 'off' }"
        data-test="field-sensitive-input"
      />
      <button
        type="button"
        class="cb-config-field__row-button"
        data-test="field-sensitive-toggle"
        :aria-label="revealed ? `隐藏 ${field.label}` : `显示 ${field.label}`"
        @click="revealed = !revealed"
      >
        {{ revealed ? '隐藏' : '显示' }}
      </button>
      <p class="cb-config-field__note" data-test="field-sensitive-note">
        敏感项请在凭据页或 Provider 编辑中修改
      </p>
    </div>

    <!-- other str -->
    <div v-else class="cb-config-field__control" data-test="field-text">
      <NInput
        :value="textValue"
        :disabled="isDisabled"
        :input-props="inputProps"
        @update:value="onText"
      />
      <span v-if="constraintText" class="cb-config-field__constraints">{{ constraintText }}</span>
    </div>

    <p class="cb-config-field__description">{{ field.description }}</p>
    <p v-if="isDisabled && disabledReason" class="cb-config-field__disabled" data-test="field-disabled-reason">
      {{ disabledReason }}
    </p>
  </div>
</template>

<style scoped>
.cb-config-field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3) 0;
  border-bottom: 1px solid var(--cb-border);
}

.cb-config-field:last-child {
  border-bottom: none;
}

.cb-config-field__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--cb-space-2);
}

.cb-config-field__label {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
}

.cb-config-field__key {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  overflow-wrap: anywhere;
}

.cb-config-field__control {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-2);
  max-width: 560px;
}

.cb-config-field__control--block {
  display: block;
  width: 100%;
  max-width: 720px;
}

.cb-config-field__bool-text {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-config-field__constraints {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-config-field__description {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  max-width: 72ch;
}

.cb-config-field__row-button {
  flex: none;
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-config-field__row-button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-config-field__row-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-config-field__list-row {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  margin-bottom: var(--cb-space-2);
}

.cb-config-field__list-row :deep(.n-input) {
  flex: 1;
}

.cb-config-field__json :deep(textarea),
.cb-config-field__control--block :deep(textarea) {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
}

.cb-config-field__error {
  margin-top: var(--cb-space-1);
  color: var(--cb-danger);
  font-size: var(--cb-text-xs);
}

.cb-config-field__note {
  flex-basis: 100%;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-config-field__disabled {
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}
</style>
