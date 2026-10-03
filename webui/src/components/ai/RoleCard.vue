<script setup lang="ts">
/**
 * 角色卡片（W4 §27-§30、§103）：中文角色名 / 当前绑定 / 重启提示 / 折叠说明。
 *
 * 只负责展示与发意图：切换模型 emit 出去，由页面调用 store，组件不改状态。
 */
import { computed } from 'vue'

import StatusBadge from '@/components/StatusBadge.vue'
import type { ModelItem, RoleItem } from '@/types/ai'
import { roleDescriptor } from '@/types/ai'

const props = withDefaults(
  defineProps<{
    item: RoleItem
    models: ModelItem[]
    busy?: boolean
  }>(),
  { busy: false },
)

const emit = defineEmits<{ change: [model: string] }>()

const descriptor = computed(() => roleDescriptor(props.item.role))
/** §27：优先展示 Core 提供的中文名，未知角色回退到角色键。 */
const label = computed(() => descriptor.value?.label ?? props.item.role)
const description = computed(() => descriptor.value?.description ?? '')
const restartHint = computed(() => descriptor.value?.restartHint ?? '')

/** 绑定值不在模型列表里时也要能显示出来（例如模型刚被删除）。 */
const orphanModel = computed(() => {
  if (!props.item.model) return ''
  return props.models.some((model) => model.name === props.item.model) ? '' : props.item.model
})

function onChange(event: Event): void {
  const target = event.target as HTMLSelectElement
  emit('change', target.value)
}
</script>

<template>
  <article
    class="cb-role-card"
    :class="{ 'cb-role-card--busy': busy }"
    :aria-busy="busy ? 'true' : undefined"
    :data-role="item.role"
    data-test="role-card"
  >
    <header class="cb-role-card__head">
      <h3 class="cb-role-card__title">{{ label }}</h3>
      <StatusBadge
        v-if="item.model"
        state="ok"
        label="已绑定"
      />
      <StatusBadge v-else state="warn" label="未配置" />
    </header>

    <p class="cb-role-card__bound">
      <span class="cb-caption">当前绑定</span>
      <span v-if="item.model" class="cb-role-card__model" data-test="role-model">{{ item.model }}</span>
      <span v-else class="cb-role-card__unset" data-test="role-unset">⚠ 未配置</span>
    </p>

    <p v-if="item.restart_required" class="cb-role-card__restart" data-test="role-restart">
      已保存 · 重启后生效
    </p>

    <label class="cb-role-card__field">
      <span class="cb-caption">更换模型</span>
      <select
        class="cb-role-card__select"
        :value="item.model"
        :disabled="busy"
        :aria-label="`${label} 的模型`"
        data-test="role-select"
        @change="onChange"
      >
        <option value="">解绑（不指定模型）</option>
        <option v-if="orphanModel" :value="orphanModel">{{ orphanModel }}（不在模型列表）</option>
        <option v-for="model in models" :key="model.name" :value="model.name">
          {{ model.name }}{{ model.enabled ? '' : '（已停用）' }}
        </option>
      </select>
    </label>

    <p v-if="busy" class="cb-role-card__busy cb-caption" role="status">正在保存…</p>

    <details class="cb-role-card__details" data-test="role-details">
      <summary class="cb-role-card__summary">ⓘ 这个模型负责什么？</summary>
      <div class="cb-role-card__details-body">
        <p class="cb-role-card__description">{{ description }}</p>
        <p class="cb-role-card__hint">{{ restartHint }}</p>
        <p class="cb-caption cb-role-card__key">
          高级信息 · 内部键 <code class="cb-code" data-test="role-key">{{ item.key }}</code>
        </p>
      </div>
    </details>
  </article>
</template>

<style scoped>
.cb-role-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
  min-width: 0;
}

.cb-role-card--busy {
  border-color: var(--cb-primary);
}

.cb-role-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-role-card__title {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.cb-role-card__bound {
  display: flex;
  align-items: baseline;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-role-card__model {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.cb-role-card__unset {
  font-size: var(--cb-text-sm);
  color: var(--cb-warning);
}

.cb-role-card__restart {
  padding: var(--cb-space-1) var(--cb-space-2);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.cb-role-card__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.cb-role-card__select {
  width: 100%;
  padding: var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
}

.cb-role-card__select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.cb-role-card__busy {
  color: var(--cb-text-muted);
}

.cb-role-card__details {
  border-top: 1px solid var(--cb-border);
  padding-top: var(--cb-space-2);
}

.cb-role-card__summary {
  cursor: pointer;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.cb-role-card__details-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding-top: var(--cb-space-2);
}

.cb-role-card__description {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.cb-role-card__hint {
  font-size: var(--cb-text-xs);
  color: var(--cb-text-muted);
}

.cb-role-card__key {
  overflow-wrap: anywhere;
}
</style>
