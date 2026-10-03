<script setup lang="ts">
/**
 * 凭据（W4 §58-§59）：Provider / 凭据域 / 凭据名 / 状态 / 来源 + 更换、删除、测试。
 *
 * 列表只显示后端给的 masked 值；明文只存在于「更换」输入框内，提交后
 * 立即清空，绝不写进任何 store 状态，也绝不渲染出来。
 */
import { NInput } from 'naive-ui'
import { onMounted, ref } from 'vue'

import { credentialsApi, type CredentialItem } from '@/api/config'
import { ApiError, errorMessage } from '@/api/client'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import { useCredentialsStore } from '@/stores/credentials'
import type { TestResult } from '@/types/ai'

const store = useCredentialsStore()
const ai = useAiStore()

const DOMAIN_LABELS: Record<string, string> = {
  ai: 'AI 服务商',
  embedding: '向量模型',
  onebot: 'OneBot',
  web: 'Web 登录',
  tool: '工具',
}

const SOURCE_LABELS: Record<string, string> = {
  env: '环境变量',
  dotenv: '.env',
  database: '数据库',
  file: 'secrets.json',
  unset: '未配置',
}

const editingKey = ref('')
const editValue = ref('')
const revealEdit = ref(false)
const saving = ref(false)

const deleteTarget = ref<CredentialItem | null>(null)
const showDelete = ref(false)
const forceTarget = ref<CredentialItem | null>(null)
const showForce = ref(false)
const forceMessage = ref('')

const testingKey = ref('')
const testResult = ref<TestResult | null>(null)
const testError = ref('')

function rowKey(item: CredentialItem): string {
  return `${item.domain}/${item.ref}`
}

function domainLabel(item: CredentialItem): string {
  return DOMAIN_LABELS[item.domain] ?? item.domain
}

function sourceText(item: CredentialItem): string {
  const source = item.source ?? 'unset'
  const label = SOURCE_LABELS[source] ?? source
  return `${label}（${source}）`
}

function providerFor(item: CredentialItem): string {
  if (item.domain !== 'ai' && item.domain !== 'embedding') return '—'
  const names = ai.providers
    .filter((provider) => provider.api_key_env === item.ref)
    .map((provider) => provider.name)
  return names.length > 0 ? names.join('、') : '—'
}

function startEdit(item: CredentialItem): void {
  editingKey.value = rowKey(item)
  editValue.value = ''
  revealEdit.value = false
}

function cancelEdit(): void {
  editingKey.value = ''
  editValue.value = ''
  revealEdit.value = false
}

async function saveEdit(item: CredentialItem): Promise<void> {
  const value = editValue.value
  editValue.value = ''
  revealEdit.value = false
  if (!value) {
    toast.warning('请输入新的凭据值')
    return
  }
  saving.value = true
  const ok = await store.save(item.domain, item.ref, value)
  saving.value = false
  if (!ok) {
    toast.error('保存失败', store.error || '请稍后重试')
    return
  }
  editingKey.value = ''
  toast.success('凭据已更新', `${item.ref} 已原子写入，页面只显示脱敏值`)
}

function startDelete(item: CredentialItem): void {
  deleteTarget.value = item
  forceMessage.value = ''
  showDelete.value = true
}

async function confirmDelete(): Promise<void> {
  const target = deleteTarget.value
  showDelete.value = false
  deleteTarget.value = null
  if (!target) return
  try {
    await credentialsApi.remove(target.domain, target.ref)
    toast.success('凭据已删除', target.ref)
    await store.load()
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === 'credential.in_use') {
      forceMessage.value = caught.message
      forceTarget.value = target
      showForce.value = true
      return
    }
    toast.error('删除失败', errorMessage(caught))
  }
}

async function confirmForce(): Promise<void> {
  const target = forceTarget.value
  showForce.value = false
  forceTarget.value = null
  if (!target) return
  try {
    await credentialsApi.remove(target.domain, target.ref, true)
    toast.success('已强制删除', target.ref)
    await store.load()
  } catch (caught) {
    toast.error('强制删除失败', errorMessage(caught))
  }
}

async function runTest(item: CredentialItem): Promise<void> {
  testingKey.value = rowKey(item)
  testResult.value = null
  testError.value = ''
  const provider =
    ai.providers.find((entry) => entry.api_key_env === item.ref)?.name ?? item.ref
  const result = await store.test({ provider })
  testingKey.value = ''
  if (!result) {
    testError.value = store.error || '测试请求失败'
    return
  }
  testResult.value = result
}

onMounted(() => {
  void store.load()
  void ai.loadProviders()
})
</script>

<template>
  <div class="cb-credentials" data-test="credentials-page">
    <SectionHeader
      title="凭据"
      description="API Key / Token 只写入 .env 或 secrets.json，页面与接口永远只显示脱敏值。"
    />

    <ErrorState v-if="store.error" :message="store.error" @retry="store.load()" />

    <LoadingState v-if="store.loading && store.items.length === 0" label="正在读取凭据…" :rows="4" />

    <EmptyState
      v-else-if="store.items.length === 0"
      title="没有可管理的凭据"
      description="配置服务商或工具后，这里会列出对应的凭据槽位。"
    />

    <div v-else class="cb-credentials__table-wrap">
      <table class="cb-credentials__table">
        <caption class="cb-visually-hidden">凭据列表（脱敏）</caption>
        <thead>
          <tr>
            <th scope="col">Provider</th>
            <th scope="col">凭据域</th>
            <th scope="col">凭据名</th>
            <th scope="col">状态</th>
            <th scope="col">来源</th>
            <th scope="col">当前值（脱敏）</th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="item in store.items" :key="rowKey(item)">
            <tr class="cb-credentials__row" data-test="credential-row" :data-key="rowKey(item)">
              <td>{{ providerFor(item) }}</td>
              <td>{{ domainLabel(item) }}</td>
              <td><code class="cb-credentials__ref">{{ item.ref }}</code></td>
              <td>
                <span
                  class="cb-credentials__status"
                  :class="{ 'cb-credentials__status--on': item.configured }"
                  data-test="credential-status"
                >
                  <span aria-hidden="true">{{ item.configured ? '●' : '○' }}</span>
                  {{ item.configured ? '已配置' : '未配置' }}
                </span>
              </td>
              <td>
                <span data-test="credential-source">{{ sourceText(item) }}</span>
              </td>
              <td>
                <code data-test="credential-masked">{{ item.masked || '—' }}</code>
              </td>
              <td>
                <div class="cb-credentials__actions">
                  <button
                    type="button"
                    class="cb-credentials__button"
                    data-test="credential-replace"
                    @click="startEdit(item)"
                  >
                    更换
                  </button>
                  <button
                    type="button"
                    class="cb-credentials__button"
                    data-test="credential-delete"
                    @click="startDelete(item)"
                  >
                    删除
                  </button>
                  <button
                    v-if="item.domain === 'ai' || item.domain === 'embedding'"
                    type="button"
                    class="cb-credentials__button"
                    data-test="credential-test"
                    :disabled="!item.configured || testingKey === rowKey(item)"
                    @click="runTest(item)"
                  >
                    {{ testingKey === rowKey(item) ? '测试中…' : '测试' }}
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="editingKey === rowKey(item)" class="cb-credentials__edit-row">
              <td colspan="7">
                <div class="cb-credentials__edit" data-test="credential-edit">
                  <label class="cb-visually-hidden" :for="`credential-input-${rowKey(item)}`">
                    新的凭据值
                  </label>
                  <NInput
                    :id="`credential-input-${rowKey(item)}`"
                    :type="revealEdit ? 'text' : 'password'"
                    :value="editValue"
                    :input-props="{ autocomplete: 'off', 'aria-label': `新的 ${item.ref} 值` }"
                    data-test="credential-edit-input"
                    @update:value="editValue = $event"
                  />
                  <button
                    type="button"
                    class="cb-credentials__button"
                    data-test="credential-edit-reveal"
                    @click="revealEdit = !revealEdit"
                  >
                    {{ revealEdit ? '隐藏' : '显示' }}
                  </button>
                  <button
                    type="button"
                    class="cb-credentials__button cb-credentials__button--primary"
                    data-test="credential-save"
                    :disabled="saving"
                    @click="saveEdit(item)"
                  >
                    {{ saving ? '保存中…' : '保存' }}
                  </button>
                  <button
                    type="button"
                    class="cb-credentials__button"
                    data-test="credential-cancel"
                    :disabled="saving"
                    @click="cancelEdit"
                  >
                    取消
                  </button>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <div v-if="testResult || testError" class="cb-credentials__result" data-test="credential-test-result" role="status">
      <p class="cb-credentials__result-title">
        {{ testResult ? (testResult.ok ? '✓ 测试成功' : '✗ 测试失败') : '✗ 测试失败' }}
      </p>
      <dl class="cb-credentials__result-facts">
        <div>
          <dt class="cb-caption">HTTP 状态</dt>
          <dd>{{ testResult?.http_status ?? '—' }}</dd>
        </div>
        <div>
          <dt class="cb-caption">延迟</dt>
          <dd>{{ testResult ? `${testResult.latency_ms} ms` : '—' }}</dd>
        </div>
        <div>
          <dt class="cb-caption">消息</dt>
          <dd>{{ testResult?.message || testResult?.reply || testError || '—' }}</dd>
        </div>
      </dl>
    </div>

    <ConfirmDialog
      :show="showDelete"
      title="删除凭据"
      :message="deleteTarget ? `确定删除「${deleteTarget.ref}」吗？` : ''"
      detail="删除后使用该凭据的功能会立即失效；如果仍被 Provider 引用，后端会拒绝删除。"
      confirm-text="删除"
      danger
      @confirm="confirmDelete"
      @cancel="showDelete = false"
    />

    <ConfirmDialog
      :show="showForce"
      title="该凭据仍在使用中"
      :message="forceMessage"
      detail="如果确认不再使用该凭据，可以强制删除。"
      confirm-text="强制删除"
      danger
      @confirm="confirmForce"
      @cancel="showForce = false"
    />
  </div>
</template>

<style scoped>
.cb-credentials {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-credentials__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-credentials__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.cb-credentials__table th,
.cb-credentials__table td {
  padding: var(--cb-space-2) var(--cb-space-3);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  vertical-align: middle;
}

.cb-credentials__table th {
  color: var(--cb-text-muted);
  font-weight: 500;
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-credentials__row:last-child td {
  border-bottom: none;
}

.cb-credentials__ref {
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-credentials__status {
  color: var(--cb-text-faint);
  white-space: nowrap;
}

.cb-credentials__status--on {
  color: var(--cb-success);
}

.cb-credentials__actions {
  display: flex;
  gap: var(--cb-space-1);
  flex-wrap: wrap;
}

.cb-credentials__button {
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

.cb-credentials__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-credentials__button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-credentials__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary);
  color: var(--cb-bg);
}

.cb-credentials__edit-row td {
  background: var(--cb-bg-soft);
}

.cb-credentials__edit {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  max-width: 640px;
}

.cb-credentials__edit :deep(.n-input) {
  flex: 1;
}

.cb-credentials__result {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.cb-credentials__result-title {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-credentials__result-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.cb-credentials__result-facts dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}
</style>
