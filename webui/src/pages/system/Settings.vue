<script setup lang="ts">
/**
 * 配置中心（W4 §38-§49、§50-§53、§72-§73、§86-§96、§117、§133-§137）。
 *
 * 只读 schema（元数据）+ effective（真正生效的值），编辑只进内存草稿
 * （config store）；「应用」必须经 校验 → 预览 → 确认 → PATCH → toast，
 * 并且**绝不自动重启**：需要重启的键只提示「重启后生效」。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ConfigField from '@/components/config/ConfigField.vue'
import ConfigSearch from '@/components/config/ConfigSearch.vue'
import ConfigSection from '@/components/config/ConfigSection.vue'
import ConfigSourceBadge from '@/components/config/ConfigSourceBadge.vue'
import ConfigStatusBadge from '@/components/config/ConfigStatusBadge.vue'
import DirtyBar from '@/components/config/DirtyBar.vue'
import { toast } from '@/composables/toast'
import { useConfigStore } from '@/stores/config'
import type { ConfigFieldMeta, ConfigLevel } from '@/types/config'

const store = useConfigStore()
const route = useRoute()

const LEVELS: ConfigLevel[] = ['basic', 'advanced', 'expert']
const LEVEL_LABELS: Record<ConfigLevel, string> = { basic: '基础', advanced: '高级', expert: '专家' }
const LEVEL_ORDER: Record<ConfigLevel, number> = { basic: 0, advanced: 1, expert: 2 }

const focusKey = ref('')
const keptCount = ref(0)
const showPreview = ref(false)
const previewHot = ref(0)
const previewRestart = ref(0)
const previewLines = ref<string[]>([])

const isExpert = computed(() => store.level === 'expert')

/** 级别语义：基础只显示基础项，高级 = 基础 + 高级，专家 = 全部。 */
const visibleFields = computed<ConfigFieldMeta[]>(() =>
  store.fields.filter((field) => LEVEL_ORDER[field.level] <= LEVEL_ORDER[store.level]),
)

const groups = computed(() => {
  const order = store.schema?.areas ?? []
  const map = new Map<string, ConfigFieldMeta[]>()
  for (const field of visibleFields.value) {
    const list = map.get(field.area)
    if (list) list.push(field)
    else map.set(field.area, [field])
  }
  const rank = (area: string): number => {
    const index = order.indexOf(area)
    return index === -1 ? order.length : index
  }
  return [...map.entries()]
    .sort((left, right) => rank(left[0]) - rank(right[0]))
    .map(([area, items]) => ({ area, items }))
})

const unusedCount = computed(
  () =>
    visibleFields.value.filter(
      (field) => field.usage_status === 'DEFINED_BUT_UNUSED' || field.usage_status === 'LEGACY',
    ).length,
)

function formatValue(value: unknown): string {
  if (value === undefined || value === null) return '（空）'
  if (typeof value === 'boolean') return value ? '开启' : '关闭'
  if (typeof value === 'string') return value.length > 60 ? `${value.slice(0, 60)}…` : value
  if (typeof value === 'number') return String(value)
  try {
    const text = JSON.stringify(value)
    if (!text) return '（空）'
    return text.length > 60 ? `${text.slice(0, 60)}…` : text
  } catch {
    return String(value)
  }
}

function labelFor(key: string): string {
  return store.fields.find((field) => field.key === key)?.label ?? key
}

async function reload(): Promise<void> {
  await store.loadSchema({ includeUnused: isExpert.value })
  await store.loadEffective()
}

async function focusField(key: string): Promise<void> {
  focusKey.value = key
  const meta = store.fields.find((field) => field.key === key)
  if (meta && LEVEL_ORDER[meta.level] > LEVEL_ORDER[store.level]) {
    store.setLevel(meta.level)
    await reload()
  }
  await nextTick()
  const element = document.getElementById(`field-${key}`)
  element?.scrollIntoView?.({ block: 'center' })
}

function changeLevel(next: ConfigLevel): void {
  if (next === store.level) return
  keptCount.value = store.dirtyKeys.length
  store.setLevel(next)
  if (keptCount.value > 0) {
    toast.info('已保留未保存的修改', `${keptCount.value} 项修改在切换级别后仍然保留`)
  }
  void reload()
}

function discardDraft(): void {
  store.discard()
  keptCount.value = 0
}

async function openApplyPreview(): Promise<void> {
  if (!store.isDirty || store.applying) return
  const result = await store.validate()
  if (!result) {
    toast.error('无法预览修改', store.error || '请检查填写内容')
    return
  }
  previewHot.value = result.hot_reload.length
  previewRestart.value = result.restart_required.length
  previewLines.value = store.dirtyKeys.map((key) => {
    const before = formatValue(store.effectiveByKey.get(key)?.value)
    const after = formatValue(store.draft[key])
    return `${labelFor(key)}：旧值 ${before} → 新值 ${after}`
  })
  showPreview.value = true
}

const previewDetail = computed(() => {
  const head = `共 ${store.dirtyKeys.length} 项修改：`
  const tail = `✓ ${previewHot.value} 项立即生效 / ⚠ ${previewRestart.value} 项需要重启`
  return [head, ...previewLines.value, tail].join('\n')
})

async function confirmApply(): Promise<void> {
  showPreview.value = false
  const result = await store.apply()
  if (!result) {
    toast.error('保存失败', store.error || '请稍后重试')
    return
  }
  keptCount.value = 0
  toast.success(
    '✓ 设置已保存',
    `立即生效 ${result.hot_reload.length} 项 · 重启后生效 ${result.restart_required.length} 项`,
  )
}

function onBeforeUnload(event: BeforeUnloadEvent): void {
  if (!store.isDirty) return
  event.preventDefault()
  event.returnValue = ''
}

function onQueryFocus(value: unknown): void {
  if (typeof value === 'string' && value) void focusField(value)
}

onMounted(async () => {
  const levelQuery = typeof route.query.level === 'string' ? route.query.level : ''
  if ((LEVELS as string[]).includes(levelQuery)) store.setLevel(levelQuery as ConfigLevel)
  await Promise.all([reload(), store.loadRestartPending()])
  onQueryFocus(route.query.focus)
  window.addEventListener('beforeunload', onBeforeUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', onBeforeUnload)
})

watch(
  () => route.query.focus,
  (value) => {
    if (value !== undefined) onQueryFocus(value)
  },
)

watch(
  () => store.isDirty,
  (dirty) => {
    if (!dirty) keptCount.value = 0
  },
)
</script>

<template>
  <div class="cb-settings" data-test="settings-page">
    <SectionHeader
      title="设置中心"
      description="按级别显示配置项；所有修改先进入草稿，确认后一次性保存。"
    />

    <div class="cb-settings__levels" role="group" aria-label="配置级别">
      <button
        v-for="level in LEVELS"
        :key="level"
        type="button"
        class="cb-settings__level"
        :class="{ 'cb-settings__level--active': store.level === level }"
        :aria-pressed="store.level === level"
        :data-test="`level-${level}`"
        @click="changeLevel(level)"
      >
        {{ LEVEL_LABELS[level] }}
      </button>
      <span v-if="keptCount > 0" class="cb-settings__kept" data-test="draft-kept-hint" role="status">
        已保留 {{ keptCount }} 项未保存修改
      </span>
    </div>

    <ConfigSearch :fields="store.fields" @select="focusField" />

    <div v-if="isExpert" class="cb-settings__expert" data-test="expert-warning" role="status">
      <p class="cb-settings__expert-title">
        <span aria-hidden="true">⚠</span> 专家模式
      </p>
      <p>
        这里会显示标记为「当前未使用」或「旧语义（已废弃）」的配置项（当前 {{ unusedCount }} 项）。
        这些配置项当前没有被 Runtime 读取，修改它们不会改变 Bot 行为。
      </p>
    </div>

    <ErrorState v-if="store.error" :message="store.error" @retry="reload" />

    <LoadingState v-if="store.loading && store.fields.length === 0" label="正在读取配置…" :rows="4" />

    <EmptyState
      v-else-if="groups.length === 0"
      title="没有可显示的设置"
      description="当前级别下没有配置项，试试切换级别或清空搜索。"
    />

    <template v-else>
      <ConfigSection
        v-for="group in groups"
        :key="group.area"
        :title="group.area"
        :fields="group.items"
      >
        <div
          v-for="field in group.items"
          :id="`field-${field.key}`"
          :key="field.key"
          class="cb-settings__field"
          :class="{ 'cb-settings__field--focus': focusKey === field.key }"
          data-test="settings-field"
          :data-key="field.key"
        >
          <ConfigField
            :field="field"
            :value="store.draftValue(field.key)"
            :disabled="field.sensitive"
            @update:value="store.setValue(field.key, $event)"
          />
          <div class="cb-settings__meta">
            <ConfigSourceBadge
              :source="store.effectiveByKey.get(field.key)?.source"
              :field-key="field.key"
            />
            <ConfigStatusBadge
              :field="field"
              :effective="store.effectiveByKey.get(field.key)"
              :dirty="store.isDirtyKey(field.key)"
              :pending-restart="store.restartPending.pending.includes(field.key)"
            />
          </div>
        </div>
      </ConfigSection>
    </template>

    <DirtyBar
      v-if="store.isDirty"
      :count="store.dirtyKeys.length"
      :dirty-keys="store.dirtyKeys"
      :applying="store.applying"
      @discard="discardDraft"
      @apply="openApplyPreview"
      @preview="openApplyPreview"
    />

    <ConfirmDialog
      :show="showPreview"
      title="确认应用修改"
      :message="`将保存 ${store.dirtyKeys.length} 项修改`"
      :detail="previewDetail"
      confirm-text="应用修改"
      @confirm="confirmApply"
      @cancel="showPreview = false"
    />
  </div>
</template>

<style scoped>
.cb-settings {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-settings__levels {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-2);
}

.cb-settings__level {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text-muted);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-settings__level:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-settings__level--active {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.cb-settings__kept {
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.cb-settings__expert {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-md);
  background: var(--cb-warning-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-settings__expert-title {
  color: var(--cb-warning);
}

.cb-settings__field {
  border-radius: var(--cb-radius-sm);
  transition: background 0.2s ease;
}

.cb-settings__field--focus {
  background: var(--cb-primary-soft);
  outline: 1px solid var(--cb-primary);
  scroll-margin-top: var(--cb-space-5);
}

.cb-settings__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-2);
  padding-bottom: var(--cb-space-2);
}
</style>
