<script setup lang="ts">
/**
 * AI 概览（W4 §6/§7/§63/§116）：一屏看懂状态，并按 checks 给出下一步。
 *
 * 所有数字来自 `GET /api/v1/ai/status`（`aiStore.status`），拿不到的显示「—」；
 * 冷却倒计时只是视觉提示，真实状态以后端刷新为准。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import MetricCard from '@/components/MetricCard.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { useAiStore } from '@/stores/ai'
import type { AiHealth } from '@/types/ai'

const aiStore = useAiStore()

const HEALTH_META: Record<AiHealth, { state: StatusState; label: string; description: string }> = {
  ready: {
    state: 'ok',
    label: '就绪',
    description: 'AI 已就绪：Provider、凭据、模型与默认聊天模型都已配置。',
  },
  degraded: {
    state: 'warn',
    label: '降级',
    description: 'AI 可用但不完整：部分模型处于冷却或缺少 Key，回复可能变慢或发生故障转移。',
  },
  unavailable: {
    state: 'error',
    label: '不可用',
    description: 'AI 当前不可用：请检查 Provider、API Key 与模型配置。',
  },
  not_configured: {
    state: 'off',
    label: '尚未配置',
    description: '尚未完成 AI 配置：按下面的下一步提示依次完成即可。',
  },
}

const status = computed(() => aiStore.status)

const health = computed(() => {
  const current = status.value
  if (!current) return { state: 'idle' as StatusState, label: '状态未知', description: '正在读取 AI 状态…' }
  return HEALTH_META[current.status]
})

const loading = computed(() => aiStore.loading && !status.value)

// ------------------------------------------------------------------- 指标卡
const providerValue = computed(() =>
  status.value ? `${status.value.providers.with_key}/${status.value.providers.total}` : null,
)
const providerHint = computed(() => {
  const providers = status.value?.providers
  if (!providers) return ''
  const parts = [`共 ${providers.total} 个`, `${providers.with_key} 个已有 Key`]
  if (providers.missing_key.length > 0) parts.push(`缺少 Key：${providers.missing_key.join('、')}`)
  return parts.join(' · ')
})

const modelValueText = computed(() =>
  status.value ? `${status.value.models.enabled}/${status.value.models.total}` : null,
)
const modelHint = computed(() => {
  const models = status.value?.models
  if (!models) return ''
  return `${models.disabled} 个停用 · ${models.usable} 个当前可用`
})

const chatModelValue = computed(() => status.value?.chat_model || null)

const fallbackValue = computed(() => status.value?.fallback_chain.length ?? null)
const fallbackHint = computed(() => {
  const chain = status.value?.fallback_chain
  if (!chain || chain.length === 0) return ''
  return chain.join(' → ')
})

const rateLimitedValue = computed(() => status.value?.errors.rate_limited ?? null)
const serverErrorValue = computed(() => status.value?.errors.server_errors ?? null)
const cooldownValue = computed(() => status.value?.models.cooldown ?? null)

// -------------------------------------------------------------- 下一步提示
interface NextStep {
  key: string
  title: string
  description: string
  action: string
  to: string
}

const steps = computed<NextStep[]>(() => {
  const checks = status.value?.checks
  if (!checks) return []
  const items: NextStep[] = []
  if (!checks.has_provider) {
    items.push({
      key: 'provider',
      title: '尚未配置 AI Provider',
      description: '先添加一个 OpenAI 兼容的服务商（接口地址 + API Key）。',
      action: '添加 Provider',
      to: '/ai/providers?create=1',
    })
  }
  if (!checks.has_credential) {
    items.push({
      key: 'credential',
      title: 'Provider 缺少 API Key',
      description: '为已创建的服务商配置凭据，Key 只会写入服务端 .env。',
      action: '配置凭据',
      to: '/ai/providers',
    })
  }
  if (!checks.has_model) {
    items.push({
      key: 'model',
      title: '尚未配置模型',
      description: '在服务商下添加真实模型 ID，供聊天与各用途使用。',
      action: '添加模型',
      to: '/ai/models?create=1',
    })
  }
  if (!checks.chat_bound) {
    items.push({
      key: 'chat',
      title: '尚未设置默认聊天模型',
      description: '把聊天用途绑定到某个模型后，机器人才会真正回复。',
      action: '配置模型用途',
      to: '/ai/roles',
    })
  }
  return items
})

const allReady = computed(() => steps.value.length === 0 && Boolean(status.value))

// ------------------------------------------------------------- 冷却倒计时
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

// 后端给的是「剩余秒数」（路由器的时钟是单调时钟，不能当墙钟时间用）；
// 这里只把它按本地经过时间往下走，刷新后仍以后端值为准。
function remainingSeconds(remaining: number): number {
  if (!remaining) return 0
  const loadedAt = aiStore.lastLoadedAt || now.value
  const elapsed = Math.max(0, (now.value - loadedAt) / 1000)
  return Math.max(0, Math.ceil(remaining - elapsed))
}

onMounted(() => {
  now.value = Date.now()
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
  if (!aiStore.lastLoadedAt && !aiStore.loading) void aiStore.loadAll()
})

onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer)
  timer = null
})
</script>

<template>
  <div class="ai-overview" data-test="ai-overview">
    <section class="cb-card ai-overview__health" data-test="ai-health">
      <div class="ai-overview__health-head">
        <StatusBadge :state="health.state" :label="health.label" data-test="ai-status-badge" />
        <p class="ai-overview__health-text">{{ health.description }}</p>
      </div>
      <RouterLink class="cb-link-button" to="/ai/setup" data-test="run-setup">
        重新运行配置向导
      </RouterLink>
    </section>

    <div class="cb-grid cb-grid--cards" data-test="ai-metrics">
      <MetricCard label="Provider" :value="providerValue" :hint="providerHint" :loading="loading" />
      <MetricCard label="模型" :value="modelValueText" :hint="modelHint" :loading="loading" />
      <MetricCard
        label="当前聊天模型"
        :value="chatModelValue"
        hint="故障转移链的第一位"
        :loading="loading"
      />
      <MetricCard
        label="Fallback 数量"
        :value="fallbackValue"
        :hint="fallbackHint"
        :loading="loading"
      />
      <MetricCard
        label="429 限流"
        :value="rateLimitedValue"
        hint="近 7 天被限流的次数"
        :loading="loading"
      />
      <MetricCard
        label="5xx 错误"
        :value="serverErrorValue"
        hint="近 7 天服务端错误次数"
        :loading="loading"
      />
      <MetricCard
        label="冷却中"
        :value="cooldownValue"
        hint="等待冷却结束的模型数"
        :loading="loading"
      />
    </div>

    <section class="cb-card ai-overview__next" data-test="ai-next-steps">
      <SectionHeader
        title="下一步"
        description="按顺序完成，AI 就能正常回复。"
      >
        <template #actions>
          <RouterLink class="cb-link-button" to="/ai/setup" data-test="next-setup">
            配置向导
          </RouterLink>
        </template>
      </SectionHeader>

      <p v-if="allReady" class="ai-overview__ready" data-test="next-ready">✓ AI Ready</p>
      <ul v-else-if="steps.length > 0" class="ai-overview__steps">
        <li v-for="step in steps" :key="step.key" class="ai-overview__step" :data-test="`next-${step.key}`">
          <div class="ai-overview__step-text">
            <p class="ai-overview__step-title">{{ step.title }}</p>
            <p class="cb-caption">{{ step.description }}</p>
          </div>
          <RouterLink class="cb-link-button" :to="step.to" :data-test="`next-${step.key}-action`">
            {{ step.action }}
          </RouterLink>
        </li>
      </ul>
      <p v-else class="cb-caption">正在读取 AI 状态…</p>
    </section>

    <section v-if="status && status.cooldown_models.length > 0" class="cb-card" data-test="ai-cooldowns">
      <SectionHeader
        title="冷却中的模型"
        :description="`${status.cooldown_models.length} 个模型处于冷却，稍后自动恢复`"
      />
      <ul class="ai-overview__cooldowns">
        <li v-for="item in status.cooldown_models" :key="item.name" class="ai-overview__cooldown">
          <span class="ai-overview__cooldown-name">{{ item.name }}</span>
          <span class="cb-caption">
            约 {{ remainingSeconds(item.remaining_seconds) }} 秒后恢复
            <template v-if="remainingSeconds(item.remaining_seconds) === 0">（等待后端刷新确认）</template>
          </span>
        </li>
      </ul>
      <p class="cb-caption">倒计时仅为视觉提示，实际状态以刷新到的后端数据为准。</p>
    </section>
  </div>
</template>

<style scoped>
.ai-overview {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-5);
  min-width: 0;
}

.ai-overview__health-head {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  flex-wrap: wrap;
}

.ai-overview__health-text {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.ai-overview__health {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-4);
  flex-wrap: wrap;
}

.ai-overview__next {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.ai-overview__ready {
  font-size: var(--cb-text-lg);
  color: var(--cb-success);
}

.ai-overview__steps,
.ai-overview__cooldowns {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.ai-overview__step {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-4);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.ai-overview__step-title {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.ai-overview__cooldown {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding: var(--cb-space-2) 0;
  border-bottom: 1px solid var(--cb-border);
}

.ai-overview__cooldown:last-child {
  border-bottom: none;
}

.ai-overview__cooldown-name {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.cb-link-button {
  display: inline-flex;
  align-items: center;
  flex: none;
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.cb-link-button:hover {
  border-color: var(--cb-primary);
  text-decoration: none;
}
</style>
