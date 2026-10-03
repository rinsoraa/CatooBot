<script setup lang="ts">
/** 区块标题（§30）：页面内一级区块使用，标题 + 说明 + 右侧操作。 */
withDefaults(defineProps<{ title: string; description?: string }>(), { description: '' })
</script>

<template>
  <div class="cb-section-header">
    <div class="cb-section-header__text">
      <h2 class="cb-section-header__title">{{ title }}</h2>
      <p v-if="description" class="cb-section-header__description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="cb-section-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.cb-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-section-header__title {
  font-size: var(--cb-text-lg);
  color: var(--cb-text);
}

.cb-section-header__description {
  margin-top: var(--cb-space-1);
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.cb-section-header__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex: none;
}
</style>
