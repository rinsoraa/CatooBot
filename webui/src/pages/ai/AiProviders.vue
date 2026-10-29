<script setup lang="ts">
/**
 * 服务商页（W4 §8-§17）：卡片列表 + 表单（创建/编辑）+ 删除冲突处理 + 测试连接。
 *
 * 所有写操作先落服务端、再读真实状态；表单错误贴在字段旁，保存中禁用按钮。
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import SecretField from '@/components/ai/SecretField.vue'
import TestResultPanel from '@/components/ai/TestResultPanel.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import { useCredentialsStore } from '@/stores/credentials'
import type { ProviderItem, TestResult } from '@/types/ai'

const aiStore = useAiStore()
const credentialsStore = useCredentialsStore()
const router = useRouter()
const route = useRoute()

const NAME_PATTERN = /^[A-Za-z0-9_-]{1,32}$/
const ENV_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const DEFAULT_TYPE = 'openai_compatible'

type KeyMode = 'keep' | 'replace' | 'clear'

const showForm = ref(false)
const editingName = ref('')
const saving = ref(false)
const formError = ref('')
const fieldErrors = reactive<Record<string, string>>({})
const envEdited = ref(false)

const form = reactive({
  name: '',
  type: DEFAULT_TYPE,
  base_url: '',
  api_key_env: '',
  apiKey: '',
  keyMode: 'keep' as KeyMode,
  enabled: true,
})

const testState = reactive<Record<string, { loading: boolean; result: TestResult | null }>>({})

const isEditing = computed(() => editingName.value.length > 0)
const showLoading = computed(() => aiStore.loading && aiStore.providers.length === 0)

/** 类型下拉只从现有 Provider 去重取值；一个都没有时给默认类型（§14）。 */
const providerTypes = computed(() => {
  const types = new Set<string>()
  for (const provider of aiStore.providers) {
    if (provider.type) types.add(provider.type)
  }
  if (types.size === 0) types.add(DEFAULT_TYPE)
  return [...types]
})

const derivedEnv = computed(() => deriveEnvFromName(form.name))

/** 每张卡显示的最近错误来自该 Provider 下模型的 last_error（后端没有 Provider 级 last_error）。 */
function lastError(providerName: string): string {
  const model = aiStore.models.find((item) => item.provider === providerName && item.last_error)
  return model?.last_error ?? ''
}

function providerState(provider: ProviderItem): { state: StatusState; label: string } {
  if (!provider.has_key) return { state: 'warn', label: '缺少 API Key' }
  if (provider.models.length === 0) return { state: 'idle', label: '尚无模型' }
  return { state: 'ok', label: '可用' }
}

function modelsOf(providerName: string): string[] {
  return aiStore.models.filter((model) => model.provider === providerName).map((model) => model.name)
}

// ------------------------------------------------------------------ 表单
function resetForm(): void {
  form.name = ''
  form.type = providerTypes.value[0] ?? DEFAULT_TYPE
  form.base_url = ''
  form.api_key_env = ''
  form.apiKey = ''
  form.keyMode = 'keep'
  form.enabled = true
  envEdited.value = false
  formError.value = ''
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
}

function openCreate(): void {
  editingName.value = ''
  resetForm()
  showForm.value = true
}

function openEdit(provider: ProviderItem): void {
  editingName.value = provider.name
  resetForm()
  form.name = provider.name
  form.type = provider.type || DEFAULT_TYPE
  form.base_url = provider.base_url
  form.api_key_env = provider.api_key_env
  showForm.value = true
}

function closeForm(): void {
  showForm.value = false
  saving.value = false
}

watch(
  () => form.name,
  (name) => {
    if (isEditing.value || envEdited.value) return
    form.api_key_env = deriveEnvFromName(name)
  },
)

function deriveEnvFromName(name: string): string {
  const slug = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return slug ? `CATOOBOT_${slug}_API_KEY` : ''
}

function onEnvInput(value: string): void {
  envEdited.value = true
  form.api_key_env = value
}

function validate(): boolean {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  if (!isEditing.value && !NAME_PATTERN.test(form.name.trim())) {
    fieldErrors.name = '名称只允许 1~32 位字母、数字、下划线或短横线'
  }
  if (!/^https?:\/\/[^\s]+$/.test(form.base_url.trim())) {
    fieldErrors.base_url = '接口地址必须是 http:// 或 https:// 开头的完整地址'
  }
  if (form.api_key_env && !ENV_PATTERN.test(form.api_key_env)) {
    fieldErrors.api_key_env = '环境变量名只允许字母、数字、下划线（不能以数字开头）'
  }
  return Object.keys(fieldErrors).length === 0
}

function inferField(message: string): string {
  if (message.includes('接口地址') || message.includes('base_url')) return 'base_url'
  if (message.includes('环境变量')) return 'api_key_env'
  if (message.includes('类型')) return 'type'
  if (message.includes('名称')) return 'name'
  return ''
}

async function submit(): Promise<void> {
  if (saving.value) return
  formError.value = ''
  if (!validate()) return
  const name = form.name.trim()
  const envName = form.api_key_env.trim()
  saving.value = true
  try {
    const saved = await aiStore.saveProvider(name, {
      type: form.type,
      base_url: form.base_url.trim(),
      api_key_env: envName,
    })
    if (!saved) {
      formError.value = aiStore.error
      const field = inferField(aiStore.error)
      if (field) fieldErrors[field] = aiStore.error
      return
    }

    if (form.apiKey) {
      const keySaved = await credentialsStore.save('ai', envName, form.apiKey)
      if (!keySaved) {
        formError.value = `Provider 已保存，但 API Key 写入失败：${credentialsStore.error}`
        toast.error('API Key 未保存', credentialsStore.error)
        return
      }
      await aiStore.loadAll()
    } else if (isEditing.value && form.keyMode === 'clear' && envName) {
      const removed = await credentialsStore.remove('ai', envName, true)
      if (!removed) {
        formError.value = `Provider 已保存，但凭据清除失败：${credentialsStore.error}`
        toast.error('凭据未清除', credentialsStore.error)
        return
      }
      await aiStore.loadAll()
    }

    const restart = aiStore.providers.find((item) => item.name === name)?.restart_required
    toast.success(
      isEditing.value ? `已保存 Provider「${name}」` : `已创建 Provider「${name}」`,
      restart ? '需要重启 CatooBot 才能生效' : '',
    )
    closeForm()
  } finally {
    saving.value = false
  }
}

// ------------------------------------------------------------------ 测试连接
async function testProvider(provider: ProviderItem): Promise<void> {
  testState[provider.name] = { loading: true, result: null }
  const model = aiStore.models.find((item) => item.provider === provider.name)?.name
  const result = await credentialsStore.test(
    model ? { provider: provider.name, model } : { provider: provider.name },
  )
  testState[provider.name] = { loading: false, result }
  if (!result) toast.error('测试连接失败', credentialsStore.error)
}

// ------------------------------------------------------------------ 删除
const conflict = ref<{ name: string; message: string; count: number } | null>(null)
const forceConfirm = ref(false)
const deleting = ref(false)

const deleteTarget = ref<ProviderItem | null>(null)
const deleteConfirm = ref(false)

/** 站内确认（§37）：删除不可恢复；有模型时说明受影响的模型。 */
const deleteMessage = computed(() => {
  const target = deleteTarget.value
  if (!target) return ''
  const models = modelsOf(target.name)
  const affected = models.length
    ? `该 Provider 下仍有 ${models.length} 个模型（${models.join('、')}），需先删除这些模型，或强制删除时连带移除。`
    : ''
  return `删除后无法恢复。该 Provider 将从配置中移除。${affected}`
})

function requestRemove(provider: ProviderItem): void {
  if (deleting.value) return
  deleteTarget.value = provider
  deleteConfirm.value = true
}

async function removeProvider(): Promise<void> {
  const provider = deleteTarget.value
  deleteTarget.value = null
  if (!provider || deleting.value) return
  deleting.value = true
  const ok = await aiStore.deleteProvider(provider.name)
  deleting.value = false
  if (ok) {
    toast.success(`已删除 Provider「${provider.name}」`)
    return
  }
  const count = modelsOf(provider.name).length
  if (count > 0) {
    conflict.value = { name: provider.name, message: aiStore.error, count }
  } else {
    toast.error('删除失败', aiStore.error)
  }
}

async function forceRemove(): Promise<void> {
  const target = conflict.value
  if (!target) return
  const ok = await aiStore.deleteProvider(target.name, true)
  forceConfirm.value = false
  if (ok) {
    conflict.value = null
    toast.success(`已强制删除 Provider「${target.name}」及其模型`)
  } else {
    toast.error('强制删除失败', aiStore.error)
  }
}

function viewModels(): void {
  conflict.value = null
  void router.push('/ai/models')
}

onMounted(() => {
  if (!aiStore.lastLoadedAt && !aiStore.loading) void aiStore.loadAll()
  if (route.query.create === '1') openCreate()
})
</script>

<template>
  <div class="ai-providers" data-test="ai-providers">
    <SectionHeader
      title="服务商"
      description="OpenAI 兼容接口的地址与 API Key；Key 只写入服务端 .env，前端不回显。"
    >
      <template #actions>
        <button type="button" class="ai-btn ai-btn--primary" data-test="add-provider" @click="openCreate">
          + 添加 Provider
        </button>
      </template>
    </SectionHeader>

    <ErrorState v-if="aiStore.error && !conflict && !showForm" :message="aiStore.error" @retry="aiStore.loadAll()" />

    <LoadingState v-if="showLoading" label="正在读取服务商…" :rows="3" />

    <EmptyState
      v-else-if="aiStore.providers.length === 0"
      title="尚未配置服务商"
      description="先添加一个 OpenAI 兼容的 Provider（接口地址 + API Key），才能创建模型。"
    >
      <button type="button" class="ai-btn ai-btn--primary" data-test="empty-add-provider" @click="openCreate">
        添加 Provider
      </button>
    </EmptyState>

    <div v-else class="ai-providers__list" data-test="provider-list">
      <article
        v-for="provider in aiStore.providers"
        :key="provider.name"
        class="cb-card ai-provider"
        :data-test="`provider-${provider.name}`"
      >
        <div class="ai-provider__head">
          <div class="ai-provider__title">
            <h3 class="ai-provider__name">{{ provider.name }}</h3>
            <StatusBadge :state="providerState(provider).state" :label="providerState(provider).label" />
          </div>
          <div class="ai-provider__actions">
            <button type="button" class="ai-btn" data-test="test-provider" @click="testProvider(provider)">
              测试连接
            </button>
            <button type="button" class="ai-btn" data-test="edit-provider" @click="openEdit(provider)">
              编辑
            </button>
            <button
              type="button"
              class="ai-btn ai-btn--danger"
              data-test="delete-provider"
              @click="requestRemove(provider)"
            >
              删除
            </button>
          </div>
        </div>

        <dl class="ai-provider__facts">
          <div class="ai-provider__fact">
            <dt class="cb-caption">类型</dt>
            <dd>{{ provider.type }}</dd>
          </div>
          <div class="ai-provider__fact">
            <dt class="cb-caption">接口地址</dt>
            <dd class="ai-provider__mono">{{ provider.base_url || '—' }}</dd>
          </div>
          <div class="ai-provider__fact">
            <dt class="cb-caption">API Key</dt>
            <dd>{{ provider.has_key ? `已配置（${provider.api_key_env || '环境变量'}）` : `未配置（${provider.api_key_env || '未设置环境变量'}）` }}</dd>
          </div>
          <div class="ai-provider__fact">
            <dt class="cb-caption">模型数量</dt>
            <dd>{{ provider.models.length }} 个</dd>
          </div>
          <div v-if="lastError(provider.name)" class="ai-provider__fact ai-provider__fact--wide">
            <dt class="cb-caption">最近错误</dt>
            <dd class="ai-provider__error">{{ lastError(provider.name) }}</dd>
          </div>
        </dl>

        <p v-if="provider.restart_required" class="ai-provider__restart" data-test="provider-restart">
          该变更需要重启 CatooBot 才能生效。
        </p>

        <TestResultPanel
          v-if="testState[provider.name] && (testState[provider.name].loading || testState[provider.name].result)"
          :result="testState[provider.name].result"
          :loading="testState[provider.name].loading"
          :title="`测试连接：${provider.name}`"
        />
      </article>
    </div>

    <!-- 创建 / 编辑表单 -->
    <div v-if="showForm" class="ai-modal" data-test="provider-form">
      <div class="ai-modal__backdrop" aria-hidden="true" @click="closeForm" />
      <form
        class="ai-modal__panel"
        role="dialog"
        aria-modal="true"
        aria-label="服务商表单"
        @submit.prevent="submit"
      >
        <h2 class="ai-modal__title">{{ isEditing ? `编辑 Provider：${editingName}` : '添加 Provider' }}</h2>

        <div class="ai-field">
          <label class="ai-field__label" for="provider-name">名称</label>
          <input
            id="provider-name"
            v-model="form.name"
            class="ai-field__input"
            data-test="provider-name-input"
            :disabled="isEditing"
            placeholder="例如 deepseek"
          />
          <p v-if="fieldErrors.name" class="ai-field__error" role="alert" data-test="error-name">
            {{ fieldErrors.name }}
          </p>
        </div>

        <div class="ai-field">
          <label class="ai-field__label" for="provider-type">类型</label>
          <select id="provider-type" v-model="form.type" class="ai-field__input" data-test="provider-type-input">
            <option v-for="type in providerTypes" :key="type" :value="type">{{ type }}</option>
          </select>
          <p class="ai-field__hint">类型来自现有服务商；当前后端支持 OpenAI 兼容接口。</p>
          <p v-if="fieldErrors.type" class="ai-field__error" role="alert">{{ fieldErrors.type }}</p>
        </div>

        <div class="ai-field">
          <label class="ai-field__label" for="provider-base-url">Base URL</label>
          <input
            id="provider-base-url"
            v-model="form.base_url"
            class="ai-field__input ai-field__input--mono"
            data-test="provider-base-url-input"
            placeholder="https://api.deepseek.com/v1"
          />
          <p class="ai-field__hint">OpenAI 兼容的接口根地址，通常以 /v1 结尾（不要带 /chat/completions）。</p>
          <p v-if="fieldErrors.base_url" class="ai-field__error" role="alert" data-test="error-base-url">
            {{ fieldErrors.base_url }}
          </p>
        </div>

        <div class="ai-field">
          <label class="ai-field__label" for="provider-env">API Key 环境变量名</label>
          <input
            id="provider-env"
            class="ai-field__input ai-field__input--mono"
            data-test="provider-env-input"
            :value="form.api_key_env"
            placeholder="CATOOBOT_DEEPSEEK_API_KEY"
            @input="onEnvInput(($event.target as HTMLInputElement).value)"
          />
          <p class="ai-field__hint">
            写入服务端 .env 的变量名，只允许字母、数字、下划线；默认由名称派生<template v-if="derivedEnv">（{{ derivedEnv }}）</template>。
          </p>
          <p v-if="fieldErrors.api_key_env" class="ai-field__error" role="alert" data-test="error-env">
            {{ fieldErrors.api_key_env }}
          </p>
        </div>

        <SecretField
          v-if="!isEditing || form.keyMode === 'replace'"
          v-model="form.apiKey"
          label="API Key"
          :configured="isEditing"
          :hint="isEditing ? '留空表示不变；输入新值会覆盖 .env 中的旧 Key。' : '只写入服务端 .env，保存后无法在页面回显明文。'"
          placeholder="sk-…"
        />

        <div v-if="isEditing" class="ai-field" data-test="key-modes">
          <p class="ai-field__label">已保存的 Key</p>
          <div class="ai-field__radios">
            <label><input v-model="form.keyMode" type="radio" value="keep" data-test="key-mode-keep" /> 不变（默认）</label>
            <label><input v-model="form.keyMode" type="radio" value="replace" data-test="key-mode-replace" /> 更换</label>
            <label><input v-model="form.keyMode" type="radio" value="clear" data-test="key-mode-clear" /> 清除</label>
          </div>
          <p v-if="form.keyMode === 'clear'" class="ai-field__hint">
            将删除 .env 中的 {{ form.api_key_env || '对应变量' }}；若仍被其他 Provider 引用，后端会拒绝。
          </p>
        </div>

        <div class="ai-field">
          <label class="ai-field__label ai-field__label--row" for="provider-enabled">
            <input id="provider-enabled" type="checkbox" checked disabled data-test="provider-enabled" />
            启用
          </label>
          <p class="ai-field__hint">当前版本 Provider 没有独立启停字段；请在模型列表中启用/停用具体模型。</p>
        </div>

        <p v-if="formError" class="ai-form__error" role="alert" data-test="form-error">{{ formError }}</p>

        <div class="ai-modal__actions">
          <button type="button" class="ai-btn" data-test="form-cancel" @click="closeForm">取消</button>
          <button type="submit" class="ai-btn ai-btn--primary" :disabled="saving" data-test="form-save">
            {{ saving ? '保存中…' : '保存' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 删除冲突（ai.provider_in_use） -->
    <div v-if="conflict" class="ai-modal" data-test="provider-conflict">
      <div class="ai-modal__backdrop" aria-hidden="true" @click="conflict = null" />
      <div class="ai-modal__panel" role="dialog" aria-modal="true" aria-label="无法删除服务商">
        <h2 class="ai-modal__title">无法删除 Provider「{{ conflict.name }}」</h2>
        <p class="ai-modal__message" data-test="conflict-message">{{ conflict.message }}</p>
        <p class="cb-caption">该 Provider 下仍有 {{ conflict.count }} 个模型。</p>
        <div class="ai-modal__actions">
          <button type="button" class="ai-btn" data-test="conflict-view-models" @click="viewModels">
            查看模型
          </button>
          <button type="button" class="ai-btn ai-btn--danger" data-test="conflict-force" @click="forceConfirm = true">
            强制删除
          </button>
        </div>
      </div>
    </div>

    <!-- 首次删除必须先经站内确认（§37/§38） -->
    <ConfirmDialog
      v-model="deleteConfirm"
      title="删除 Provider"
      :message="deleteMessage"
      detail="此操作不可恢复，确定继续吗？"
      confirm-text="删除"
      danger
      @confirm="removeProvider"
    />

    <ConfirmDialog
      v-model="forceConfirm"
      title="强制删除 Provider"
      :message="`强制删除「${conflict?.name ?? ''}」会连带删除其下全部模型，并解除相关绑定。`"
      detail="此操作不可恢复，确定继续吗？"
      confirm-text="强制删除"
      danger
      @confirm="forceRemove"
    />
  </div>
</template>

<style scoped>
.ai-providers {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.ai-providers__list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--cb-space-4);
}

.ai-provider {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
}

.ai-provider__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.ai-provider__title {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  min-width: 0;
}

.ai-provider__name {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.ai-provider__actions {
  display: flex;
  gap: var(--cb-space-1);
  flex-wrap: wrap;
}

.ai-provider__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.ai-provider__fact--wide {
  grid-column: 1 / -1;
}

.ai-provider__fact dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.ai-provider__mono {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.ai-provider__error {
  color: var(--cb-danger);
}

.ai-provider__restart {
  font-size: var(--cb-text-xs);
  color: var(--cb-warning);
}

/* 通用控件 */
.ai-btn {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.ai-btn:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
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
  width: min(520px, 100%);
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
  margin-top: var(--cb-space-2);
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

.ai-field__radios {
  display: flex;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.ai-field__radios label {
  display: flex;
  align-items: center;
  gap: var(--cb-space-1);
}
</style>
