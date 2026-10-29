<script lang="ts">
/** 工具列表项（W5 §34-§35）：开关必须经服务端确认，`restart_required` 如实展示。 */
export interface ToolCardModel {
  name: string
  display_name: string
  description: string
  category: string
  risk_level: string
  enabled: boolean
  requires_credentials: boolean
  has_credential: boolean | null
  timeout: number | null
  cache_ttl_seconds: number | null
  calls: number | null
  failures: number | null
  last_used_at: number | null
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { toolsApi } from '@/api/abilities'
import { errorMessage } from '@/api/client'
import StatusBadge, { type StatusState } from '@/components/StatusBadge.vue'
import { toast } from '@/composables/toast'
import type { ToolRow } from '@/types/domain'

const props = defineProps<{ tool: ToolRow }>()
const emit = defineEmits<{ updated: [] }>()

const RISK_LABELS: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
}

const enabled = ref(props.tool.enabled)
const saving = ref(false)
const restartRequired = ref(false)

watch(
  () => props.tool.enabled,
  (value) => {
    enabled.value = value
  },
)

const riskLabel = computed(() => RISK_LABELS[props.tool.risk_level] ?? (props.tool.risk_level || '未知'))
const riskState = computed<StatusState>(() => {
  if (props.tool.risk_level === 'high') return 'error'
  if (props.tool.risk_level === 'medium') return 'warn'
  return 'idle'
})
const credentialText = computed(() => {
  if (props.tool.has_credential === null) return '—'
  return props.tool.has_credential ? '已就绪' : '缺失'
})
const credentialState = computed<StatusState>(() => {
  if (props.tool.has_credential === null) return 'idle'
  return props.tool.has_credential ? 'ok' : 'error'
})

function formatNumber(value: number | null): string {
  return value === null || value === undefined ? '—' : String(value)
}

function formatDateTime(seconds: number | null): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '从未使用'
  const millis = seconds > 1e12 ? seconds : seconds * 1000
  const date = new Date(millis)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (value: number): string => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function toggle(): Promise<void> {
  if (saving.value) return
  const next = !enabled.value
  enabled.value = next
  saving.value = true
  restartRequired.value = false
  try {
    const result = await toolsApi.update(props.tool.name, { enabled: next })
    if (result.restart_required) restartRequired.value = true
    toast.success(next ? '工具已启用' : '工具已停用', props.tool.display_name || props.tool.name)
    emit('updated')
  } catch (caught) {
    enabled.value = !next
    toast.error('操作失败', errorMessage(caught))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <article class="tool-card cb-card" :data-test="`tool-card-${tool.name}`">
    <header class="tool-card__head">
      <div class="tool-card__title">
        <h3 class="tool-card__name">{{ tool.display_name || tool.name }}</h3>
        <code class="tool-card__id">{{ tool.name }}</code>
      </div>
      <button
        type="button"
        class="tool-card__switch"
        role="switch"
        :aria-checked="enabled ? 'true' : 'false'"
        :aria-label="`启用 ${tool.display_name || tool.name}`"
        :disabled="saving"
        :data-test="`tool-toggle-${tool.name}`"
        @click="toggle"
      >
        <span class="tool-card__switch-track" aria-hidden="true">
          <span class="tool-card__switch-thumb" />
        </span>
        <span class="tool-card__switch-label">{{ enabled ? '已启用' : '已停用' }}</span>
      </button>
    </header>

    <p class="tool-card__description">{{ tool.description || '（无描述）' }}</p>

    <dl class="tool-card__facts">
      <div class="tool-card__fact">
        <dt>类别</dt>
        <dd>{{ tool.category || '—' }}</dd>
      </div>
      <div class="tool-card__fact">
        <dt>风险等级</dt>
        <dd data-test="tool-risk">
          <StatusBadge :state="riskState" :label="riskLabel" />
        </dd>
      </div>
      <div class="tool-card__fact">
        <dt>凭据</dt>
        <dd data-test="tool-credential">
          <StatusBadge :state="credentialState" :label="credentialText" />
        </dd>
      </div>
      <div class="tool-card__fact">
        <dt>调用 / 失败</dt>
        <dd data-test="tool-counts">
          {{ formatNumber(tool.calls) }} / {{ formatNumber(tool.failures) }}
        </dd>
      </div>
      <div class="tool-card__fact">
        <dt>最近使用</dt>
        <dd data-test="tool-last-used">{{ formatDateTime(tool.last_used_at) }}</dd>
      </div>
    </dl>

    <p v-if="restartRequired" class="tool-card__restart" data-test="tool-restart-hint">
      ⚠ 重启后生效
    </p>
  </article>
</template>

<style scoped>
.tool-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
}

.tool-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-3);
}

.tool-card__name {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.tool-card__id {
  display: inline-block;
  margin-top: var(--cb-space-1);
  color: var(--cb-text-faint);
}

.tool-card__description {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.tool-card__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(128px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
}

.tool-card__fact {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.tool-card__fact dt {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.tool-card__fact dd {
  margin: 0;
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  font-variant-numeric: tabular-nums;
}

.tool-card__switch {
  display: inline-flex;
  align-items: center;
  gap: var(--cb-space-2);
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
  flex: none;
}

.tool-card__switch:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.tool-card__switch[aria-checked='true'] {
  border-color: var(--cb-success);
  background: var(--cb-success-soft);
  color: var(--cb-success);
}

.tool-card__switch-track {
  position: relative;
  width: 26px;
  height: 14px;
  border-radius: 999px;
  background: var(--cb-border-strong);
  transition: background-color 0.15s ease;
}

.tool-card__switch[aria-checked='true'] .tool-card__switch-track {
  background: var(--cb-success);
}

.tool-card__switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--cb-surface);
  transition: transform 0.15s ease;
}

.tool-card__switch[aria-checked='true'] .tool-card__switch-thumb {
  transform: translateX(12px);
}

.tool-card__restart {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}
</style>
