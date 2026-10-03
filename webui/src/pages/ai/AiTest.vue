<script setup lang="ts">
/**
 * 测试台（W4 §36-§37）：连通性诊断，绝不写记忆/对话/关系。
 *
 * Provider 与模型都来自 store 的真实配置；测试调用走完整路由器
 * （`aiStore.testModel`），结果统一用 TestResultPanel 展示。
 */
import { computed, onMounted, ref } from 'vue'

import ErrorState from '@/components/ErrorState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import TestResultPanel from '@/components/ai/TestResultPanel.vue'
import { useAiStore } from '@/stores/ai'
import type { TestResult } from '@/types/ai'

const DEFAULT_PROMPT = '你好，请简单回复一句“测试成功”。'

const aiStore = useAiStore()
const selectedProvider = ref('')
const selectedModel = ref('')
const prompt = ref(DEFAULT_PROMPT)
const result = ref<TestResult | null>(null)
const loading = ref(false)
const pageLoading = ref(false)

const modelsForProvider = computed(() => {
  const provider = aiStore.providers.find((item) => item.name === selectedProvider.value)
  return provider?.models ?? []
})

async function load(): Promise<void> {
  pageLoading.value = true
  await Promise.all([aiStore.loadProviders(), aiStore.loadModels()])
  pageLoading.value = false
  if (!selectedProvider.value) {
    const first = aiStore.providers.find((item) => item.models.length > 0) ?? aiStore.providers[0]
    if (first) {
      selectedProvider.value = first.name
      selectedModel.value = first.models[0] ?? ''
    }
  }
}

onMounted(load)

function onProviderChange(event: Event): void {
  selectedProvider.value = (event.target as HTMLSelectElement).value
  selectedModel.value = modelsForProvider.value[0] ?? ''
  result.value = null
}

function onModelChange(event: Event): void {
  selectedModel.value = (event.target as HTMLSelectElement).value
  result.value = null
}

async function send(): Promise<void> {
  if (!selectedModel.value || loading.value) return
  loading.value = true
  result.value = await aiStore.testModel(selectedModel.value, prompt.value)
  loading.value = false
}
</script>

<template>
  <div class="test" data-test="ai-test">
    <PageHeader title="测试台" subtitle="用一条真实请求检查模型连通性与故障转移结果。" />

    <section class="test__notice" role="note" data-test="test-notice">
      <p class="test__notice-title">这是连通性诊断</p>
      <p>
        测试请求<strong>不会写入记忆、对话或关系</strong>，也不会修改任何数据；它只验证当前配置能否得到模型回复。
      </p>
    </section>

    <section class="test__panel cb-card">
      <SectionHeader title="发起测试" description="选择 Provider 与模型，发送一条诊断 prompt。" />

      <ErrorState v-if="aiStore.error && !result" :message="aiStore.error" @retry="load" />

      <p v-if="pageLoading && aiStore.providers.length === 0" class="cb-muted">正在读取 Provider…</p>

      <form class="test__form" data-test="test-form" @submit.prevent="send">
        <label class="test__field">
          <span class="cb-caption">Provider</span>
          <select
            :value="selectedProvider"
            required
            data-test="test-provider"
            @change="onProviderChange"
          >
            <option value="" disabled>请选择 Provider</option>
            <option v-for="provider in aiStore.providers" :key="provider.name" :value="provider.name">
              {{ provider.name }}{{ provider.has_key ? '' : '（缺少 Key）' }}
            </option>
          </select>
        </label>

        <label class="test__field">
          <span class="cb-caption">模型</span>
          <select
            :value="selectedModel"
            required
            :disabled="modelsForProvider.length === 0"
            data-test="test-model"
            @change="onModelChange"
          >
            <option value="" disabled>请选择模型</option>
            <option v-for="name in modelsForProvider" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>

        <label class="test__field test__field--wide">
          <span class="cb-caption">Prompt</span>
          <textarea
            v-model="prompt"
            rows="3"
            class="test__textarea"
            data-test="test-prompt"
          />
        </label>

        <div class="test__actions">
          <button
            type="submit"
            class="test__submit"
            :disabled="!selectedModel || loading"
            data-test="test-send"
          >
            {{ loading ? '正在测试…' : '发送测试' }}
          </button>
        </div>
      </form>
    </section>

    <section class="test__panel cb-card" data-test="test-result">
      <SectionHeader title="测试结果" description="成功与失败都展示后端原始字段。" />
      <TestResultPanel :result="result" :loading="loading" title="模型测试" />
    </section>
  </div>
</template>

<style scoped>
.test {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.test__notice {
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-info);
  border-radius: var(--cb-radius-md);
  background: var(--cb-info-soft);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.test__notice-title {
  font-weight: 600;
  margin-bottom: var(--cb-space-1);
}

.test__panel {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.test__form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cb-space-3);
}

.test__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.test__field--wide {
  grid-column: 1 / -1;
}

.test__field select,
.test__textarea {
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

.test__actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
}

.test__submit {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-primary);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.test__submit:hover:not(:disabled) {
  border-color: var(--cb-primary-strong);
}

.test__submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
