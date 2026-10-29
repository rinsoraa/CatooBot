<script setup lang="ts">
/**
 * 配置向导（W4 §67-§70、§106）：五步闭环，全部复用已有 store 动作。
 *
 * 步骤定位由后端推导的 `aiStore.status.checks` 决定；「✓ AI Ready」只有在
 * 后端 `status === 'ready'` 且**本会话内**第 ⑤ 步模型测试成功时才显示——
 * 不能只看 Provider 数量。
 */
import { computed, onMounted, ref, watch } from 'vue'

import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import SecretField from '@/components/ai/SecretField.vue'
import TestResultPanel from '@/components/ai/TestResultPanel.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import { useCredentialsStore } from '@/stores/credentials'
import type { TestResult } from '@/types/ai'
import type { StatusState } from '@/components/StatusBadge.vue'

type StepKey = 'provider' | 'key' | 'model' | 'chat' | 'test'

interface StepDef {
  key: StepKey
  index: number
  title: string
  description: string
}

const STEPS: StepDef[] = [
  { key: 'provider', index: 1, title: '添加 Provider', description: '接口地址与类型' },
  { key: 'key', index: 2, title: '填写 API Key', description: '只写入 .env，页面永远只显示掩码' },
  { key: 'model', index: 3, title: '添加模型', description: '别名 + 服务商模型 ID' },
  { key: 'chat', index: 4, title: '设为聊天模型', description: '默认聊天模型 = 故障转移链第一项' },
  { key: 'test', index: 5, title: '测试', description: '发一条真实诊断请求' },
]

const aiStore = useAiStore()
const credentialsStore = useCredentialsStore()

const loading = ref(false)
const activeStep = ref<StepKey>('provider')
/** §106：只有本会话内测试成功才置 true。 */
const testedOk = ref(false)

// ① Provider
const providerName = ref('')
const providerType = ref('openai_compatible')
const baseUrl = ref('')
const savingProvider = ref(false)

// ② API Key
const keyProvider = ref('')
const keyValue = ref('')
const savingKey = ref(false)

// ③ 模型
const modelAlias = ref('')
const modelProvider = ref('')
const modelId = ref('')
const savingModel = ref(false)

// ④ 聊天模型
const chatChoice = ref('')
const savingChat = ref(false)

// ⑤ 测试
const testTarget = ref('')
const testResult = ref<TestResult | null>(null)
const testLoading = ref(false)

const providerTypeOptions = computed(() => {
  const types = new Set(aiStore.providers.map((provider) => provider.type).filter(Boolean))
  if (types.size === 0) types.add('openai_compatible')
  return [...types]
})

const done = computed<Record<StepKey, boolean>>(() => ({
  provider: aiStore.providers.length > 0 || Boolean(aiStore.status?.checks.has_provider),
  key: Boolean(aiStore.status?.checks.has_credential),
  model: aiStore.models.length > 0 || Boolean(aiStore.status?.checks.has_model),
  chat: Boolean(aiStore.status?.chat_model),
  test: testedOk.value,
}))

const firstIncomplete = computed<StepKey>(
  () => STEPS.find((step) => !done.value[step.key])?.key ?? 'test',
)

const completedCount = computed(() => STEPS.filter((step) => done.value[step.key]).length)

/** §106：后端 ready + 本会话测试成功，两个条件缺一不可。 */
const aiReady = computed(() => aiStore.status?.status === 'ready' && testedOk.value)

const healthState = computed<StatusState>(() => {
  switch (aiStore.status?.status) {
    case 'ready':
      return 'ok'
    case 'degraded':
      return 'warn'
    case 'unavailable':
      return 'error'
    case 'not_configured':
      return 'off'
    default:
      return 'idle'
  }
})

const healthLabel = computed(() => {
  switch (aiStore.status?.status) {
    case 'ready':
      return '就绪'
    case 'degraded':
      return '降级'
    case 'unavailable':
      return '不可用'
    case 'not_configured':
      return '未配置'
    default:
      return '未知'
  }
})

const backendError = computed(() => aiStore.error || credentialsStore.error)

const keyEnvRef = computed(() => {
  const provider = aiStore.providers.find((item) => item.name === keyProvider.value)
  return provider?.api_key_env || deriveEnvName(keyProvider.value)
})

const keyConfigured = computed(() => {
  const provider = aiStore.providers.find((item) => item.name === keyProvider.value)
  return Boolean(provider?.has_key)
})

const keyMasked = computed(() => {
  const item = credentialsStore.items.find(
    (credential) => credential.domain === 'ai' && credential.ref === keyEnvRef.value,
  )
  return item?.masked ?? ''
})

function deriveEnvName(name: string): string {
  const cleaned = name
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return cleaned ? `CATOOBOT_${cleaned}_API_KEY` : ''
}

function nextStepKey(key: StepKey): StepKey | null {
  const index = STEPS.findIndex((step) => step.key === key)
  return STEPS[index + 1]?.key ?? null
}

function nextStepTitle(key: StepKey): string {
  const next = nextStepKey(key)
  if (!next) return ''
  return STEPS.find((step) => step.key === next)?.title ?? ''
}

function goNext(key: StepKey): void {
  const next = nextStepKey(key)
  if (next) activeStep.value = next
}

async function refresh(): Promise<void> {
  loading.value = true
  await Promise.all([
    aiStore.loadStatus(),
    aiStore.loadProviders(),
    aiStore.loadModels(),
    credentialsStore.load(),
  ])
  loading.value = false
  syncDefaults()
}

function syncDefaults(): void {
  if (!keyProvider.value || !aiStore.providers.some((p) => p.name === keyProvider.value)) {
    keyProvider.value = aiStore.providers[0]?.name ?? ''
  }
  if (!modelProvider.value || !aiStore.providers.some((p) => p.name === modelProvider.value)) {
    modelProvider.value = aiStore.providers[0]?.name ?? ''
  }
  const modelNames = aiStore.models.map((model) => model.name)
  if (!chatChoice.value || !modelNames.includes(chatChoice.value)) {
    chatChoice.value = aiStore.status?.chat_model || modelNames[0] || ''
  }
  if (!testTarget.value || !modelNames.includes(testTarget.value)) {
    testTarget.value = aiStore.status?.chat_model || modelNames[0] || ''
  }
}

// 当前步骤一旦完成，就自动前进到第一个未完成步骤（§67/§106）。
watch(done, () => {
  if (done.value[activeStep.value]) activeStep.value = firstIncomplete.value
})

onMounted(async () => {
  await refresh()
  activeStep.value = firstIncomplete.value
})

function reportFailure(): void {
  toast.error(backendError.value || '操作失败，请重试')
}

async function saveProviderStep(): Promise<void> {
  const name = providerName.value.trim()
  const url = baseUrl.value.trim()
  if (!name || !url) {
    toast.warning('请填写 Provider 名称与接口地址')
    return
  }
  savingProvider.value = true
  const ok = await aiStore.saveProvider(name, {
    type: providerType.value,
    base_url: url,
    api_key_env: deriveEnvName(name),
  })
  savingProvider.value = false
  if (!ok) {
    reportFailure()
    return
  }
  providerName.value = ''
  baseUrl.value = ''
  keyProvider.value = name
  await refresh()
  toast.success('Provider 已保存')
}

async function saveKeyStep(): Promise<void> {
  const refName = keyEnvRef.value
  const value = keyValue.value.trim()
  if (!refName || !value) {
    toast.warning('请选择 Provider 并填写 API Key')
    return
  }
  savingKey.value = true
  const ok = await credentialsStore.save('ai', refName, value)
  savingKey.value = false
  if (!ok) {
    reportFailure()
    return
  }
  keyValue.value = ''
  await refresh()
  toast.success('API Key 已保存')
}

async function saveModelStep(): Promise<void> {
  const alias = modelAlias.value.trim()
  const modelIdValue = modelId.value.trim()
  if (!alias || !modelProvider.value || !modelIdValue) {
    toast.warning('请填写模型别名、Provider 与服务商模型 ID')
    return
  }
  savingModel.value = true
  const ok = await aiStore.saveModel(alias, {
    provider: modelProvider.value,
    model: modelIdValue,
    enabled: true,
  })
  savingModel.value = false
  if (!ok) {
    reportFailure()
    return
  }
  modelAlias.value = ''
  modelId.value = ''
  chatChoice.value = alias
  await refresh()
  toast.success('模型已保存')
}

async function saveChatStep(): Promise<void> {
  if (!chatChoice.value) {
    toast.warning('请选择要设为聊天模型的模型')
    return
  }
  savingChat.value = true
  const result = await aiStore.setRole('chat', chatChoice.value)
  savingChat.value = false
  if (!result) {
    reportFailure()
    return
  }
  await refresh()
  toast.success(result.restart_required ? '已保存，重启后生效' : '已保存')
}

async function runTest(): Promise<void> {
  if (!testTarget.value || testLoading.value) return
  testLoading.value = true
  const result = await aiStore.testModel(testTarget.value, 'ping')
  testResult.value = result
  testLoading.value = false
  if (!result) {
    reportFailure()
    return
  }
  if (result.ok) {
    testedOk.value = true
    await refresh()
    toast.success('测试成功')
    return
  }
  testedOk.value = false
  toast.error(result.message || '测试失败')
}
</script>

<template>
  <div class="setup" data-test="ai-setup">
    <PageHeader title="配置向导" subtitle="五步完成新装 AI 闭环：Provider → Key → 模型 → 默认模型 → 测试。" />

    <section
      v-if="aiReady"
      class="setup__ready"
      role="status"
      data-test="setup-ready"
    >
      <p class="setup__ready-title">✓ AI Ready</p>
      <p class="setup__ready-text">
        Provider、Key、模型、默认聊天模型与本次会话的测试都已通过，AI 可以正常使用。
      </p>
    </section>
    <section v-else class="setup__status cb-card" data-test="setup-status">
      <p>
        当前进度：{{ completedCount }}/{{ STEPS.length }} 步
        <StatusBadge :state="healthState" :label="healthLabel" />
      </p>
      <p class="cb-caption">
        只有后端状态为 ready 且本会话内第 ⑤ 步测试成功，才会显示「✓ AI Ready」——不能只看 Provider 数量。
      </p>
    </section>

    <ErrorState v-if="backendError" :message="backendError" @retry="refresh" />

    <section class="setup__panel cb-card">
      <SectionHeader title="安装步骤" description="点击步骤标题可切换；完成当前步骤后会自动前进。" />

      <ol class="setup__steps">
        <li
          v-for="step in STEPS"
          :key="step.key"
          class="setup__step"
          :class="{ 'setup__step--active': activeStep === step.key, 'setup__step--done': done[step.key] }"
          :aria-current="activeStep === step.key ? 'step' : undefined"
          :data-test="`setup-step-${step.key}`"
        >
          <button type="button" class="setup__step-head" @click="activeStep = step.key">
            <span class="setup__step-index" aria-hidden="true">{{ step.index }}</span>
            <span class="setup__step-title">{{ step.title }}</span>
            <span class="cb-caption setup__step-desc">{{ step.description }}</span>
            <span
              v-if="done[step.key]"
              class="setup__step-done"
              :data-test="`setup-done-${step.key}`"
            >
              ✓ 已完成
            </span>
            <span v-else class="setup__step-todo">待完成</span>
          </button>

          <div v-if="activeStep === step.key" class="setup__step-body">
            <!-- ① Provider -->
            <template v-if="step.key === 'provider'">
              <div class="setup__grid">
                <label class="setup__field">
                  <span class="cb-caption">Provider 名称</span>
                  <input v-model="providerName" type="text" placeholder="例如 openai" data-test="setup-provider-name" />
                </label>
                <label class="setup__field">
                  <span class="cb-caption">类型</span>
                  <select v-model="providerType" data-test="setup-provider-type">
                    <option v-for="type in providerTypeOptions" :key="type" :value="type">{{ type }}</option>
                  </select>
                </label>
                <label class="setup__field setup__field--wide">
                  <span class="cb-caption">接口地址（Base URL）</span>
                  <input
                    v-model="baseUrl"
                    type="url"
                    placeholder="https://api.example.com/v1"
                    data-test="setup-provider-url"
                  />
                </label>
              </div>
              <p class="cb-caption">API Key 环境变量名将使用 {{ deriveEnvName(providerName) || 'CATOOBOT_<名称>_API_KEY' }}。</p>
              <button
                type="button"
                class="setup__action"
                :disabled="savingProvider"
                data-test="setup-save-provider"
                @click="saveProviderStep"
              >
                {{ savingProvider ? '正在保存…' : '保存 Provider' }}
              </button>
            </template>

            <!-- ② API Key -->
            <template v-else-if="step.key === 'key'">
              <label class="setup__field">
                <span class="cb-caption">Provider</span>
                <select v-model="keyProvider" data-test="setup-key-provider">
                  <option v-for="provider in aiStore.providers" :key="provider.name" :value="provider.name">
                    {{ provider.name }}
                  </option>
                </select>
              </label>
              <SecretField
                v-model="keyValue"
                label="API Key"
                :hint="keyEnvRef ? `写入环境变量 ${keyEnvRef}；不会进入 config.yaml，也不会回显明文。` : '请先添加 Provider'"
                :configured="keyConfigured"
                :masked="keyMasked"
                :disabled="!keyProvider"
              />
              <button
                type="button"
                class="setup__action"
                :disabled="savingKey || !keyProvider || !keyValue"
                data-test="setup-save-key"
                @click="saveKeyStep"
              >
                {{ savingKey ? '正在保存…' : '保存 API Key' }}
              </button>
            </template>

            <!-- ③ 模型 -->
            <template v-else-if="step.key === 'model'">
              <div class="setup__grid">
                <label class="setup__field">
                  <span class="cb-caption">模型别名</span>
                  <input v-model="modelAlias" type="text" placeholder="例如 fast" data-test="setup-model-alias" />
                </label>
                <label class="setup__field">
                  <span class="cb-caption">Provider</span>
                  <select v-model="modelProvider" data-test="setup-model-provider">
                    <option v-for="provider in aiStore.providers" :key="provider.name" :value="provider.name">
                      {{ provider.name }}
                    </option>
                  </select>
                </label>
                <label class="setup__field setup__field--wide">
                  <span class="cb-caption">服务商模型 ID</span>
                  <input v-model="modelId" type="text" placeholder="例如 gpt-4o-mini" data-test="setup-model-id" />
                </label>
              </div>
              <button
                type="button"
                class="setup__action"
                :disabled="savingModel"
                data-test="setup-save-model"
                @click="saveModelStep"
              >
                {{ savingModel ? '正在保存…' : '保存模型' }}
              </button>
            </template>

            <!-- ④ 设为聊天模型 -->
            <template v-else-if="step.key === 'chat'">
              <p class="cb-caption">默认聊天模型 = 故障转移链的第一个模型；这里会把它移动到链首。</p>
              <label class="setup__field">
                <span class="cb-caption">聊天模型</span>
                <select v-model="chatChoice" data-test="setup-chat-select">
                  <option value="" disabled>请选择模型</option>
                  <option v-for="model in aiStore.models" :key="model.name" :value="model.name">
                    {{ model.name }}{{ model.enabled ? '' : '（已停用）' }}
                  </option>
                </select>
              </label>
              <button
                type="button"
                class="setup__action"
                :disabled="savingChat || !chatChoice"
                data-test="setup-save-chat"
                @click="saveChatStep"
              >
                {{ savingChat ? '正在保存…' : '设为聊天模型' }}
              </button>
            </template>

            <!-- ⑤ 测试 -->
            <template v-else>
              <p class="cb-caption">测试是连通性诊断：不会写入记忆/对话/关系，只验证能否拿到回复。</p>
              <label class="setup__field">
                <span class="cb-caption">测试模型</span>
                <select v-model="testTarget" data-test="setup-test-model">
                  <option value="" disabled>请选择模型</option>
                  <option v-for="model in aiStore.models" :key="model.name" :value="model.name">
                    {{ model.name }}
                  </option>
                </select>
              </label>
              <button
                type="button"
                class="setup__action"
                :disabled="testLoading || !testTarget"
                data-test="setup-run-test"
                @click="runTest"
              >
                {{ testLoading ? '正在测试…' : '发送测试' }}
              </button>
              <TestResultPanel :result="testResult" :loading="testLoading" title="配置测试" />
            </template>

            <div class="setup__next">
              <span v-if="done[step.key] && step.key === 'test'" class="cb-caption">全部步骤已完成。</span>
              <span v-else-if="done[step.key] && nextStepKey(step.key)" class="cb-caption">
                本步已完成。
              </span>
              <button
                v-if="nextStepKey(step.key)"
                type="button"
                class="setup__next-button"
                :disabled="!done[step.key]"
                :data-test="`setup-next-${step.key}`"
                @click="goNext(step.key)"
              >
                下一步：{{ nextStepTitle(step.key) }}
              </button>
            </div>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.setup {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.setup__ready {
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-success);
  border-radius: var(--cb-radius-md);
  background: var(--cb-success-soft);
}

.setup__ready-title {
  font-size: var(--cb-text-lg);
  font-weight: 600;
  color: var(--cb-success);
}

.setup__ready-text {
  margin-top: var(--cb-space-1);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.setup__status {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.setup__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.setup__steps {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.setup__step {
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.setup__step--active {
  border-color: var(--cb-primary);
}

.setup__step--done {
  border-color: var(--cb-success);
}

.setup__step-head {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  width: 100%;
  padding: var(--cb-space-3);
  border: 0;
  background: none;
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  text-align: left;
  cursor: pointer;
  flex-wrap: wrap;
}

.setup__step-index {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  flex: none;
  border-radius: 50%;
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-xs);
}

.setup__step-title {
  font-weight: 600;
}

.setup__step-desc {
  flex: 1;
  min-width: 0;
}

.setup__step-done {
  color: var(--cb-success);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.setup__step-todo {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.setup__step-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: 0 var(--cb-space-3) var(--cb-space-3);
}

.setup__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--cb-space-3);
}

.setup__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.setup__field--wide {
  grid-column: 1 / -1;
}

.setup__field input,
.setup__field select {
  width: 100%;
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.setup__action {
  align-self: flex-start;
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-primary);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.setup__action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.setup__next {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--cb-space-3);
  border-top: 1px solid var(--cb-border);
  padding-top: var(--cb-space-2);
}

.setup__next-button {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.setup__next-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
