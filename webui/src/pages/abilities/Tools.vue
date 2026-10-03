<script setup lang="ts">
/**
 * 工具页（W5 §34-§38）：注册表清单 + 策略摘要 + 权限规则 + 决策预览。
 *
 * 本页不执行任何工具；「决策预览」只读打分结果，绝不触发外部请求。
 * 缓存的清理由服务端确认（confirm=clear），页面上仍需二次确认。
 */
import { computed, onMounted, ref } from 'vue'

import { toolsApi, type ToolListPayload } from '@/api/abilities'
import { errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import MetricCard from '@/components/MetricCard.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ToolCard from '@/components/domain/ToolCard.vue'
import { toast } from '@/composables/toast'
import type { ToolRow } from '@/types/domain'

interface PermissionRule {
  scope: string
  ref: string
  tool_name: string
  allowed: boolean
  created_at?: number | null
}

interface CandidateRow {
  name: string
  score: number
  enabled: boolean
  risk_level: string
}

const loading = ref(false)
const error = ref('')
const payload = ref<ToolListPayload | null>(null)

const permissions = ref<PermissionRule[]>([])
const permissionsError = ref('')

const formScope = ref<'user' | 'group'>('user')
const formScopeId = ref('')
const formTool = ref('')
const formAllowed = ref(true)
const savingPermission = ref(false)

const deleteTarget = ref<PermissionRule | null>(null)
const showDeleteConfirm = ref(false)
const deleting = ref(false)

const previewText = ref('')
const previewLoading = ref(false)
const previewError = ref('')
const preview = ref<Record<string, unknown> | null>(null)

const showCacheConfirm = ref(false)
const clearingCache = ref(false)

const tools = computed<ToolRow[]>(() => payload.value?.items ?? [])
const stats = computed<Record<string, unknown>>(() => payload.value?.stats ?? {})
const policy = computed<Record<string, unknown>>(() => payload.value?.policy ?? {})

const previewCandidates = computed<CandidateRow[]>(() => {
  const rows = preview.value?.candidates
  if (!Array.isArray(rows)) return []
  return rows as CandidateRow[]
})

const previewSelected = computed<string>(() => {
  const selected = preview.value?.selected
  return typeof selected === 'string' && selected ? selected : '—'
})

const previewRejected = computed<Record<string, unknown>[]>(() => {
  const rows = preview.value?.rejected
  return Array.isArray(rows) ? (rows as Record<string, unknown>[]) : []
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

function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function metricValue(value: unknown): string | number | null {
  if (typeof value === 'number' || typeof value === 'string') return value
  return null
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    payload.value = await toolsApi.list()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

async function loadPermissions(): Promise<void> {
  permissionsError.value = ''
  try {
    const data = await toolsApi.permissions()
    permissions.value = (data.items ?? []) as unknown as PermissionRule[]
  } catch (caught) {
    permissionsError.value = errorMessage(caught)
  }
}

async function savePermission(): Promise<void> {
  const scopeId = formScopeId.value.trim()
  const tool = formTool.value || tools.value[0]?.name || ''
  if (!scopeId) {
    toast.warning('请填写用户 / 群 ID')
    return
  }
  if (!tool) {
    toast.warning('请选择工具')
    return
  }
  savingPermission.value = true
  try {
    await toolsApi.setPermission({
      scope: formScope.value,
      scope_id: scopeId,
      tool,
      allowed: formAllowed.value,
    })
    toast.success('权限规则已保存', `${formScope.value}:${scopeId} → ${tool}`)
    formScopeId.value = ''
    await loadPermissions()
  } catch (caught) {
    toast.error('保存权限失败', errorMessage(caught))
  } finally {
    savingPermission.value = false
  }
}

function startDelete(rule: PermissionRule): void {
  deleteTarget.value = rule
  showDeleteConfirm.value = true
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  deleting.value = true
  try {
    await toolsApi.clearPermission(target.scope, target.ref, target.tool_name)
    toast.success('权限规则已删除', `${target.scope}:${target.ref} → ${target.tool_name}`)
    await loadPermissions()
  } catch (caught) {
    toast.error('删除权限失败', errorMessage(caught))
  } finally {
    deleting.value = false
  }
}

async function runPreview(): Promise<void> {
  const text_ = previewText.value.trim()
  if (!text_) {
    toast.warning('请输入一段文本')
    return
  }
  previewLoading.value = true
  previewError.value = ''
  try {
    preview.value = await toolsApi.decisionDebug(text_)
  } catch (caught) {
    previewError.value = errorMessage(caught)
    preview.value = null
  } finally {
    previewLoading.value = false
  }
}

async function confirmClearCache(): Promise<void> {
  showCacheConfirm.value = false
  clearingCache.value = true
  try {
    const result = await toolsApi.clearCache()
    toast.success('缓存已清空', `共清理 ${result.cleared ?? 0} 条（${result.tool || '全部工具'}）`)
  } catch (caught) {
    toast.error('清空缓存失败', errorMessage(caught))
  } finally {
    clearingCache.value = false
  }
}

onMounted(() => {
  void load()
  void loadPermissions()
})
</script>

<template>
  <div class="tools" data-test="abilities-tools">
    <section class="tools__section">
      <SectionHeader
        title="工具清单"
        description="这里只展示注册表与统计；启用 / 停用经服务端确认后刷新。"
      />

      <ErrorState v-if="error" :message="error" @retry="load" />
      <LoadingState v-else-if="loading && tools.length === 0" label="正在读取工具注册表…" :rows="4" />
      <EmptyState
        v-else-if="tools.length === 0"
        title="没有注册任何工具"
        description="工具由后端注册表决定；这里不会凭空列出工具。"
      />
      <div v-else class="tools__grid">
        <ToolCard v-for="tool in tools" :key="tool.name" :tool="tool" @updated="load" />
      </div>
    </section>

    <section class="tools__section cb-card" data-test="tools-policy">
      <SectionHeader
        title="策略摘要"
        description="风险等级与限流由后端策略决定，界面只如实展示。"
      />
      <dl class="tools__policy">
        <div class="tools__policy-item">
          <dt>允许的风险等级</dt>
          <dd data-test="policy-risk">{{ describe(policy.allowed_risk_levels) }}</dd>
        </div>
        <div class="tools__policy-item">
          <dt>限流</dt>
          <dd data-test="policy-rate-limit">{{ describe(policy.rate_limit) }}</dd>
        </div>
        <div class="tools__policy-item">
          <dt>每回合最大调用</dt>
          <dd>{{ describe(stats.max_calls_per_turn) }}</dd>
        </div>
        <div class="tools__policy-item">
          <dt>决策模式</dt>
          <dd>{{ describe(stats.decision_mode) }}</dd>
        </div>
      </dl>
      <div class="tools__metrics">
        <MetricCard label="工具总数" :value="metricValue(stats.total)" />
        <MetricCard label="已启用" :value="metricValue(stats.enabled_count)" />
        <MetricCard label="调用次数" :value="metricValue(stats.calls)" />
        <MetricCard label="失败次数" :value="metricValue(stats.failure)" state="warn" />
        <MetricCard label="缓存命中" :value="metricValue(stats.cache_hits)" />
      </div>
      <div class="tools__cache">
        <p class="cb-caption">
          清空工具缓存只影响执行结果缓存，不会停用工具、也不会修改任何配置。
        </p>
        <button
          type="button"
          class="tools__button tools__button--danger"
          :disabled="clearingCache"
          data-test="tools-clear-cache"
          @click="showCacheConfirm = true"
        >
          {{ clearingCache ? '正在清理…' : '清空工具缓存' }}
        </button>
      </div>
    </section>

    <section class="tools__section cb-card" data-test="tools-permissions">
      <SectionHeader
        title="权限"
        description="按用户 / 群限制工具可用性；拒绝优先。删除规则需要确认。"
      />

      <ErrorState v-if="permissionsError" :message="permissionsError" @retry="loadPermissions" />
      <p v-else-if="permissions.length === 0" class="cb-muted" data-test="permissions-empty">
        还没有权限规则：所有工具按默认策略对所有用户 / 群生效。
      </p>
      <table v-else class="tools__table" data-test="permissions-table">
        <thead>
          <tr>
            <th scope="col">范围</th>
            <th scope="col">对象</th>
            <th scope="col">工具</th>
            <th scope="col">结果</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="rule in permissions" :key="`${rule.scope}-${rule.ref}-${rule.tool_name}`" data-test="permission-row">
            <td>{{ rule.scope === 'user' ? '用户' : rule.scope === 'group' ? '群' : rule.scope }}</td>
            <td>{{ rule.ref }}</td>
            <td>{{ rule.tool_name }}</td>
            <td>{{ rule.allowed ? '允许' : '拒绝' }}</td>
            <td>
              <button
                type="button"
                class="tools__button"
                data-test="permission-delete"
                @click="startDelete(rule)"
              >
                删除
              </button>
            </td>
          </tr>
        </tbody>
      </table>

      <form class="tools__form" data-test="permission-form" @submit.prevent="savePermission">
        <label class="tools__field">
          <span>范围</span>
          <select v-model="formScope" data-test="permission-scope">
            <option value="user">用户</option>
            <option value="group">群</option>
          </select>
        </label>
        <label class="tools__field">
          <span>用户 / 群 ID</span>
          <input v-model="formScopeId" type="text" placeholder="例如 10001" data-test="permission-scope-id" />
        </label>
        <label class="tools__field">
          <span>工具</span>
          <select v-model="formTool" data-test="permission-tool">
            <option v-for="tool in tools" :key="tool.name" :value="tool.name">
              {{ tool.display_name || tool.name }}
            </option>
          </select>
        </label>
        <label class="tools__field">
          <span>结果</span>
          <select v-model="formAllowed" data-test="permission-allowed">
            <option :value="true">允许</option>
            <option :value="false">拒绝</option>
          </select>
        </label>
        <button type="submit" class="tools__button tools__button--primary" :disabled="savingPermission" data-test="permission-save">
          {{ savingPermission ? '保存中…' : '保存规则' }}
        </button>
      </form>
    </section>

    <section class="tools__section cb-card" data-test="tools-decision">
      <SectionHeader
        title="决策预览"
        description="只读展示工具选择打分，绝不执行工具、绝不发起外部请求。"
      />
      <form class="tools__preview-form" data-test="decision-form" @submit.prevent="runPreview">
        <input
          v-model="previewText"
          type="text"
          class="tools__preview-input"
          placeholder="输入一句话，例如：明天上海天气怎么样？"
          aria-label="决策预览文本"
          data-test="decision-input"
        />
        <button type="submit" class="tools__button tools__button--primary" :disabled="previewLoading" data-test="decision-run">
          {{ previewLoading ? '分析中…' : '预览' }}
        </button>
      </form>

      <ErrorState v-if="previewError" :message="previewError" @retry="runPreview" />
      <div v-else-if="preview" class="tools__preview" data-test="decision-result">
        <p class="cb-caption">
          决策模式：{{ describe(preview.decision_mode) }}；选中的工具：<strong data-test="decision-selected">{{ previewSelected }}</strong>
        </p>
        <p class="cb-caption">候选：</p>
        <ul v-if="previewCandidates.length > 0" class="tools__candidate-list">
          <li v-for="candidate in previewCandidates" :key="candidate.name" data-test="decision-candidate">
            {{ candidate.name }}（分数 {{ candidate.score }}，{{ candidate.enabled ? '已启用' : '已停用' }}，风险
            {{ candidate.risk_level || '—' }}）
          </li>
        </ul>
        <p v-else class="cb-muted">没有命中任何候选。</p>
        <p v-if="previewRejected.length > 0" class="cb-caption">被拒绝：</p>
        <ul v-if="previewRejected.length > 0" class="tools__candidate-list tools__candidate-list--muted">
          <li v-for="(item, index) in previewRejected" :key="index">
            {{ text(item.name) }}（{{ text(item.reason) }}）
          </li>
        </ul>
        <pre v-if="typeof preview.instruction_preview === 'string' && preview.instruction_preview" class="tools__instruction">{{ preview.instruction_preview }}</pre>
      </div>
      <p v-else class="cb-muted" data-test="decision-empty">输入文本后点击「预览」，这里会显示候选与选中结果。</p>
    </section>

    <ConfirmDialog
      v-model:show="showDeleteConfirm"
      title="删除权限规则"
      :message="deleteTarget ? `确定删除 ${deleteTarget.scope}:${deleteTarget.ref} → ${deleteTarget.tool_name} 吗？` : ''"
      detail="删除后该对象回落到默认策略；不会删除工具本身。"
      confirm-text="删除"
      danger
      @confirm="confirmDelete"
    />

    <ConfirmDialog
      v-model:show="showCacheConfirm"
      title="清空工具缓存"
      message="确定清空工具执行缓存吗？"
      detail="只会清空缓存条目；不会停用工具、不会修改配置、不会删除服务器日志。"
      confirm-text="清空"
      danger
      @confirm="confirmClearCache"
    />
  </div>
</template>

<style scoped>
.tools {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.tools__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.tools__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--cb-space-3);
}

.tools__policy {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.tools__policy-item dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.tools__policy-item dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.tools__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3);
}

.tools__cache {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
  padding-top: var(--cb-space-3);
  border-top: 1px solid var(--cb-border);
}

.tools__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.tools__table th,
.tools__table td {
  padding: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
}

.tools__table th {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  font-weight: 500;
}

.tools__form {
  display: flex;
  align-items: flex-end;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
  padding-top: var(--cb-space-3);
  border-top: 1px solid var(--cb-border);
}

.tools__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.tools__field input,
.tools__field select {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.tools__preview-form {
  display: flex;
  gap: var(--cb-space-2);
}

.tools__preview-input {
  flex: 1;
  min-width: 0;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.tools__candidate-list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  margin: 0;
  padding-left: var(--cb-space-4);
  font-size: var(--cb-text-sm);
}

.tools__candidate-list--muted {
  color: var(--cb-text-muted);
}

.tools__instruction {
  max-height: 220px;
  overflow: auto;
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tools__button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
  white-space: nowrap;
}

.tools__button:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.tools__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.tools__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.tools__button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
