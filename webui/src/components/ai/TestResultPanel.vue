<script setup lang="ts">
/**
 * 统一的测试结果面板（§6 / §16）：Provider 测试与 Model 测试共用一个形状。
 *
 * 状态永远用文字表达（成功 / 失败），颜色只是辅助；后端 message 原文照登，
 * 不做任何改写或翻译；`error_class` 推导出的 HTTP 状态会标注来源。
 */
import { computed } from 'vue'

import LoadingState from '@/components/LoadingState.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import type { TestResult } from '@/types/ai'

const props = withDefaults(
  defineProps<{
    result: TestResult | null
    loading?: boolean
    title?: string
  }>(),
  {
    loading: false,
    title: '测试结果',
  },
)

const replyText = computed(() => props.result?.response || props.result?.reply || '')
const hasReply = computed(() => replyText.value.length > 0)
const hasMessage = computed(() => Boolean(props.result?.message))
const modelNote = computed(() => {
  const result = props.result
  if (!result) return ''
  const providerModel = result.provider_model ?? ''
  if (!providerModel || providerModel === result.model) return ''
  return providerModel
})
const httpNote = computed(() => {
  const result = props.result
  if (!result) return ''
  return result.http_status_source === 'error_class'
    ? '该状态码由错误分类推导，上游没有返回 HTTP 状态'
    : ''
})
</script>

<template>
  <section class="test-result" data-test="test-result-panel" :aria-busy="loading ? 'true' : undefined">
    <h3 class="test-result__title">{{ title }}</h3>

    <LoadingState v-if="loading" label="正在测试…" :rows="2" />

    <template v-else-if="result">
      <div class="test-result__status">
        <StatusBadge
          :state="result.ok ? 'ok' : 'error'"
          :label="result.ok ? '成功' : '失败'"
        />
        <span v-if="result.provider" class="cb-caption">服务商：{{ result.provider }}</span>
      </div>

      <dl class="test-result__facts">
        <div class="test-result__fact">
          <dt class="cb-caption">HTTP 状态</dt>
          <dd data-test="test-http-status">
            <template v-if="result.http_status !== null">
              {{ result.http_status }}
              <span v-if="httpNote" class="test-result__note" data-test="test-http-note">（{{ httpNote }}）</span>
            </template>
            <template v-else>—</template>
          </dd>
        </div>
        <div class="test-result__fact">
          <dt class="cb-caption">延迟</dt>
          <dd data-test="test-latency">{{ result.latency_ms }} ms</dd>
        </div>
        <div class="test-result__fact">
          <dt class="cb-caption">模型（别名）</dt>
          <dd data-test="test-model">{{ result.model || '—' }}</dd>
        </div>
        <div v-if="modelNote" class="test-result__fact">
          <dt class="cb-caption">实际作答模型</dt>
          <dd data-test="test-provider-model">{{ modelNote }}</dd>
        </div>
        <div class="test-result__fact">
          <dt class="cb-caption">错误类型</dt>
          <dd data-test="test-error-type">{{ result.error_type || '—' }}</dd>
        </div>
      </dl>

      <div v-if="hasMessage" class="test-result__block">
        <p class="cb-caption">错误信息</p>
        <p class="test-result__message" data-test="test-message">{{ result.message }}</p>
      </div>

      <div v-if="hasReply" class="test-result__block">
        <p class="cb-caption">模型回复</p>
        <pre class="cb-code test-result__reply" data-test="test-reply">{{ replyText }}</pre>
      </div>
    </template>

    <p v-else class="cb-caption">暂无测试结果。</p>
  </section>
</template>

<style scoped>
.test-result {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.test-result__title {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.test-result__status {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.test-result__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.test-result__fact dd {
  margin: var(--cb-space-1) 0 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.test-result__note {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.test-result__block {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.test-result__message {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.test-result__reply {
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 320px;
  overflow-y: auto;
}
</style>
