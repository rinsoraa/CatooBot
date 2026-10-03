<script setup lang="ts">
/**
 * 表情与口癖（W5 §39-§41）：贴纸库与口癖列表，都是服务端过滤 + 分页。
 *
 * 诚实说明：`preview_url` 恒为 null（后端没有贴纸静态路由），这里显示文件名
 * 占位，不伪造图片；删除是归档（可回溯），仍需二次确认。
 */
import { computed, onMounted, ref } from 'vue'

import { mediaApi } from '@/api/abilities'
import { errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type { ExpressionRow, StickerRow } from '@/types/domain'

const STICKER_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  disabled: '停用',
  archived: '已归档',
}

const PAGE_SIZE = 50

// ------------------------------------------------------------------ stickers
const stickers = ref<StickerRow[]>([])
const stickerStats = ref<Record<string, unknown>>({})
const stickerTotal = ref<number | null>(null)
const stickersLoading = ref(false)
const stickersError = ref('')
const stickerQuery = ref('')
const stickerStatus = ref('')
const offset = ref(0)

// --------------------------------------------------------------- expressions
const expressions = ref<ExpressionRow[]>([])
const expressionStats = ref<Record<string, unknown>>({})
const expressionsLoading = ref(false)
const expressionsError = ref('')
const expressionStatus = ref('')

// ------------------------------------------------------------------ dialogs
const deleteTarget = ref<{ kind: 'sticker' | 'expression'; id: string; label: string } | null>(null)
const showDeleteConfirm = ref(false)
const working = ref(false)
const showReindexConfirm = ref(false)
const reindexResult = ref<Record<string, unknown> | null>(null)

const page = computed(() => Math.floor(offset.value / PAGE_SIZE) + 1)
const hasPrev = computed(() => offset.value > 0)
const hasNext = computed(() => stickers.value.length >= PAGE_SIZE)

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function statusLabel(status: string): string {
  return STICKER_STATUS_LABELS[status] ?? (status || '—')
}

function statusState(status: string): StatusState {
  if (status === 'active') return 'ok'
  if (status === 'disabled') return 'off'
  if (status === 'archived') return 'idle'
  return 'warn'
}

async function loadStickers(): Promise<void> {
  stickersLoading.value = true
  stickersError.value = ''
  try {
    const data = await mediaApi.stickers({
      q: stickerQuery.value.trim() || undefined,
      status: stickerStatus.value || undefined,
      limit: PAGE_SIZE,
      offset: offset.value,
    })
    stickers.value = data.items ?? []
    stickerStats.value = data.stats ?? {}
    stickerTotal.value = data.total ?? null
  } catch (caught) {
    stickersError.value = errorMessage(caught)
  } finally {
    stickersLoading.value = false
  }
}

async function loadExpressions(): Promise<void> {
  expressionsLoading.value = true
  expressionsError.value = ''
  try {
    const data = await mediaApi.expressions({
      status: expressionStatus.value || undefined,
      limit: 200,
    })
    expressions.value = data.items ?? []
    expressionStats.value = data.stats ?? {}
  } catch (caught) {
    expressionsError.value = errorMessage(caught)
  } finally {
    expressionsLoading.value = false
  }
}

function applyStickerFilter(): void {
  offset.value = 0
  void loadStickers()
}

function changePage(delta: number): void {
  offset.value = Math.max(0, offset.value + delta * PAGE_SIZE)
  void loadStickers()
}

function startDeleteSticker(sticker: StickerRow): void {
  deleteTarget.value = {
    kind: 'sticker',
    id: sticker.sticker_id,
    label: sticker.file_name || sticker.sticker_id,
  }
  showDeleteConfirm.value = true
}

function startDeleteExpression(row: ExpressionRow): void {
  deleteTarget.value = { kind: 'expression', id: row.pattern_id, label: row.pattern }
  showDeleteConfirm.value = true
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  working.value = true
  try {
    if (target.kind === 'sticker') {
      await mediaApi.stickerAction(target.id, 'delete', 'delete')
      toast.success('贴纸已归档', `${target.label} 落成 archived，可回溯`)
      await loadStickers()
    } else {
      await mediaApi.expressionAction(target.id, 'delete', 'delete')
      toast.success('口癖已删除', target.label)
      await loadExpressions()
    }
  } catch (caught) {
    toast.error('删除失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function setStickerStatus(sticker: StickerRow, action: 'enable' | 'disable'): Promise<void> {
  working.value = true
  try {
    await mediaApi.stickerAction(sticker.sticker_id, action)
    toast.success(action === 'enable' ? '贴纸已启用' : '贴纸已停用', sticker.file_name || sticker.sticker_id)
    await loadStickers()
  } catch (caught) {
    toast.error('操作失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function setExpressionStatus(row: ExpressionRow, action: 'enable' | 'disable'): Promise<void> {
  working.value = true
  try {
    await mediaApi.expressionAction(row.pattern_id, action)
    toast.success(action === 'enable' ? '口癖已启用' : '口癖已停用', row.pattern)
    await loadExpressions()
  } catch (caught) {
    toast.error('操作失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

async function confirmReindex(): Promise<void> {
  showReindexConfirm.value = false
  working.value = true
  try {
    reindexResult.value = await mediaApi.reindex()
    toast.success('重新索引完成', `扫描 ${text(reindexResult.value.scanned)} 条`)
    await loadStickers()
  } catch (caught) {
    toast.error('重新索引失败', errorMessage(caught))
  } finally {
    working.value = false
  }
}

onMounted(() => {
  void loadStickers()
  void loadExpressions()
})
</script>

<template>
  <div class="media" data-test="abilities-media">
    <section class="media__section cb-card" data-test="media-stickers">
      <SectionHeader
        title="贴纸库"
        description="只有真实贴纸库里的内容会出现在这里，普通图片不会被自动标记为贴纸。"
      />

      <form class="media__filters" @submit.prevent="applyStickerFilter">
        <label class="media__field">
          <span>关键词</span>
          <input v-model="stickerQuery" type="search" placeholder="文件名 / 表情" data-test="sticker-query" />
        </label>
        <label class="media__field">
          <span>状态</span>
          <select v-model="stickerStatus" data-test="sticker-status" @change="applyStickerFilter">
            <option value="">全部</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
            <option value="archived">已归档</option>
          </select>
        </label>
        <button type="submit" class="media__button media__button--primary" data-test="sticker-search">搜索</button>
        <button type="button" class="media__button" :disabled="working" data-test="sticker-reindex" @click="showReindexConfirm = true">
          重新索引
        </button>
      </form>

      <p v-if="reindexResult" class="media__reindex cb-caption" data-test="reindex-result">
        重新索引完成：扫描 {{ text(reindexResult.scanned) }} 条；新增 / 更新无法从扫描计数中区分（null），
        移除恒为 0（扫描从不删除记录）。
      </p>

      <ErrorState v-if="stickersError" :message="stickersError" @retry="loadStickers" />
      <LoadingState v-else-if="stickersLoading && stickers.length === 0" label="正在读取贴纸库…" :rows="3" />
      <EmptyState
        v-else-if="stickers.length === 0"
        title="没有匹配的贴纸"
        description="贴纸只会来自真实贴纸库；调整过滤条件或先让机器人收集贴纸。"
      />
      <div v-else class="media__grid">
        <article v-for="sticker in stickers" :key="sticker.sticker_id" class="media__sticker" data-test="sticker-card">
          <div class="media__preview" aria-hidden="true">
            <span v-if="sticker.file_name" class="media__file-name">{{ sticker.file_name }}</span>
            <span v-else class="media__file-name">（无文件名）</span>
          </div>
          <div class="media__sticker-body">
            <p class="media__sticker-title" data-test="sticker-name">{{ sticker.file_name || sticker.sticker_id }}</p>
            <p class="cb-caption">
              情绪：{{ sticker.emotion || '—' }} · 意图：{{ sticker.intent || '—' }} · 使用
              {{ sticker.usage_count }} 次
            </p>
            <p class="cb-caption" data-test="sticker-valid">
              文件引用：{{ sticker.valid ? '可解析' : '无法解析' }} · 来源：{{ sticker.origin || '—' }}
            </p>
            <div class="media__actions">
              <StatusBadge :state="statusState(sticker.status)" :label="statusLabel(sticker.status)" />
              <button
                v-if="sticker.status !== 'active'"
                type="button"
                class="media__button"
                :disabled="working"
                data-test="sticker-enable"
                @click="setStickerStatus(sticker, 'enable')"
              >
                启用
              </button>
              <button
                v-else
                type="button"
                class="media__button"
                :disabled="working"
                data-test="sticker-disable"
                @click="setStickerStatus(sticker, 'disable')"
              >
                停用
              </button>
              <button type="button" class="media__button media__button--danger" :disabled="working" data-test="sticker-delete" @click="startDeleteSticker(sticker)">
                删除
              </button>
            </div>
          </div>
        </article>
      </div>

      <nav v-if="stickers.length > 0" class="media__pager" aria-label="贴纸分页">
        <button type="button" class="media__button" :disabled="!hasPrev || stickersLoading" data-test="sticker-prev" @click="changePage(-1)">
          上一页
        </button>
        <span class="cb-caption" data-test="sticker-page">
          第 {{ page }} 页 · 本页 {{ stickers.length }} 条 ·
          总数 {{ stickerTotal === null ? '—' : stickerTotal }}
        </span>
        <button type="button" class="media__button" :disabled="!hasNext || stickersLoading" data-test="sticker-next" @click="changePage(1)">
          下一页
        </button>
      </nav>
    </section>

    <section class="media__section cb-card" data-test="media-expressions">
      <SectionHeader
        title="口癖"
        description="从真实对话中学到的表达模式；停用后不再使用，删除会同时删掉来源与向量。"
      />

      <div class="media__filters">
        <label class="media__field">
          <span>状态</span>
          <select v-model="expressionStatus" data-test="expression-status" @change="loadExpressions">
            <option value="">全部</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </select>
        </label>
        <span class="cb-caption">
          统计：共 {{ text(expressionStats.total) }} 条 · 启用 {{ text(expressionStats.active) }} · 停用
          {{ text(expressionStats.disabled) }}
        </span>
      </div>

      <ErrorState v-if="expressionsError" :message="expressionsError" @retry="loadExpressions" />
      <LoadingState v-else-if="expressionsLoading && expressions.length === 0" label="正在读取口癖…" :rows="3" />
      <p v-else-if="expressions.length === 0" class="cb-muted" data-test="expressions-empty">
        还没有学到口癖；普通消息不会被当作口癖。
      </p>
      <table v-else class="media__table" data-test="expressions-table">
        <thead>
          <tr>
            <th scope="col">口癖</th>
            <th scope="col">类型</th>
            <th scope="col">范围</th>
            <th scope="col">出现</th>
            <th scope="col">使用者</th>
            <th scope="col">状态</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in expressions" :key="row.pattern_id" data-test="expression-row">
            <td>{{ row.pattern }}</td>
            <td>{{ row.kind || '—' }}</td>
            <td>{{ row.group_id || '全局' }}</td>
            <td>{{ row.occurrences }}</td>
            <td>{{ row.speakers }}</td>
            <td>
              <StatusBadge :state="statusState(row.status)" :label="statusLabel(row.status)" />
            </td>
            <td class="media__row-actions">
              <button
                v-if="row.status !== 'active'"
                type="button"
                class="media__button"
                :disabled="working"
                data-test="expression-enable"
                @click="setExpressionStatus(row, 'enable')"
              >
                启用
              </button>
              <button
                v-else
                type="button"
                class="media__button"
                :disabled="working"
                data-test="expression-disable"
                @click="setExpressionStatus(row, 'disable')"
              >
                停用
              </button>
              <button type="button" class="media__button media__button--danger" :disabled="working" data-test="expression-delete" @click="startDeleteExpression(row)">
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <ConfirmDialog
      v-model:show="showDeleteConfirm"
      :title="deleteTarget?.kind === 'expression' ? '删除口癖' : '删除贴纸'"
      :message="
        deleteTarget?.kind === 'expression'
          ? `确定删除口癖「${deleteTarget?.label}」吗？`
          : `确定删除贴纸「${deleteTarget?.label}」吗？`
      "
      :detail="
        deleteTarget?.kind === 'expression'
          ? '删除会同时移除来源与向量，无法恢复。'
          : '删除落成归档（archived），可回溯；不会物理删除文件。'
      "
      confirm-text="删除"
      danger
      @confirm="confirmDelete"
    />

    <ConfirmDialog
      v-model:show="showReindexConfirm"
      title="重新索引贴纸库"
      message="确定全量重扫贴纸目录吗？"
      detail="扫描只会新增 / 更新记录，永远不会删除已有贴纸；过程可能需要一点时间。"
      confirm-text="开始重扫"
      @confirm="confirmReindex"
    />
  </div>
</template>

<style scoped>
.media {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.media__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.media__filters {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.media__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.media__field input,
.media__field select {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.media__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--cb-space-3);
}

.media__sticker {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.media__preview {
  display: grid;
  place-items: center;
  min-height: 72px;
  padding: var(--cb-space-2);
  border: 1px dashed var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
}

.media__file-name {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
  overflow-wrap: anywhere;
  text-align: center;
}

.media__sticker-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.media__sticker-title {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.media__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex-wrap: wrap;
  margin-top: var(--cb-space-2);
}

.media__button {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
  white-space: nowrap;
}

.media__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.media__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.media__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.media__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.media__reindex {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
}

.media__pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding-top: var(--cb-space-2);
  border-top: 1px solid var(--cb-border);
}

.media__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.media__table th,
.media__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
}

.media__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.media__row-actions {
  display: flex;
  gap: var(--cb-space-2);
}
</style>
