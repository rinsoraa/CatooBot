<script setup lang="ts">
/**
 * 模型页（W4 §18-§26）：桌面表格 / 窄屏卡片，状态（就绪 / 冷却 / 停用 / 错误）
 * 只用文本来表达；启用开关直接写服务端并回读，不做乐观更新（§76）。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import TestResultPanel from '@/components/ai/TestResultPanel.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import { roleDescriptor, type ModelItem, type TestResult } from '@/types/ai'

const aiStore = useAiStore()
const router = useRouter()
const route = useRoute()

const MODEL_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/
const DEFAULT_TEST_PROMPT = '你好，请简单回复一句“测试成功”。'

// 窄屏用卡片、桌面用表格（§82）；宽度变化时切换渲染分支。
const isNarrow = ref(false)
let mediaQuery: MediaQueryList | null = null

function syncNarrow(event?: MediaQueryListEvent): void {
  isNarrow.value = event ? event.matches : Boolean(mediaQuery?.matches)
}

// ------------------------------------------------------------- 冷却倒计时
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

// 冷却剩余：后端给剩余秒数（路由器时钟是单调时钟），本地只做视觉递减。
function cooldownRemaining(model: ModelItem): number {
  const remaining = model.cooldown_remaining_seconds || 0
  if (!remaining) return 0
  const loadedAt = aiStore.lastLoadedAt || now.value
  const elapsed = Math.max(0, (now.value - loadedAt) / 1000)
  return Math.max(0, Math.ceil(remaining - elapsed))
}

// ------------------------------------------------------------------ 状态
function modelState(model: ModelItem): { state: StatusState; label: string } {
  if (!model.enabled) return { state: 'off', label: '已停用' }
  if (model.in_cooldown) {
    return { state: 'warn', label: `冷却中（约 ${cooldownRemaining(model)} 秒）` }
  }
  if (model.last_error || model.failure_count > 0) {
    return { state: 'error', label: `⚠ 错误${model.failure_count > 0 ? `（连续失败 ${model.failure_count} 次）` : ''}` }
  }
  return { state: 'ok', label: '就绪' }
}

function roleLabels(roles: string[]): string[] {
  return roles.map((role) => roleDescriptor(role)?.label ?? role)
}

function usageText(model: ModelItem): string {
  return `${model.usage.calls} 次调用 / ${model.usage.failures} 次失败`
}

function usageHint(model: ModelItem): string {
  if (model.usage.calls === 0) return ''
  return `平均 ${model.usage.avg_latency_ms} ms`
}

// ------------------------------------------------------------------ 加载
const showLoading = computed(() => aiStore.loading && aiStore.models.length === 0)

// ------------------------------------------------------------------ 启用开关
const toggling = reactive<Record<string, boolean>>({})

async function toggleEnabled(model: ModelItem): Promise<void> {
  if (toggling[model.name]) return
  toggling[model.name] = true
  const ok = await aiStore.saveModel(model.name, { enabled: !model.enabled })
  toggling[model.name] = false
  if (ok) {
    toast.success(model.enabled ? `已停用「${model.name}」` : `已启用「${model.name}」`)
  } else {
    toast.error('切换失败', aiStore.error)
  }
}

// ------------------------------------------------------------------ 测试
const testTarget = ref<ModelItem | null>(null)
const testPrompt = ref(DEFAULT_TEST_PROMPT)
const testLoading = ref(false)
const testResult = ref<TestResult | null>(null)

function openTest(model: ModelItem): void {
  testTarget.value = model
  testPrompt.value = DEFAULT_TEST_PROMPT
  testResult.value = null
  testLoading.value = false
}

function closeTest(): void {
  testTarget.value = null
  testResult.value = null
  testLoading.value = false
}

async function runTest(): Promise<void> {
  const target = testTarget.value
  if (!target || testLoading.value) return
  testLoading.value = true
  testResult.value = null
  const result = await aiStore.testModel(target.name, testPrompt.value)
  testLoading.value = false
  testResult.value = result
  if (!result) toast.error('测试失败', aiStore.error)
}

// ------------------------------------------------------------------ 表单
const showForm = ref(false)
const editingName = ref('')
const saving = ref(false)
const formError = ref('')
const fieldErrors = reactive<Record<string, string>>({})
const form = reactive({ name: '', provider: '', model: '', enabled: true })

const isEditing = computed(() => editingName.value.length > 0)

/** 下拉只列有 Key 的 Provider；编辑时保留当前值以免选项缺失。 */
const providerOptions = computed(() => {
  const names = [...aiStore.providerNames]
  if (isEditing.value && form.provider && !names.includes(form.provider)) names.unshift(form.provider)
  return names
})

const noProvider = computed(() => !isEditing.value && aiStore.providerNames.length === 0)

// 深链 ?create=1 可能在数据到达前就打开了表单：Provider 列表就绪后补默认值。
watch(
  () => aiStore.providerNames,
  (names) => {
    if (!isEditing.value && showForm.value && !form.provider && names.length > 0) {
      form.provider = names[0]
    }
  },
)

function clearFieldErrors(): void {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
}

function openCreate(): void {
  editingName.value = ''
  form.name = ''
  form.provider = aiStore.providerNames[0] ?? ''
  form.model = ''
  form.enabled = true
  formError.value = ''
  clearFieldErrors()
  showForm.value = true
}

function openEdit(model: ModelItem): void {
  editingName.value = model.name
  form.name = model.name
  form.provider = model.provider
  form.model = model.model
  form.enabled = model.enabled
  formError.value = ''
  clearFieldErrors()
  showForm.value = true
}

function closeForm(): void {
  showForm.value = false
  saving.value = false
}

function validate(): boolean {
  clearFieldErrors()
  if (!isEditing.value && !MODEL_NAME_PATTERN.test(form.name.trim())) {
    fieldErrors.name = '模型别名只允许字母、数字、下划线、点或短横线（不超过 64 位）'
  }
  if (!form.provider) fieldErrors.provider = '请选择 Provider'
  if (!form.model.trim()) fieldErrors.model = '必须填写服务商提供的模型 ID'
  return Object.keys(fieldErrors).length === 0
}

function inferField(message: string): string {
  if (message.includes('Provider') || message.includes('provider')) return 'provider'
  if (message.includes('模型 ID') || message.includes('model')) return 'model'
  if (message.includes('别名') || message.includes('名称')) return 'name'
  return ''
}

async function submit(): Promise<void> {
  if (saving.value) return
  formError.value = ''
  if (!validate()) return
  saving.value = true
  try {
    const name = form.name.trim()
    const ok = await aiStore.saveModel(name, {
      provider: form.provider,
      model: form.model.trim(),
      enabled: form.enabled,
    })
    if (!ok) {
      formError.value = aiStore.error
      const field = inferField(aiStore.error)
      if (field) fieldErrors[field] = aiStore.error
      return
    }
    toast.success(isEditing.value ? `已保存模型「${name}」` : `已创建模型「${name}」`)
    closeForm()
  } finally {
    saving.value = false
  }
}

// ------------------------------------------------------------------ 删除
const conflict = ref<{ name: string; message: string; roles: string[] } | null>(null)
const forceConfirm = ref(false)

const deleteTarget = ref<ModelItem | null>(null)
const deleteConfirm = ref(false)

/** 站内确认（§37）：删除不可恢复；有用途时说明受影响的绑定。 */
const deleteMessage = computed(() => {
  const target = deleteTarget.value
  if (!target) return ''
  const labels = roleLabels(target.roles)
  const affected = labels.length
    ? `该模型绑定了 ${labels.length} 个用途（${labels.join('、')}），需先改绑，或强制删除时解除绑定。`
    : ''
  return `删除后无法恢复。该模型将从配置中移除。${affected}`
})

function requestRemove(model: ModelItem): void {
  deleteTarget.value = model
  deleteConfirm.value = true
}

async function removeModel(): Promise<void> {
  const target = deleteTarget.value
  deleteTarget.value = null
  if (!target) return
  const ok = await aiStore.deleteModel(target.name)
  if (ok) {
    toast.success(`已删除模型「${target.name}」`)
    return
  }
  const roles = aiStore.models.find((item) => item.name === target.name)?.roles ?? target.roles
  if (roles.length > 0) {
    conflict.value = { name: target.name, message: aiStore.error, roles }
  } else {
    toast.error('删除失败', aiStore.error)
  }
}

async function forceRemove(): Promise<void> {
  const target = conflict.value
  if (!target) return
  const ok = await aiStore.deleteModel(target.name, true)
  forceConfirm.value = false
  if (ok) {
    conflict.value = null
    toast.success(`已强制删除模型「${target.name}」并解绑相关用途`)
  } else {
    toast.error('强制删除失败', aiStore.error)
  }
}

function viewRoles(): void {
  conflict.value = null
  void router.push('/ai/roles')
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 720px)')
  syncNarrow()
  if (typeof mediaQuery.addEventListener === 'function') {
    mediaQuery.addEventListener('change', syncNarrow)
  } else {
    mediaQuery.addListener(syncNarrow)
  }
  now.value = Date.now()
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
  if (!aiStore.lastLoadedAt && !aiStore.loading) void aiStore.loadAll()
  if (route.query.create === '1') openCreate()
})

onBeforeUnmount(() => {
  if (!mediaQuery) return
  if (typeof mediaQuery.removeEventListener === 'function') {
    mediaQuery.removeEventListener('change', syncNarrow)
  } else {
    mediaQuery.removeListener(syncNarrow)
  }
  if (timer !== null) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="ai-models" data-test="ai-models">
    <SectionHeader
      title="模型"
      description="显示别名与真实模型 ID 是两回事：别名用于绑定用途，Model ID 才是发给服务商的名字。"
    >
      <template #actions>
        <button type="button" class="ai-btn ai-btn--primary" data-test="add-model" @click="openCreate">
          + 添加模型
        </button>
      </template>
    </SectionHeader>

    <ErrorState v-if="aiStore.error && !conflict && !showForm && !testTarget" :message="aiStore.error" @retry="aiStore.loadAll()" />

    <LoadingState v-if="showLoading" label="正在读取模型…" :rows="3" />

    <EmptyState
      v-else-if="aiStore.models.length === 0"
      title="尚未配置模型"
      description="模型属于某个 Provider，添加后即可绑定聊天、视觉等用途。"
    >
      <button type="button" class="ai-btn ai-btn--primary" data-test="empty-add-model" @click="openCreate">
        添加模型
      </button>
    </EmptyState>

    <!-- 桌面：表格 -->
    <table v-else-if="!isNarrow" class="ai-models__table cb-card" data-test="models-table">
      <thead>
        <tr>
          <th scope="col">显示名称</th>
          <th scope="col">Model ID</th>
          <th scope="col">Provider</th>
          <th scope="col">启用</th>
          <th scope="col">优先级</th>
          <th scope="col">用途</th>
          <th scope="col">状态</th>
          <th scope="col">用量（近 7 天）</th>
          <th scope="col">最近错误</th>
          <th scope="col">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="model in aiStore.models" :key="model.name" :data-test="`model-row-${model.name}`">
          <td>{{ model.name }}</td>
          <td class="ai-models__mono">{{ model.model }}</td>
          <td>{{ model.provider }}</td>
          <td>
            <div class="ai-models__switch-cell">
              <button
                type="button"
                role="switch"
                class="ai-switch"
                :class="{ 'ai-switch--on': model.enabled }"
                :aria-checked="model.enabled ? 'true' : 'false'"
                :disabled="toggling[model.name]"
                :data-test="`toggle-${model.name}`"
                :aria-label="model.enabled ? `停用 ${model.name}` : `启用 ${model.name}`"
                @click="toggleEnabled(model)"
              >
                <span class="ai-switch__thumb" aria-hidden="true" />
              </button>
              <span class="cb-caption">{{ model.enabled ? '已启用' : '已停用' }}</span>
            </div>
          </td>
          <td>{{ model.order + 1 }}</td>
          <td>
            <template v-if="model.roles.length === 0">
              <span class="cb-faint">—</span>
            </template>
            <template v-else>
              <span v-for="label in roleLabels(model.roles)" :key="label" class="ai-tag">{{ label }}</span>
            </template>
          </td>
          <td>
            <StatusBadge :state="modelState(model).state" :label="modelState(model).label" />
          </td>
          <td>
            <span data-test="model-usage">{{ usageText(model) }}</span>
            <span v-if="usageHint(model)" class="cb-caption ai-models__usage-hint">{{ usageHint(model) }}</span>
          </td>
          <td class="ai-models__last-error" :title="model.last_error ?? ''">
            {{ model.last_error || '—' }}
          </td>
          <td>
            <div class="ai-models__row-actions">
              <button type="button" class="ai-btn" data-test="test-model" @click="openTest(model)">测试模型</button>
              <button type="button" class="ai-btn" data-test="edit-model" @click="openEdit(model)">编辑</button>
              <button type="button" class="ai-btn ai-btn--danger" data-test="delete-model" @click="requestRemove(model)">
                删除
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 窄屏：卡片 -->
    <div v-else class="ai-models__cards" data-test="models-cards">
      <article v-for="model in aiStore.models" :key="model.name" class="cb-card ai-model-card">
        <div class="ai-model-card__head">
          <h3 class="ai-model-card__name">{{ model.name }}</h3>
          <StatusBadge :state="modelState(model).state" :label="modelState(model).label" />
        </div>
        <dl class="ai-model-card__facts">
          <div><dt class="cb-caption">Model ID</dt><dd class="ai-models__mono">{{ model.model }}</dd></div>
          <div><dt class="cb-caption">Provider</dt><dd>{{ model.provider }}</dd></div>
          <div><dt class="cb-caption">优先级</dt><dd>{{ model.order + 1 }}</dd></div>
          <div><dt class="cb-caption">用量</dt><dd>{{ usageText(model) }}</dd></div>
        </dl>
        <p class="cb-caption">用途：<template v-if="model.roles.length">{{ roleLabels(model.roles).join('、') }}</template><template v-else>—</template></p>
        <p v-if="model.last_error" class="ai-model-card__error">{{ model.last_error }}</p>
        <div class="ai-models__row-actions">
          <button
            type="button"
            role="switch"
            class="ai-switch"
            :class="{ 'ai-switch--on': model.enabled }"
            :aria-checked="model.enabled ? 'true' : 'false'"
            :disabled="toggling[model.name]"
            :aria-label="model.enabled ? `停用 ${model.name}` : `启用 ${model.name}`"
            @click="toggleEnabled(model)"
          >
            <span class="ai-switch__thumb" aria-hidden="true" />
          </button>
          <button type="button" class="ai-btn" @click="openTest(model)">测试模型</button>
          <button type="button" class="ai-btn" @click="openEdit(model)">编辑</button>
          <button type="button" class="ai-btn ai-btn--danger" @click="requestRemove(model)">删除</button>
        </div>
      </article>
    </div>

    <!-- 添加 / 编辑表单 -->
    <div v-if="showForm" class="ai-modal" data-test="model-form">
      <div class="ai-modal__backdrop" aria-hidden="true" @click="closeForm" />
      <form class="ai-modal__panel" role="dialog" aria-modal="true" aria-label="模型表单" @submit.prevent="submit">
        <h2 class="ai-modal__title">{{ isEditing ? `编辑模型：${editingName}` : '添加模型' }}</h2>

        <EmptyState
          v-if="noProvider"
          title="请先创建 Provider"
          description="模型必须挂在有 API Key 的服务商下；先在服务商页完成配置。"
          data-test="model-no-provider"
        >
          <RouterLink class="ai-btn ai-btn--primary" to="/ai/providers?create=1" data-test="goto-providers">
            去创建 Provider
          </RouterLink>
        </EmptyState>

        <template v-else>
          <div class="ai-field">
            <label class="ai-field__label" for="model-name">显示名称（别名）</label>
            <input
              id="model-name"
              v-model="form.name"
              class="ai-field__input"
              data-test="model-name-input"
              :disabled="isEditing"
              placeholder="例如 fast"
            />
            <p class="ai-field__hint">别名用于绑定用途与排序；真正发给服务商的是下面的 Model ID。</p>
            <p v-if="fieldErrors.name" class="ai-field__error" role="alert" data-test="error-name">{{ fieldErrors.name }}</p>
          </div>

          <div class="ai-field">
            <label class="ai-field__label" for="model-provider">Provider</label>
            <select id="model-provider" v-model="form.provider" class="ai-field__input" data-test="model-provider-input">
              <option value="" disabled>请选择</option>
              <option v-for="name in providerOptions" :key="name" :value="name">{{ name }}</option>
            </select>
            <p class="ai-field__hint">只列出已配置 API Key 的服务商。</p>
            <p v-if="fieldErrors.provider" class="ai-field__error" role="alert" data-test="error-provider">{{ fieldErrors.provider }}</p>
          </div>

          <div class="ai-field">
            <label class="ai-field__label" for="model-id">Model ID</label>
            <input
              id="model-id"
              v-model="form.model"
              class="ai-field__input ai-field__input--mono"
              data-test="model-id-input"
              placeholder="例如 deepseek-chat"
            />
            <p class="ai-field__hint">服务商文档里的模型名（Model ID），不是显示别名。</p>
            <p v-if="fieldErrors.model" class="ai-field__error" role="alert" data-test="error-model">{{ fieldErrors.model }}</p>
          </div>

          <label class="ai-field__label ai-field__label--row" for="model-enabled">
            <input id="model-enabled" v-model="form.enabled" type="checkbox" data-test="model-enabled-input" />
            启用（创建后立即参与路由）
          </label>
        </template>

        <p v-if="formError" class="ai-form__error" role="alert" data-test="form-error">{{ formError }}</p>

        <div class="ai-modal__actions">
          <button type="button" class="ai-btn" data-test="form-cancel" @click="closeForm">取消</button>
          <button
            type="submit"
            class="ai-btn ai-btn--primary"
            :disabled="saving || noProvider"
            data-test="form-save"
          >
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 测试模型 -->
    <div v-if="testTarget" class="ai-modal" data-test="test-dialog">
      <div class="ai-modal__backdrop" aria-hidden="true" @click="closeTest" />
      <div class="ai-modal__panel" role="dialog" aria-modal="true" aria-label="测试模型">
        <h2 class="ai-modal__title">测试模型：{{ testTarget.name }}</h2>
        <div class="ai-field">
          <label class="ai-field__label" for="test-prompt">测试提示词</label>
          <textarea
            id="test-prompt"
            v-model="testPrompt"
            class="ai-field__input ai-field__textarea"
            data-test="test-prompt"
            rows="3"
          />
          <p class="ai-field__hint">测试走完整路由（含故障转移），不会写入记忆或对话。</p>
        </div>
        <div class="ai-modal__actions">
          <button type="button" class="ai-btn" data-test="test-cancel" @click="closeTest">关闭</button>
          <button type="button" class="ai-btn ai-btn--primary" :disabled="testLoading" data-test="test-run" @click="runTest">
            {{ testLoading ? '测试中…' : '开始测试' }}
          </button>
        </div>
        <TestResultPanel :result="testResult" :loading="testLoading" :title="`测试结果：${testTarget.name}`" />
      </div>
    </div>

    <!-- 删除冲突（ai.model_in_use） -->
    <div v-if="conflict" class="ai-modal" data-test="model-conflict">
      <div class="ai-modal__backdrop" aria-hidden="true" @click="conflict = null" />
      <div class="ai-modal__panel" role="dialog" aria-modal="true" aria-label="无法删除模型">
        <h2 class="ai-modal__title">无法删除模型「{{ conflict.name }}」</h2>
        <p class="ai-modal__message" data-test="conflict-message">{{ conflict.message }}</p>
        <p class="cb-caption">
          绑定的用途：{{ conflict.roles.map((role) => roleDescriptor(role)?.label ?? role).join('、') }}
        </p>
        <div class="ai-modal__actions">
          <button type="button" class="ai-btn" data-test="conflict-view-roles" @click="viewRoles">查看用途</button>
          <button type="button" class="ai-btn ai-btn--danger" data-test="conflict-force" @click="forceConfirm = true">
            强制删除
          </button>
        </div>
      </div>
    </div>

    <!-- 首次删除必须先经站内确认（§37/§38） -->
    <ConfirmDialog
      v-model="deleteConfirm"
      title="删除模型"
      :message="deleteMessage"
      detail="此操作不可恢复，确定继续吗？"
      confirm-text="删除"
      danger
      @confirm="removeModel"
    />

    <ConfirmDialog
      v-model="forceConfirm"
      title="强制删除模型"
      :message="`强制删除「${conflict?.name ?? ''}」会同时解绑它承担的全部用途。`"
      detail="此操作不可恢复，确定继续吗？"
      confirm-text="强制删除"
      danger
      @confirm="forceRemove"
    />
  </div>
</template>

<style scoped>
.ai-models {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.ai-models__table {
  width: 100%;
  border-collapse: collapse;
  padding: var(--cb-space-3);
  font-size: var(--cb-text-sm);
}

.ai-models__table th,
.ai-models__table td {
  padding: var(--cb-space-2) var(--cb-space-3);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  vertical-align: top;
}

.ai-models__table thead th {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  font-weight: 600;
  white-space: nowrap;
}

.ai-models__table tbody tr:last-child td {
  border-bottom: none;
}

.ai-models__mono {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  overflow-wrap: anywhere;
}

.ai-models__switch-cell {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.ai-models__usage-hint {
  display: block;
}

.ai-models__last-error {
  max-width: 220px;
  color: var(--cb-danger);
  overflow-wrap: anywhere;
}

.ai-models__row-actions {
  display: flex;
  gap: var(--cb-space-1);
  flex-wrap: wrap;
}

.ai-models__cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--cb-space-4);
}

.ai-model-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
}

.ai-model-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-2);
}

.ai-model-card__name {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.ai-model-card__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cb-space-2);
  margin: 0;
}

.ai-model-card__facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.ai-model-card__error {
  font-size: var(--cb-text-xs);
  color: var(--cb-danger);
  overflow-wrap: anywhere;
}

.ai-tag {
  display: inline-block;
  margin-right: var(--cb-space-1);
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
  white-space: nowrap;
}

/* 开关 */
.ai-switch {
  position: relative;
  width: 34px;
  height: 18px;
  padding: 0;
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-bg-soft);
  cursor: pointer;
}

.ai-switch:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.ai-switch__thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--cb-text-faint);
  transition: left 0.15s ease;
}

.ai-switch--on {
  border-color: var(--cb-success);
  background: var(--cb-success-soft);
}

.ai-switch--on .ai-switch__thumb {
  left: 18px;
  background: var(--cb-success);
}

/* 通用控件 */
.ai-btn {
  display: inline-flex;
  align-items: center;
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
  text-decoration: none;
}

.ai-btn:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
  text-decoration: none;
}

.ai-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.ai-btn--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
}

.ai-btn--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

/* 模态 */
.ai-modal {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: grid;
  place-items: center;
  padding: var(--cb-space-4);
}

.ai-modal__backdrop {
  position: absolute;
  inset: 0;
  background: var(--cb-bg);
  opacity: 0.72;
}

.ai-modal__panel {
  position: relative;
  width: min(560px, 100%);
  max-height: 90vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-5);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-lg);
  background: var(--cb-surface-raised);
  box-shadow: var(--cb-shadow-md);
}

.ai-modal__title {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
}

.ai-modal__message {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ai-modal__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--cb-space-2);
}

.ai-field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.ai-field__label {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.ai-field__label--row {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.ai-field__input {
  width: 100%;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.ai-field__input--mono {
  font-family: var(--cb-font-mono);
}

.ai-field__textarea {
  resize: vertical;
  min-height: 72px;
}

.ai-field__hint {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.ai-field__error,
.ai-form__error {
  font-size: var(--cb-text-xs);
  color: var(--cb-danger);
}

.ai-form__error {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  font-size: var(--cb-text-sm);
  white-space: pre-wrap;
}
</style>
