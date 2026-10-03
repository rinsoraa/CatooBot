<script setup lang="ts">
/**
 * 配置分组（W4 §41、§117）：组标题 + 说明 + 徽标插槽 + 字段插槽。
 *
 * 字段本体由页面用 `v-for` 渲染 `ConfigField` 后放进默认插槽，
 * 这样分组组件不需要知道草稿/生效状态。
 */
import type { ConfigFieldMeta } from '@/types/config'

withDefaults(
  defineProps<{
    title: string
    description?: string
    fields: ConfigFieldMeta[]
  }>(),
  { description: '' },
)
</script>

<template>
  <section class="cb-config-section" :data-area="title" data-test="config-section">
    <div class="cb-config-section__head">
      <div class="cb-config-section__text">
        <h3 class="cb-config-section__title">{{ title }}</h3>
        <p v-if="description" class="cb-config-section__description">{{ description }}</p>
      </div>
      <div class="cb-config-section__badges">
        <slot name="badge" />
        <span class="cb-config-section__count">共 {{ fields.length }} 项</span>
      </div>
    </div>
    <div class="cb-config-section__body">
      <slot />
    </div>
  </section>
</template>

<style scoped>
.cb-config-section {
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-config-section__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-3);
  margin-bottom: var(--cb-space-2);
}

.cb-config-section__title {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
}

.cb-config-section__description {
  margin-top: var(--cb-space-1);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-config-section__badges {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex: none;
}

.cb-config-section__count {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-config-section__body {
  display: flex;
  flex-direction: column;
}
</style>
