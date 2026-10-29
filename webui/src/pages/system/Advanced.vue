<script setup lang="ts">
/**
 * 高级 · 原始 YAML（W4 §54-§57、§72、§99-§100）。
 *
 * 直接编辑 overrides.yaml 原文：加载 → 修改 → 确认对话框（展示变更摘要 +
 * 「这会替换 overrides.yaml」）→ 才真正 PUT；后端校验失败时原样展示
 * 解析错误。本页不提供导出，也绝不显示任何 Secret / API Key。
 */
import { NInput } from 'naive-ui'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import { useConfigStore } from '@/stores/config'

const store = useConfigStore()

const text = ref('')
const original = ref('')
const path = ref('')
const loading = ref(true)
const saving = ref(false)
const loadError = ref('')
const saveError = ref('')
const showConfirm = ref(false)

const dirty = computed(() => text.value !== original.value)

const diff = computed(() => {
  const counts = (source: string): Map<string, number> => {
    const map = new Map<string, number>()
    for (const line of source.split('\n')) map.set(line, (map.get(line) ?? 0) + 1)
    return map
  }
  const before = counts(original.value)
  const after = counts(text.value)
  let added = 0
  let removed = 0
  for (const [line, count] of after) added += Math.max(0, count - (before.get(line) ?? 0))
  for (const [line, count] of before) removed += Math.max(0, count - (after.get(line) ?? 0))
  return { added, removed }
})

const confirmDetail = computed(
  () =>
    `这会替换 overrides.yaml（${path.value || '未知路径'}）：新增 ${diff.value.added} 行，删除 ${diff.value.removed} 行。` +
    '保存前会由后端校验 YAML，解析错误会原样返回。',
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  saveError.value = ''
  const raw = await store.loadRaw()
  if (raw) {
    text.value = raw.yaml
    original.value = raw.yaml
    path.value = raw.path
  } else {
    loadError.value = store.error || '无法读取 overrides.yaml'
  }
  loading.value = false
}

function requestSave(): void {
  if (saving.value || !dirty.value) return
  showConfirm.value = true
}

async function confirmSave(): Promise<void> {
  showConfirm.value = false
  saving.value = true
  const saved = await store.saveRaw(text.value)
  saving.value = false
  if (!saved) {
    saveError.value = store.error || '请检查 YAML 语法后重试'
    toast.error('保存失败', saveError.value)
    return
  }
  original.value = text.value
  toast.success('原始配置已保存', '正在重新读取生效值')
  await load()
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  void load()
  window.addEventListener('beforeunload', onBeforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})
</script>

<template>
  <div class="cb-advanced" data-test="advanced-page">
    <SectionHeader
      title="高级 · 原始 YAML"
      description="直接编辑 overrides.yaml；普通用户建议使用标准设置页面。"
    />

    <div class="cb-advanced__warning" data-test="advanced-warning" role="alert">
      <p class="cb-advanced__warning-title">
        <span aria-hidden="true">⚠</span> 专家模式
      </p>
      <p>
        专家模式：直接修改底层配置，错误配置可能导致 Bot 无法启动；普通用户建议使用标准设置页面。
      </p>
    </div>

    <p class="cb-advanced__note" data-test="advanced-secrets-note">
      ⓘ 本页只读取 overrides.yaml 原文：不会显示任何 Secret，也不会导出 API Key。
    </p>

    <ErrorState v-if="loadError" :message="loadError" @retry="load" />
    <LoadingState v-else-if="loading" label="正在读取 overrides.yaml…" :rows="5" />

    <template v-else>
      <ErrorState v-if="saveError" data-test="advanced-error" :message="saveError" />
      <p v-if="path" class="cb-advanced__path cb-faint" data-test="advanced-path">
        文件：{{ path }}
      </p>
      <NInput
        v-model:value="text"
        type="textarea"
        class="cb-advanced__editor"
        data-test="advanced-editor"
        :autosize="{ minRows: 16, maxRows: 40 }"
        :input-props="{ spellcheck: 'false', 'aria-label': 'overrides.yaml 原文' }"
      />
      <div class="cb-advanced__actions">
        <button
          type="button"
          class="cb-advanced__button"
          data-test="advanced-reload"
          :disabled="saving"
          @click="load"
        >
          重新加载
        </button>
        <button
          type="button"
          class="cb-advanced__button cb-advanced__button--primary"
          data-test="advanced-save"
          :disabled="saving || !dirty"
          @click="requestSave"
        >
          {{ saving ? '保存中…' : '保存原始 YAML' }}
        </button>
      </div>
      <p v-if="!dirty" class="cb-advanced__hint cb-faint" data-test="advanced-hint">
        与加载时一致，没有需要保存的修改。
      </p>
    </template>

    <ConfirmDialog
      :show="showConfirm"
      title="确认替换 overrides.yaml"
      message="这会替换 overrides.yaml，请确认内容无误。"
      :detail="confirmDetail"
      confirm-text="确认替换"
      danger
      @confirm="confirmSave"
      @cancel="showConfirm = false"
    />
  </div>
</template>

<style scoped>
.cb-advanced {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-advanced__warning {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-md);
  background: var(--cb-danger-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-advanced__warning-title {
  color: var(--cb-danger);
}

.cb-advanced__note {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-advanced__path {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-advanced__editor :deep(textarea) {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
}

.cb-advanced__actions {
  display: flex;
  gap: var(--cb-space-2);
}

.cb-advanced__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-advanced__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-advanced__button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.cb-advanced__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary);
  color: var(--cb-bg);
}

.cb-advanced__button--primary:hover:not(:disabled) {
  background: var(--cb-primary-strong);
  color: var(--cb-bg);
}

.cb-advanced__hint {
  font-size: var(--cb-text-xs);
}
</style>
