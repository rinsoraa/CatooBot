<script setup lang="ts">
/** 页面标题（§30）：每个页面唯一的 h1，右侧放页面级操作。 */
withDefaults(defineProps<{ title: string; subtitle?: string }>(), { subtitle: '' })
</script>

<template>
  <div class="cb-page-header">
    <div class="cb-page-header__text">
      <h1 class="cb-page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="cb-page-header__subtitle">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="cb-page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.cb-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-page-header__title {
  font-size: var(--cb-text-2xl);
  color: var(--cb-text);
}

.cb-page-header__subtitle {
  margin-top: var(--cb-space-1);
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
}

.cb-page-header__actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  flex: none;
}
</style>
