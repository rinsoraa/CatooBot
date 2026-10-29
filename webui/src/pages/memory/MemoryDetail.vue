<script setup lang="ts">
/**
 * 记忆详情（W5 §29）：`/memory/:memoryId` 按需加载，全文 + 出处 + 检索元数据。
 *
 * 变更只走 store.act（真实服务端点），归档与删除都要确认；删除文案明确
 * 「这会修改长期记忆，不是聊天记录」。Expert 折叠区展示 memory_id / scope_key
 * 与 provenance 原始 JSON。404 由后端 message 呈现为 ErrorState。
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import MemoryCard from '@/components/domain/MemoryCard.vue'
import { errorMessage } from '@/api/client'
import { toast } from '@/composables/toast'
import { useMemoryStore } from '@/stores/memory'
import type { MemoryRow } from '@/types/domain'

type MemoryAction = 'activate' | 'archive' | 'reembed' | 'edit' | 'delete'

const DELETE_MESSAGE = '这会修改长期记忆，不是聊天记录。删除后无法恢复'
const ARCHIVE_MESSAGE = '这会修改长期记忆，不是聊天记录。归档后可在「已归档」筛选中查看。'

const PROVENANCE_FIELDS: { key: string; label: string }[] = [
  { key: 'episode_key', label: 'episode_key' },
  { key: 'experience_id', label: 'experience_id' },
  { key: 'event_id', label: 'event_id' },
  { key: 'candidate_id', label: 'candidate_id' },
]

const route = useRoute()
const router = useRouter()
const store = useMemoryStore()

const busy = ref(false)
const editing = ref(false)
const draft = ref('')
const showArchive = ref(false)
const showDelete = ref(false)

const memoryId = computed<number | null>(() => {
  const raw = route.params.memoryId
  const text = Array.isArray(raw) ? raw[0] : raw
  const value = Number(text)
  return Number.isInteger(value) && value > 0 ? value : null
})

const memory = computed<MemoryRow | null>(() => store.detail?.memory ?? null)

watch(
  memoryId,
  (value) => {
    if (value === null) return
    store.clear()
    editing.value = false
    draft.value = ''
    void store.loadDetail(value)
  },
  { immediate: true },
)

function reload(): void {
  if (memoryId.value === null) return
  void store.loadDetail(memoryId.value)
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value) ?? '—'
}

function extra(row: MemoryRow, key: string): unknown {
  return (row as unknown as Record<string, unknown>)[key]
}

function formatTime(value: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '—'
  const date = new Date(value * 1000)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

const personLabel = computed(() => {
  const row = memory.value
  if (!row) return '—'
  const scope = row.scope_key ?? ''
  if (scope.startsWith('user:')) return scope.slice(5) || '—'
  const prov = row.provenance ?? {}
  const candidate = prov.person_id ?? prov.person ?? prov.display_name
  if (typeof candidate === 'number') return String(candidate)
  if (typeof candidate === 'string' && candidate) return candidate
  return '—'
})

const sourceLabel = computed(() => (memory.value ? formatValue(extra(memory.value, 'source')) : '—'))

const provenanceRows = computed(() => {
  const prov = memory.value?.provenance ?? {}
  const known = PROVENANCE_FIELDS.map((field) => ({
    key: field.key,
    label: field.label,
    value: formatValue(prov[field.key]),
  }))
  const extras = Object.keys(prov)
    .filter((key) => !PROVENANCE_FIELDS.some((field) => field.key === key))
    .map((key) => ({ key, label: key, value: formatValue(prov[key]) }))
  return [...known, ...extras]
})

const hasRetrieval = computed(() => {
  const row = memory.value
  if (!row) return false
  return row.score !== undefined || Boolean(row.origin) || extra(row, 'final') !== undefined
})

const retrievalRows = computed(() => {
  const row = memory.value
  if (!row) return []
  return [
    { label: '评分', value: formatValue(row.score ?? extra(row, 'final')) },
    { label: '来源通路', value: formatValue(row.origin) },
  ]
})

const expertJson = computed(() =>
  JSON.stringify(
    {
      memory_id: memory.value?.memory_id ?? null,
      scope_key: memory.value?.scope_key ?? null,
      provenance: memory.value?.provenance ?? {},
    },
    null,
    2,
  ),
)

async function runAction(
  action: MemoryAction,
  body: Record<string, unknown>,
  successMessage: string,
): Promise<boolean> {
  const id = memoryId.value
  if (id === null) return false
  busy.value = true
  try {
    const done = await store.act(id, action, body)
    if (done) {
      toast.success(successMessage)
      return true
    }
    toast.error('操作失败', store.error)
    return false
  } catch (caught) {
    toast.error('操作失败', errorMessage(caught))
    return false
  } finally {
    busy.value = false
  }
}

async function doArchive(): Promise<void> {
  await runAction('archive', {}, '记忆已归档')
}

async function doReembed(): Promise<void> {
  await runAction('reembed', {}, '已重新嵌入这条记忆')
}

async function doDelete(): Promise<void> {
  const done = await runAction('delete', {}, '记忆已删除')
  if (done) void router.push({ name: 'memory' })
}

function startEdit(): void {
  if (!memory.value) return
  draft.value = memory.value.content
  editing.value = true
}

function cancelEdit(): void {
  editing.value = false
  draft.value = ''
}

async function saveEdit(): Promise<void> {
  const content = draft.value.trim()
  if (!content) {
    toast.warning('记忆内容不能为空')
    return
  }
  const done = await runAction('edit', { content }, '记忆内容已保存')
  if (done) editing.value = false
}
</script>

<template>
  <div class="cb-memory-detail" data-test="memory-detail">
    <LoadingState v-if="store.loading && !memory" label="正在读取记忆详情…" :rows="4" />

    <ErrorState v-else-if="store.error && !memory" :message="store.error" @retry="reload" />

    <EmptyState
      v-else-if="!memory"
      title="没有可展示的记忆"
      description="这条记忆可能已被删除，或链接中的编号无效。"
    />

    <template v-else>
      <MemoryCard :memory="memory" expert />

      <section class="cb-memory-detail__panel cb-card">
        <SectionHeader title="记忆内容" description="全文不做截断；摘要在列表与时间线中展示。" />
        <p class="cb-memory-detail__content" data-test="detail-content">{{ memory.content }}</p>
      </section>

      <section class="cb-memory-detail__panel cb-card">
        <SectionHeader title="归属与时间" description="scope 是记忆的隔离边界；缺失字段显示「—」。" />
        <dl class="cb-memory-detail__meta">
          <div class="cb-memory-detail__meta-row">
            <dt>记忆 ID</dt>
            <dd data-test="detail-id">{{ memory.memory_id }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>person</dt>
            <dd data-test="detail-person">{{ personLabel }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>scope</dt>
            <dd data-test="detail-scope">{{ memory.scope_key || '—' }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>状态</dt>
            <dd data-test="detail-status">{{ memory.status || '—' }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>source</dt>
            <dd data-test="detail-source">{{ sourceLabel }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>创建时间</dt>
            <dd data-test="detail-created">{{ formatTime(memory.created_at) }}</dd>
          </div>
          <div class="cb-memory-detail__meta-row">
            <dt>更新时间</dt>
            <dd data-test="detail-updated">{{ formatTime(memory.updated_at) }}</dd>
          </div>
        </dl>
      </section>

      <section class="cb-memory-detail__panel cb-card">
        <SectionHeader title="出处（provenance）" description="逐字段展示写入来源；缺失即「—」。" />
        <dl class="cb-memory-detail__meta" data-test="detail-provenance">
          <div v-for="row in provenanceRows" :key="row.key" class="cb-memory-detail__meta-row">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
        </dl>
      </section>

      <section v-if="hasRetrieval" class="cb-memory-detail__panel cb-card">
        <SectionHeader title="检索元数据" description="来自检索结果的评分与命中通路。" />
        <dl class="cb-memory-detail__meta" data-test="detail-retrieval">
          <div v-for="row in retrievalRows" :key="row.label" class="cb-memory-detail__meta-row">
            <dt>{{ row.label }}</dt>
            <dd>{{ row.value }}</dd>
          </div>
        </dl>
      </section>

      <section class="cb-memory-detail__panel cb-card">
        <SectionHeader title="动作" description="所有变更都会立即写入长期记忆。" />

        <p class="cb-memory-detail__warning">这会修改长期记忆，不是聊天记录。</p>

        <div class="cb-memory-detail__actions">
          <button
            type="button"
            class="cb-memory-detail__button"
            data-test="detail-archive"
            :disabled="busy || memory.status === 'archived'"
            @click="showArchive = true"
          >
            归档
          </button>
          <button
            type="button"
            class="cb-memory-detail__button"
            data-test="detail-reembed"
            :disabled="busy"
            @click="doReembed"
          >
            重新嵌入
          </button>
          <button
            type="button"
            class="cb-memory-detail__button"
            data-test="detail-edit"
            :disabled="busy"
            @click="startEdit"
          >
            编辑
          </button>
          <button
            type="button"
            class="cb-memory-detail__button cb-memory-detail__button--danger"
            data-test="detail-delete"
            :disabled="busy"
            @click="showDelete = true"
          >
            删除
          </button>
        </div>

        <div v-if="editing" class="cb-memory-detail__editor">
          <label class="cb-caption" for="cb-memory-detail-edit">新的记忆内容</label>
          <textarea
            id="cb-memory-detail-edit"
            v-model="draft"
            class="cb-memory-detail__textarea"
            rows="5"
            data-test="detail-edit-input"
          />
          <div class="cb-memory-detail__editor-actions">
            <button
              type="button"
              class="cb-memory-detail__button"
              data-test="detail-edit-cancel"
              :disabled="busy"
              @click="cancelEdit"
            >
              取消
            </button>
            <button
              type="button"
              class="cb-memory-detail__button cb-memory-detail__button--primary"
              data-test="detail-edit-save"
              :disabled="busy"
              @click="saveEdit"
            >
              保存
            </button>
          </div>
        </div>
      </section>

      <details class="cb-memory-detail__panel cb-card" data-test="detail-expert">
        <summary class="cb-memory-detail__summary">专家信息</summary>
        <pre class="cb-memory-detail__pre" data-test="detail-expert-json">{{ expertJson }}</pre>
      </details>
    </template>

    <ConfirmDialog
      v-model:show="showArchive"
      title="归档记忆"
      :message="ARCHIVE_MESSAGE"
      confirm-text="归档"
      @confirm="doArchive"
    />

    <ConfirmDialog
      v-model:show="showDelete"
      title="删除记忆"
      :message="DELETE_MESSAGE"
      confirm-text="删除"
      danger
      @confirm="doDelete"
    />
  </div>
</template>

<style scoped>
.cb-memory-detail {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-detail__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-detail__content {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.cb-memory-detail__meta {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  margin: 0;
}

.cb-memory-detail__meta-row {
  display: flex;
  gap: var(--cb-space-3);
  padding: var(--cb-space-1) 0;
  border-bottom: 1px solid var(--cb-border);
  font-size: var(--cb-text-sm);
}

.cb-memory-detail__meta-row dt {
  flex: 0 0 120px;
  color: var(--cb-text-muted);
}

.cb-memory-detail__meta-row dd {
  margin: 0;
  color: var(--cb-text);
  overflow-wrap: anywhere;
  font-family: var(--cb-font-mono);
}

.cb-memory-detail__warning {
  font-size: var(--cb-text-sm);
  color: var(--cb-warning);
}

.cb-memory-detail__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
}

.cb-memory-detail__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-memory-detail__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-memory-detail__button:disabled {
  color: var(--cb-text-faint);
  cursor: not-allowed;
}

.cb-memory-detail__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-memory-detail__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.cb-memory-detail__editor {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
}

.cb-memory-detail__textarea {
  width: 100%;
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  resize: vertical;
}

.cb-memory-detail__textarea:focus {
  border-color: var(--cb-primary);
  outline: none;
  box-shadow: var(--cb-focus);
}

.cb-memory-detail__editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cb-space-2);
}

.cb-memory-detail__summary {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  cursor: pointer;
}

.cb-memory-detail__pre {
  margin: var(--cb-space-3) 0 0;
  padding: var(--cb-space-3);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  overflow-x: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
