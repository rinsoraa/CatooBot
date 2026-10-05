<script setup lang="ts">
/**
 * 记忆运维（v0.8 迁移）：向量状态 + 重建 / 重试 / 清空缓存，记忆整理状态与手动运行，
 * 以及检索打分链路调试（只读）。
 *
 * 危险动作（清空向量缓存）先过确认框，再由 API 层带 `confirm: "clear-cache"` 调后端；
 * 后端返回 `{error}`（如语义记忆未启用）时按提示信息展示，不当作异常崩溃。
 * 所有数字来自后端，未知一律「—」，不做估算。
 */
import { computed, onMounted, ref } from 'vue'

import { errorMessage } from '@/api/client'
import { memoryApi, type MemoryEmbeddingAction } from '@/api/memory'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import MetricCard from '@/components/MetricCard.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import type {
  MemoryConsolidationReport,
  MemoryConsolidationStatus,
  MemoryEmbeddingStatus,
  MemoryRetrievalDebug,
  MemoryRetrievalScoredRow,
} from '@/types/domain'

const EMBEDDING_LABELS: Record<MemoryEmbeddingAction, string> = {
  rebuild: '向量重建',
  retry: '向量重试',
  'clear-cache': '清空向量缓存',
}

const SCHEDULE_LABELS: Record<string, string> = {
  hourly: '每小时',
  daily: '每天',
  manual: '手动',
}

const ORIGIN_LABELS: Record<string, string> = {
  keyword: '关键词命中',
  semantic: '语义命中',
  both: '关键词 + 语义',
}

// ------------------------------------------------------------- 向量状态
const loadingEmbedding = ref(false)
const embedding = ref<MemoryEmbeddingStatus | null>(null)
const embeddingError = ref('')
const actionPending = ref<MemoryEmbeddingAction | ''>('')
const showClearConfirm = ref(false)

// ------------------------------------------------------------- 记忆整理
const loadingConsolidation = ref(false)
const consolidation = ref<MemoryConsolidationStatus | null>(null)
const consolidationError = ref('')
const consolidationScope = ref('')
const runningConsolidation = ref(false)
const lastReport = ref<MemoryConsolidationReport | null>(null)

// ------------------------------------------------------------- 检索调试
const debugQuery = ref('')
const debugScope = ref('')
const debugLoading = ref(false)
const debugError = ref('')
const debugResult = ref<MemoryRetrievalDebug | null>(null)

function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function fmt(value: unknown): string {
  const parsed = num(value)
  if (parsed !== null) return String(parsed)
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function fmtOrNull(value: unknown): string | number | null {
  const parsed = num(value)
  if (parsed !== null) return parsed
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

function text(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null
  return String(value)
}

// ------------------------------------------------------------- 向量状态
const embeddingAvailableLabel = computed<string | null>(() => {
  const available = embedding.value?.available
  if (available === undefined) return null
  return available ? '已启用' : '未启用'
})

const embeddingState = computed<'ok' | 'off' | 'idle'>(() => {
  const available = embedding.value?.available
  if (available === undefined) return 'idle'
  return available ? 'ok' : 'off'
})

const embeddingModelLabel = computed<string | null>(() => {
  const provider = text(embedding.value?.provider)
  const model = text(embedding.value?.model)
  if (!provider && !model) return null
  return `${provider ?? '—'} / ${model ?? '—'}`
})

const embeddingPairValue = computed<string | null>(() => {
  const embedded = num(embedding.value?.embedded)
  const memories = num(embedding.value?.memories)
  if (embedded === null && memories === null) return null
  return `${fmt(embedded)} / ${fmt(memories)}`
})

const embeddingHint = computed<string>(() => {
  const parts: string[] = []
  const coverage = num(embedding.value?.coverage)
  if (coverage !== null) parts.push(`覆盖率 ${Math.round(coverage * 100)}%`)
  const pending = num(embedding.value?.pending)
  if (pending !== null) parts.push(`待补 ${pending} 条`)
  return parts.join(' · ')
})

const embeddingLastError = computed<string | null>(() => text(embedding.value?.last_error))

const actionBusy = computed(() => actionPending.value !== '')

// ------------------------------------------------------------- 记忆整理
const consolidationEnabled = computed(() => consolidation.value?.enabled !== false)

const scheduleLabel = computed<string | null>(() => {
  const schedule = text(consolidation.value?.schedule)
  if (!schedule) return null
  return SCHEDULE_LABELS[schedule] ?? schedule
})

const reportScopes = computed<string>(() => {
  const scopes = lastReport.value?.scopes
  if (!Array.isArray(scopes) || scopes.length === 0) return ''
  return scopes.join('、')
})

// ------------------------------------------------------------- 检索调试
const debugRows = computed<MemoryRetrievalScoredRow[]>(() => {
  const rows = debugResult.value?.results
  return Array.isArray(rows) ? rows : []
})

const debugScopes = computed<string | null>(() => {
  const scopes = debugResult.value?.scopes
  if (!Array.isArray(scopes) || scopes.length === 0) return null
  return scopes.join('、')
})

const debugResultError = computed<string | null>(() => text(debugResult.value?.error))

const debugRaw = computed<string>(() => {
  try {
    return JSON.stringify(debugResult.value ?? {}, null, 2)
  } catch {
    return ''
  }
})

function originLabel(origin: unknown): string {
  const key = text(origin)
  if (!key) return '—'
  return ORIGIN_LABELS[key] ?? key
}

// ------------------------------------------------------------- 数据加载
async function loadEmbedding(): Promise<void> {
  loadingEmbedding.value = true
  embeddingError.value = ''
  try {
    embedding.value = await memoryApi.embeddingStatus()
  } catch (caught) {
    embeddingError.value = errorMessage(caught)
  } finally {
    loadingEmbedding.value = false
  }
}

async function loadConsolidation(): Promise<void> {
  loadingConsolidation.value = true
  consolidationError.value = ''
  try {
    const data = await memoryApi.consolidationStatus()
    consolidation.value = data
    lastReport.value = data.last_report ?? null
  } catch (caught) {
    consolidationError.value = errorMessage(caught)
  } finally {
    loadingConsolidation.value = false
  }
}

function refreshAll(): void {
  void loadEmbedding()
  void loadConsolidation()
}

function actionDetail(action: MemoryEmbeddingAction, result: Record<string, unknown>): string {
  if (action === 'clear-cache') {
    return result.cleared === false ? '服务端未清空缓存' : '向量缓存已清空，下次检索会重新生成'
  }
  const rebuilt = num(result.rebuilt)
  return rebuilt === null ? '' : `已补齐 ${rebuilt} 条向量`
}

async function runEmbeddingAction(action: MemoryEmbeddingAction): Promise<void> {
  actionPending.value = action
  try {
    const payload = await memoryApi.embeddingAction(action)
    const failure = text(payload.result?.error)
    if (failure) {
      toast.warning(`${EMBEDDING_LABELS[action]}未执行`, failure)
    } else {
      toast.success(`${EMBEDDING_LABELS[action]}完成`, actionDetail(action, payload.result))
    }
    await loadEmbedding()
  } catch (caught) {
    toast.error(`${EMBEDDING_LABELS[action]}失败`, errorMessage(caught))
  } finally {
    actionPending.value = ''
  }
}

function requestClearCache(): void {
  showClearConfirm.value = true
}

function confirmClearCache(): void {
  showClearConfirm.value = false
  void runEmbeddingAction('clear-cache')
}

async function runConsolidation(): Promise<void> {
  runningConsolidation.value = true
  try {
    const payload = await memoryApi.runConsolidation(consolidationScope.value.trim())
    const failure = text(payload.result?.error)
    if (failure) {
      toast.warning('记忆整理未执行', failure)
      return
    }
    await loadConsolidation()
    lastReport.value = payload.result
    toast.success('记忆整理完成', text(payload.result?.summary) ?? '')
  } catch (caught) {
    toast.error('记忆整理失败', errorMessage(caught))
  } finally {
    runningConsolidation.value = false
  }
}

async function runRetrievalDebug(): Promise<void> {
  const query = debugQuery.value.trim()
  if (!query) {
    toast.warning('请输入查询词', '检索调试必须带 q 查询词')
    return
  }
  debugLoading.value = true
  debugError.value = ''
  try {
    debugResult.value = await memoryApi.retrievalDebug(query, debugScope.value.trim())
  } catch (caught) {
    debugResult.value = null
    debugError.value = errorMessage(caught)
  } finally {
    debugLoading.value = false
  }
}

onMounted(() => {
  void loadEmbedding()
  void loadConsolidation()
})
</script>

<template>
  <div class="cb-memory-ops" data-test="memory-ops">
    <!-- --------------------------------------------------- 向量与整理 -->
    <section class="cb-memory-ops__panel cb-card" data-test="ops-maintenance">
      <SectionHeader
        title="记忆运维 · 向量与整理"
        description="查看向量库与整理器的真实状态；重建、重试与整理都只操作记忆数据，不改配置。"
      >
        <template #actions>
          <button
            type="button"
            class="cb-memory-ops__button"
            data-test="ops-refresh"
            :disabled="loadingEmbedding || loadingConsolidation"
            @click="refreshAll"
          >
            刷新
          </button>
        </template>
      </SectionHeader>

      <h3 class="cb-memory-ops__subtitle">向量状态</h3>

      <ErrorState
        v-if="embeddingError"
        :message="embeddingError"
        data-test="embedding-error"
        @retry="loadEmbedding"
      />

      <LoadingState
        v-else-if="loadingEmbedding && !embedding"
        label="正在读取向量状态…"
        :rows="2"
      />

      <template v-else-if="embedding">
        <div class="cb-memory-ops__metrics" data-test="embedding-grid">
          <MetricCard
            label="向量服务"
            :value="embeddingAvailableLabel"
            :state="embeddingState"
            data-test="embedding-available"
          />
          <MetricCard label="服务商 / 模型" :value="embeddingModelLabel" data-test="embedding-model" />
          <MetricCard
            label="已向量化 / 记忆总数"
            :value="embeddingPairValue"
            :hint="embeddingHint"
            data-test="embedding-coverage"
          />
          <MetricCard label="维度" :value="fmtOrNull(embedding.dimensions)" data-test="embedding-dimensions" />
          <MetricCard label="缓存命中" :value="fmtOrNull(embedding.hits)" data-test="embedding-hits" />
          <MetricCard label="缓存未命中" :value="fmtOrNull(embedding.misses)" data-test="embedding-misses" />
          <MetricCard label="失败次数" :value="fmtOrNull(embedding.failures)" data-test="embedding-failures" />
        </div>

        <p
          v-if="embedding.available === false"
          class="cb-memory-ops__notice cb-caption"
          data-test="embedding-disabled-hint"
        >
          语义向量未启用：重建与重试不会执行，后端会返回原因。
        </p>
        <p v-if="embeddingLastError" class="cb-memory-ops__warn" role="alert" data-test="embedding-last-error">
          最近一次失败：{{ embeddingLastError }}
        </p>
      </template>

      <div class="cb-memory-ops__actions">
        <button
          type="button"
          class="cb-memory-ops__button"
          data-test="embeddings-rebuild"
          :disabled="actionBusy"
          @click="runEmbeddingAction('rebuild')"
        >
          {{ actionPending === 'rebuild' ? '重建中…' : '重建向量' }}
        </button>
        <button
          type="button"
          class="cb-memory-ops__button"
          data-test="embeddings-retry"
          :disabled="actionBusy"
          @click="runEmbeddingAction('retry')"
        >
          {{ actionPending === 'retry' ? '重试中…' : '重试失败' }}
        </button>
        <button
          type="button"
          class="cb-memory-ops__button cb-memory-ops__button--danger"
          data-test="embeddings-clear-cache"
          :disabled="actionBusy"
          @click="requestClearCache"
        >
          {{ actionPending === 'clear-cache' ? '清理中…' : '清空向量缓存' }}
        </button>
      </div>

      <h3 class="cb-memory-ops__subtitle">记忆整理</h3>

      <ErrorState
        v-if="consolidationError"
        :message="consolidationError"
        data-test="consolidation-error"
        @retry="loadConsolidation"
      />

      <LoadingState
        v-else-if="loadingConsolidation && !consolidation"
        label="正在读取整理状态…"
        :rows="2"
      />

      <template v-else-if="consolidation">
        <p
          v-if="!consolidationEnabled"
          class="cb-memory-ops__notice cb-caption"
          data-test="consolidation-disabled"
        >
          记忆整理未启用（记忆功能未开启）：无法查看调度与手动运行。
        </p>
        <div v-else class="cb-memory-ops__metrics" data-test="consolidation-grid">
          <MetricCard label="调度" :value="scheduleLabel" data-test="consolidation-schedule" />
          <MetricCard
            label="重复阈值"
            :value="fmtOrNull(consolidation.duplicate_threshold)"
            data-test="consolidation-duplicate-threshold"
          />
          <MetricCard
            label="压缩最小簇"
            :value="fmtOrNull(consolidation.compression_min_cluster)"
            data-test="consolidation-compression-min"
          />
          <MetricCard label="累计运行" :value="fmtOrNull(consolidation.runs)" data-test="consolidation-runs" />
        </div>
      </template>

      <div class="cb-memory-ops__run">
        <label class="cb-memory-ops__field">
          <span class="cb-caption">范围 scope_key（留空 = 全部范围）</span>
          <input
            v-model="consolidationScope"
            type="text"
            class="cb-memory-ops__input"
            placeholder="例如 group:123456"
            data-test="consolidation-scope"
          />
        </label>
        <button
          type="button"
          class="cb-memory-ops__button cb-memory-ops__button--primary"
          data-test="consolidation-run"
          :disabled="runningConsolidation || !consolidationEnabled"
          @click="runConsolidation"
        >
          {{ runningConsolidation ? '整理中…' : '运行整理' }}
        </button>
      </div>

      <div v-if="lastReport" class="cb-memory-ops__report" data-test="consolidation-report">
        <p class="cb-memory-ops__report-title" data-test="consolidation-summary">
          {{ lastReport.summary || '最近一次整理报告' }}
        </p>
        <dl class="cb-memory-ops__report-grid">
          <div><dt>扫描</dt><dd data-test="report-scanned">{{ fmt(lastReport.scanned) }}</dd></div>
          <div><dt>去重合并</dt><dd data-test="report-duplicates">{{ fmt(lastReport.duplicates_merged) }}</dd></div>
          <div><dt>归档</dt><dd data-test="report-archived">{{ fmt(lastReport.archived) }}</dd></div>
          <div><dt>压缩簇</dt><dd data-test="report-compressed">{{ fmt(lastReport.compressed_clusters) }}</dd></div>
          <div><dt>压缩来源</dt><dd data-test="report-sources">{{ fmt(lastReport.compressed_sources) }}</dd></div>
          <div><dt>模型压缩</dt><dd data-test="report-llm">{{ fmt(lastReport.llm_compressions) }}</dd></div>
          <div><dt>冲突</dt><dd data-test="report-conflicts">{{ fmt(lastReport.conflicts) }}</dd></div>
          <div><dt>配额归档</dt><dd data-test="report-quota">{{ fmt(lastReport.quota_archived) }}</dd></div>
          <div><dt>错误</dt><dd data-test="report-errors">{{ fmt(lastReport.errors) }}</dd></div>
          <div><dt>耗时 (ms)</dt><dd data-test="report-duration">{{ fmt(lastReport.duration_ms) }}</dd></div>
        </dl>
        <p v-if="reportScopes" class="cb-caption" data-test="report-scopes">范围：{{ reportScopes }}</p>
      </div>
    </section>

    <!-- --------------------------------------------------- 检索调试 -->
    <section class="cb-memory-ops__panel cb-card" data-test="ops-retrieval">
      <SectionHeader
        title="检索调试"
        description="只读展示一次查询的候选、守卫与混合打分分量；不会写入或修改任何记忆。"
      />

      <form class="cb-memory-ops__form" data-test="retrieval-form" @submit.prevent="runRetrievalDebug">
        <label class="cb-memory-ops__field cb-memory-ops__field--grow">
          <span class="cb-caption">查询词 q</span>
          <input
            v-model="debugQuery"
            type="text"
            class="cb-memory-ops__input"
            placeholder="例如：她喜欢吃什么？"
            data-test="retrieval-q"
          />
        </label>
        <label class="cb-memory-ops__field">
          <span class="cb-caption">范围 scope（可选）</span>
          <input
            v-model="debugScope"
            type="text"
            class="cb-memory-ops__input"
            placeholder="留空 = 全部范围"
            data-test="retrieval-scope"
          />
        </label>
        <button
          type="submit"
          class="cb-memory-ops__button cb-memory-ops__button--primary"
          data-test="retrieval-run"
          :disabled="debugLoading"
        >
          {{ debugLoading ? '检索中…' : '运行检索' }}
        </button>
      </form>

      <ErrorState
        v-if="debugError"
        :message="debugError"
        data-test="retrieval-error"
        @retry="runRetrievalDebug"
      />

      <template v-else-if="debugResult">
        <p class="cb-memory-ops__notice cb-caption" data-test="retrieval-echo">
          查询「{{ debugResult.query }}」<template v-if="debugScopes"> · 范围：{{ debugScopes }}</template>
        </p>

        <p v-if="debugResultError" class="cb-memory-ops__warn" role="alert" data-test="retrieval-result-error">
          {{ debugResultError }}
        </p>

        <div class="cb-memory-ops__metrics" data-test="retrieval-metrics">
          <MetricCard label="候选总数" :value="fmtOrNull(debugResult.candidates)" data-test="retrieval-candidates" />
          <MetricCard label="关键词候选" :value="fmtOrNull(debugResult.keyword_candidates)" data-test="retrieval-keyword" />
          <MetricCard label="语义候选" :value="fmtOrNull(debugResult.semantic_candidates)" data-test="retrieval-semantic" />
          <MetricCard label="合并候选" :value="fmtOrNull(debugResult.merged_candidates)" data-test="retrieval-merged" />
          <MetricCard label="注入" :value="fmtOrNull(debugResult.injected)" data-test="retrieval-injected" />
          <MetricCard label="守卫丢弃" :value="fmtOrNull(debugResult.dropped_by_guard)" data-test="retrieval-dropped" />
          <MetricCard label="最高分" :value="fmtOrNull(debugResult.top_score)" data-test="retrieval-top-score" />
          <MetricCard label="耗时 (ms)" :value="fmtOrNull(debugResult.duration_ms)" data-test="retrieval-duration" />
          <MetricCard
            label="语义可用"
            :value="debugResult.semantic_available === undefined ? null : debugResult.semantic_available ? '是' : '否'"
            data-test="retrieval-semantic-available"
          />
        </div>

        <ol v-if="debugRows.length > 0" class="cb-memory-ops__hits" data-test="retrieval-list">
          <li
            v-for="(row, index) in debugRows"
            :key="row.id ?? index"
            class="cb-memory-ops__hit"
            data-test="retrieval-item"
          >
            <div class="cb-memory-ops__hit-head">
              <span class="cb-memory-ops__hit-rank">#{{ index + 1 }}</span>
              <span class="cb-memory-ops__hit-score" data-test="retrieval-final">final {{ fmt(row.final) }}</span>
              <span class="cb-caption">{{ originLabel(row.origin) }}</span>
            </div>
            <p class="cb-memory-ops__hit-content">{{ row.content }}</p>
            <p class="cb-memory-ops__hit-meta">
              {{ fmt(row.layer) }} · {{ fmt(row.category) }} · {{ fmt(row.scope_key) }} · {{ fmt(row.status) }}
            </p>
            <p class="cb-memory-ops__hit-components" data-test="retrieval-components">
              语义 {{ fmt(row.semantic) }} · 关键词 {{ fmt(row.keyword) }} · 重要度 {{ fmt(row.importance) }} ·
              置信 {{ fmt(row.confidence) }} · 时效 {{ fmt(row.recency) }} · 关系 {{ fmt(row.relationship) }} ·
              主题 {{ fmt(row.topic_bonus) }} · 时序 {{ fmt(row.temporal) }}
            </p>
          </li>
        </ol>
        <p v-else class="cb-muted" data-test="retrieval-no-hits">
          这次查询没有命中任何记忆（可能被相关性守卫丢弃）。
        </p>

        <details class="cb-memory-ops__raw" data-test="retrieval-raw">
          <summary>原始 JSON</summary>
          <pre class="cb-memory-ops__raw-body">{{ debugRaw }}</pre>
        </details>
      </template>

      <p v-else class="cb-muted" data-test="retrieval-empty">
        输入查询词后点击「运行检索」，这里会显示打分链路。
      </p>
    </section>

    <ConfirmDialog
      v-model:show="showClearConfirm"
      title="清空向量缓存"
      message="确定清空 embedding 服务的向量缓存吗？"
      detail="只清除进程内的向量缓存；记忆本身与已落库的向量不受影响，下次检索会重新计算。"
      confirm-text="清空"
      danger
      @confirm="confirmClearCache"
    />
  </div>
</template>

<style scoped>
.cb-memory-ops {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-memory-ops__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-memory-ops__subtitle {
  margin-top: var(--cb-space-2);
  padding-top: var(--cb-space-3);
  border-top: 1px solid var(--cb-border);
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.cb-memory-ops__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--cb-space-3);
}

.cb-memory-ops__actions,
.cb-memory-ops__run,
.cb-memory-ops__form {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--cb-space-2);
}

.cb-memory-ops__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 180px;
}

.cb-memory-ops__field--grow {
  flex: 1;
  min-width: 240px;
}

.cb-memory-ops__input {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-memory-ops__button {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
  white-space: nowrap;
}

.cb-memory-ops__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-memory-ops__button:disabled {
  color: var(--cb-text-faint);
  cursor: not-allowed;
}

.cb-memory-ops__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.cb-memory-ops__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-memory-ops__notice {
  color: var(--cb-text-muted);
}

.cb-memory-ops__warn {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-memory-ops__report {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
}

.cb-memory-ops__report-title {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.cb-memory-ops__report-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: var(--cb-space-2);
  margin: 0;
}

.cb-memory-ops__report-grid dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.cb-memory-ops__report-grid dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  font-variant-numeric: tabular-nums;
}

.cb-memory-ops__hits {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-memory-ops__hit {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
}

.cb-memory-ops__hit-head {
  display: flex;
  align-items: baseline;
  gap: var(--cb-space-2);
}

.cb-memory-ops__hit-rank {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
}

.cb-memory-ops__hit-score {
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  font-variant-numeric: tabular-nums;
}

.cb-memory-ops__hit-content {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-memory-ops__hit-meta,
.cb-memory-ops__hit-components {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}

.cb-memory-ops__raw {
  border-top: 1px solid var(--cb-border);
  padding-top: var(--cb-space-2);
}

.cb-memory-ops__raw summary {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-memory-ops__raw-body {
  max-height: 320px;
  overflow: auto;
  margin: var(--cb-space-2) 0 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
