<script setup lang="ts">
/**
 * 故障转移链（W4 §31-§34、§104）：只做排序交互，不直接调 API。
 *
 * 上移/下移只更新 `v-model:order`（未保存状态由这里显示角标，保存交给
 * 页面 / 父级处理 `save`）。不引入拖拽依赖，按钮排序键盘可达。
 */
import { computed } from 'vue'

import StatusBadge from '@/components/StatusBadge.vue'
import type { ModelItem } from '@/types/ai'

const props = defineProps<{
  items: ModelItem[]
  order: string[]
}>()

const emit = defineEmits<{
  'update:order': [order: string[]]
  save: [order: string[]]
}>()

interface ChainRow {
  name: string
  index: number
  item: ModelItem | null
}

const rows = computed<ChainRow[]>(() => {
  const byName = new Map(props.items.map((model) => [model.name, model]))
  return props.order.map((name, index) => ({ name, index, item: byName.get(name) ?? null }))
})

/** §104：与模型列表的已保存顺序不一致即视为未保存。 */
const dirty = computed(() => {
  const saved = props.items.map((model) => model.name)
  if (props.order.length !== saved.length) return true
  return props.order.some((name, index) => name !== saved[index])
})

function move(index: number, delta: number): void {
  const target = index + delta
  if (target < 0 || target >= props.order.length) return
  const next = [...props.order]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  emit('update:order', next)
}

function onSave(): void {
  emit('save', [...props.order])
}

function badgeState(item: ModelItem | null): 'ok' | 'off' | 'warn' {
  if (item?.in_cooldown) return 'warn'
  return item?.enabled ? 'ok' : 'off'
}

function badgeLabel(item: ModelItem | null): string {
  if (item?.in_cooldown) return '冷却中'
  return item?.enabled ? '已启用' : '已停用'
}
</script>

<template>
  <div class="cb-failover" data-test="failover-list">
    <ol class="cb-failover__list">
      <li
        v-for="row in rows"
        :key="row.name"
        class="cb-failover__row"
        data-test="fb-row"
      >
        <span class="cb-failover__index" aria-hidden="true">{{ row.index + 1 }}</span>

        <div class="cb-failover__main">
          <div class="cb-failover__title">
            <span class="cb-failover__name" data-test="fb-name">{{ row.name }}</span>
            <StatusBadge :state="badgeState(row.item)" :label="badgeLabel(row.item)" />
            <span v-if="row.index === 0" class="cb-failover__chat" data-test="fb-chat">
              当前聊天模型
            </span>
          </div>
          <p class="cb-caption cb-failover__meta">
            {{ row.item?.provider ?? '未知 Provider' }}
            <template v-if="row.item?.model"> · {{ row.item.model }}</template>
          </p>
        </div>

        <div class="cb-failover__actions">
          <button
            type="button"
            class="cb-failover__button"
            :disabled="row.index === 0"
            :aria-label="`上移 ${row.name}`"
            data-test="fb-up"
            @click="move(row.index, -1)"
          >
            上移
          </button>
          <button
            type="button"
            class="cb-failover__button"
            :disabled="row.index === rows.length - 1"
            :aria-label="`下移 ${row.name}`"
            data-test="fb-down"
            @click="move(row.index, 1)"
          >
            下移
          </button>
        </div>
      </li>
    </ol>

    <footer class="cb-failover__footer">
      <p class="cb-failover__state" aria-live="polite">
        <span v-if="dirty" class="cb-failover__dirty" data-test="fb-dirty">未保存</span>
        <span v-else class="cb-caption">顺序与已保存配置一致</span>
      </p>
      <button
        type="button"
        class="cb-failover__save"
        :disabled="!dirty"
        data-test="fb-save"
        @click="onSave"
      >
        保存顺序
      </button>
    </footer>
  </div>
</template>

<style scoped>
.cb-failover {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.cb-failover__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-failover__row {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
  min-width: 0;
}

.cb-failover__index {
  display: grid;
  place-items: center;
  width: 26px;
  height: 26px;
  flex: none;
  border-radius: 50%;
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-xs);
  font-variant-numeric: tabular-nums;
}

.cb-failover__main {
  flex: 1;
  min-width: 0;
}

.cb-failover__title {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex-wrap: wrap;
  min-width: 0;
}

.cb-failover__name {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
  overflow-wrap: anywhere;
}

.cb-failover__chat {
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-primary);
  border-radius: 999px;
  background: var(--cb-primary-soft);
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-failover__meta {
  margin-top: var(--cb-space-1);
  overflow-wrap: anywhere;
}

.cb-failover__actions {
  display: flex;
  gap: var(--cb-space-2);
  flex: none;
}

.cb-failover__button,
.cb-failover__save {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-failover__button:hover:not(:disabled),
.cb-failover__save:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-failover__button:disabled,
.cb-failover__save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.cb-failover__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.cb-failover__dirty {
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-warning);
  border-radius: 999px;
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}
</style>
