<script setup lang="ts">
/**
 * 工具详情（W5 §35-§37）：schema / 文档 / 设置 / 指标 / 最近执行。
 *
 * 唯一的执行入口是「测试」：必须先经 ConfirmDialog 明确说明这会真的发起一次
 * 外部请求（不会发 QQ 消息），确认后才 POST `/{name}/test`（body 带 confirm）。
 * 其余操作（改设置）只走 `toolsApi.update`，绝不从页面执行工具（§93）。
 */
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { toolsApi } from '@/api/abilities'
import { ApiError, errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type { ToolDetail, ToolTestResult } from '@/types/domain'

const route = useRoute()

const RISK_LABELS: Record<string, string> = { low: '低风险', medium: '中风险', high: '高风险' }
const RISK_STATES: Record<string, StatusState> = { low: 'ok', medium: 'warn', high: 'error' }

const detail = ref<ToolDetail | null>(null)
const loading = ref(false)
const loadError = ref('')
const notFound = ref(false)

const timeoutInput = ref('')
const cacheInput = ref('')
const saving = ref(false)

const showTestConfirm = ref(false)
const testing = ref(false)
const testResult = ref<ToolTestResult | null>(null)
const testError = ref('')

const name = computed(() => String(route.params.name ?? ''))

const riskLabel = computed(() => {
  const level = detail.value?.risk_level ?? ''
  return RISK_LABELS[level] ?? (level || '未知')
})
const riskState = computed<StatusState>(() => RISK_STATES[detail.value?.risk_level ?? ''] ?? 'idle')

const credentialText = computed(() => {
  if (!detail.value) return '—'
  if (detail.value.has_credential === null) return '—'
  return detail.value.has_credential ? '已就绪' : '缺失'
})

const permissionRules = computed<Record<string, unknown>[]>(() => {
  const rules = detail.value?.permissions_summary?.rules
  return Array.isArray(rules) ? (rules as Record<string, unknown>[]) : []
})

const metricEntries = computed<[string, string][]>(() => {
  const entries = Object.entries(detail.value?.metrics ?? {})
  return entries.map(([key, value]) => [key, describe(value)])
})

function describe(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length > 0 ? value.map(String).join('、') : '—'
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

function stringify(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return '{}'
  }
}

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function formatTimestamp(value: unknown): string {
  const seconds = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(seconds) || seconds <= 0) return '—'
  const millis = seconds > 1e12 ? seconds : seconds * 1000
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value_: number): string => String(value_).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  notFound.value = false
  testResult.value = null
  testError.value = ''
  try {
    const data = await toolsApi.detail(name.value)
    detail.value = data
    timeoutInput.value = data.timeout === null || data.timeout === undefined ? '' : String(data.timeout)
    cacheInput.value =
      data.cache_ttl_seconds === null || data.cache_ttl_seconds === undefined
        ? ''
        : String(data.cache_ttl_seconds)
  } catch (caught) {
    detail.value = null
    if (caught instanceof ApiError && caught.status === 404) {
      notFound.value = true
    } else {
      loadError.value = errorMessage(caught)
    }
  } finally {
    loading.value = false
  }
}

async function saveSettings(): Promise<void> {
  const timeout = Number(timeoutInput.value)
  const cacheTtl = Number(cacheInput.value)
  if (!Number.isFinite(timeout) || timeout <= 0) {
    toast.warning('timeout 必须是大于 0 的数字（秒）')
    return
  }
  if (!Number.isFinite(cacheTtl) || cacheTtl < 0) {
    toast.warning('缓存 TTL 必须是不小于 0 的数字（秒）')
    return
  }
  saving.value = true
  try {
    // cache_ttl_seconds 是后端 PATCH 的合法字段（与 timeout 一起热应用）
    const payload = { timeout, cache_ttl_seconds: cacheTtl }
    const updated = await toolsApi.update(name.value, payload)
    detail.value = updated
    if (updated.restart_required) {
      toast.warning('设置已保存', '服务端标记为重启后生效')
    } else {
      toast.success('设置已保存', `已应用：${(updated.applied ?? []).join('、') || '无'}`)
    }
  } catch (caught) {
    toast.error('保存设置失败', errorMessage(caught))
  } finally {
    saving.value = false
  }
}

async function runTest(): Promise<void> {
  showTestConfirm.value = false
  testing.value = true
  testError.value = ''
  testResult.value = null
  try {
    testResult.value = await toolsApi.test(name.value)
  } catch (caught) {
    testError.value = errorMessage(caught)
  } finally {
    testing.value = false
  }
}

watch(name, () => void load(), { immediate: true })
</script>

<template>
  <div class="tool-detail" data-test="tool-detail">
    <ErrorState
      v-if="notFound"
      :message="`工具不存在或已被移除：${name}`"
      detail="请回到「工具」列表选择仍然注册的工具。"
    />
    <ErrorState v-else-if="loadError" :message="loadError" @retry="load" />
    <LoadingState v-else-if="loading && !detail" label="正在读取工具详情…" :rows="5" />

    <template v-else-if="detail">
      <section class="tool-detail__section cb-card" data-test="tool-overview">
        <SectionHeader :title="detail.display_name || detail.name" :description="detail.description || '（无描述）'">
          <template #actions>
            <StatusBadge :state="riskState" :label="riskLabel" />
          </template>
        </SectionHeader>
        <dl class="tool-detail__facts">
          <div><dt>内部名</dt><dd><code>{{ detail.name }}</code></dd></div>
          <div><dt>类别</dt><dd>{{ detail.category || '—' }}</dd></div>
          <div><dt>启用</dt><dd>{{ detail.enabled ? '已启用' : '已停用' }}</dd></div>
          <div><dt>凭据</dt><dd>{{ credentialText }}</dd></div>
        </dl>
        <div v-if="detail.when_to_use" class="tool-detail__block">
          <h3 class="tool-detail__label">什么时候使用</h3>
          <p class="tool-detail__text">{{ detail.when_to_use }}</p>
        </div>
        <div v-if="detail.when_not_to_use" class="tool-detail__block">
          <h3 class="tool-detail__label">什么时候不要使用</h3>
          <p class="tool-detail__text">{{ detail.when_not_to_use }}</p>
        </div>
        <div v-if="detail.limitations" class="tool-detail__block">
          <h3 class="tool-detail__label">限制</h3>
          <p class="tool-detail__text">{{ detail.limitations }}</p>
        </div>
      </section>

      <section class="tool-detail__section cb-card" data-test="tool-schemas">
        <SectionHeader title="输入 / 输出 schema" description="来自注册表的原始 JSON Schema，只读。" />
        <div class="tool-detail__schema-grid">
          <div>
            <h3 class="tool-detail__label">输入</h3>
            <pre class="tool-detail__pre" data-test="tool-input-schema">{{ stringify(detail.input_schema) }}</pre>
          </div>
          <div>
            <h3 class="tool-detail__label">输出</h3>
            <pre class="tool-detail__pre" data-test="tool-output-schema">{{ stringify(detail.output_schema) }}</pre>
          </div>
        </div>
      </section>

      <section class="tool-detail__section cb-card" data-test="tool-settings">
        <SectionHeader title="设置" description="timeout 与缓存 TTL 改动会热应用；凭据只以脱敏形式展示。" />
        <form class="tool-detail__form" data-test="tool-settings-form" @submit.prevent="saveSettings">
          <label class="tool-detail__field">
            <span>timeout（秒）</span>
            <input v-model="timeoutInput" type="number" min="0" step="0.1" data-test="tool-timeout" />
          </label>
          <label class="tool-detail__field">
            <span>缓存 TTL（秒）</span>
            <input v-model="cacheInput" type="number" min="0" step="1" data-test="tool-cache-ttl" />
          </label>
          <button type="submit" class="tool-detail__button tool-detail__button--primary" :disabled="saving" data-test="tool-save-settings">
            {{ saving ? '保存中…' : '保存设置' }}
          </button>
        </form>
        <button
          type="button"
          class="tool-detail__button"
          :disabled="testing"
          data-test="tool-test"
          @click="showTestConfirm = true"
        >
          {{ testing ? '测试中…' : '测试工具' }}
        </button>
      </section>

      <section v-if="testError || testResult" class="tool-detail__section cb-card" data-test="tool-test-result">
        <SectionHeader title="测试结果" description="测试是诊断操作：会真的发起一次外部请求，但不会发送 QQ 消息。" />
        <ErrorState v-if="testError" :message="testError" />
        <template v-else-if="testResult">
          <div class="tool-detail__result-head">
            <StatusBadge
              :state="testResult.ok ? 'ok' : 'error'"
              :label="testResult.ok ? '成功' : '失败'"
            />
            <span class="cb-caption" data-test="tool-test-duration">耗时 {{ testResult.duration_ms }} ms</span>
          </div>
          <p v-if="testResult.may_have_called_external" class="tool-detail__warning" data-test="tool-test-external">
            这次测试可能真的调用了外部服务。
          </p>
          <p v-if="testResult.error" class="tool-detail__text" data-test="tool-test-error">
            错误：{{ testResult.error }}
          </p>
          <pre v-if="testResult.ok" class="tool-detail__pre" data-test="tool-test-data">{{ stringify(testResult.result) }}</pre>
          <p v-if="testResult.note" class="cb-caption">{{ testResult.note }}</p>
        </template>
      </section>

      <section class="tool-detail__section cb-card" data-test="tool-metrics">
        <SectionHeader title="指标" description="执行器统计；没有数据时显示「—」。" />
        <dl v-if="metricEntries.length > 0" class="tool-detail__facts">
          <div v-for="[key, value] in metricEntries" :key="key">
            <dt>{{ key }}</dt>
            <dd>{{ value }}</dd>
          </div>
        </dl>
        <p v-else class="cb-muted">该工具还没有执行指标。</p>
      </section>

      <section class="tool-detail__section cb-card" data-test="tool-permissions">
        <SectionHeader
          title="权限摘要"
          :description="`共 ${String(detail.permissions_summary?.count ?? 0)} 条规则；拒绝优先。`"
        />
        <ul v-if="permissionRules.length > 0" class="tool-detail__rules">
          <li v-for="(rule, index) in permissionRules" :key="index">
            {{ text(rule.scope) }}:{{ text(rule.ref) }} → {{ rule.allowed ? '允许' : '拒绝' }}
          </li>
        </ul>
        <p v-else class="cb-muted">没有针对该工具的专门规则。</p>
      </section>

      <section class="tool-detail__section cb-card" data-test="tool-executions">
        <SectionHeader title="最近执行" description="永不回显原始参数；这里只展示状态、耗时与摘要。" />
        <table v-if="(detail.recent_executions ?? []).length > 0" class="tool-detail__table">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">状态</th>
              <th scope="col">缓存</th>
              <th scope="col">耗时（ms）</th>
              <th scope="col">错误</th>
              <th scope="col">摘要</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in detail.recent_executions ?? []" :key="index" data-test="execution-row">
              <td>{{ formatTimestamp(row.created_at) }}</td>
              <td>{{ text(row.status) }}</td>
              <td>{{ row.cache_hit ? '命中' : '未命中' }}</td>
              <td>{{ text(row.duration_ms) }}</td>
              <td>{{ text(row.error_type) }}</td>
              <td>{{ text(row.result_summary) }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="cb-muted">还没有执行记录。</p>
      </section>
    </template>

    <ConfirmDialog
      v-model:show="showTestConfirm"
      title="测试工具"
      message="这会真的发起一次外部请求（例如搜索 / 天气），不会发送 QQ 消息。"
      :detail="`确认后将以管理员身份执行「${detail?.display_name || name}」，结果只显示在本页。`"
      confirm-text="执行测试"
      danger
      @confirm="runTest"
    />
  </div>
</template>

<style scoped>
.tool-detail {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.tool-detail__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.tool-detail__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.tool-detail__facts dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.tool-detail__facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.tool-detail__block {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.tool-detail__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.tool-detail__text {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.tool-detail__schema-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--cb-space-3);
}

.tool-detail__pre {
  max-height: 260px;
  overflow: auto;
  margin: var(--cb-space-1) 0 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tool-detail__form {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.tool-detail__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.tool-detail__field input {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  width: 140px;
}

.tool-detail__button {
  align-self: flex-start;
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.tool-detail__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.tool-detail__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.tool-detail__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tool-detail__result-head {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.tool-detail__warning {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.tool-detail__rules {
  margin: 0;
  padding-left: var(--cb-space-4);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.tool-detail__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.tool-detail__table th,
.tool-detail__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
}

.tool-detail__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}
</style>
