<script setup lang="ts">
/** 空态（§32）：说明为什么为空，并提供下一步动作插槽。 */
withDefaults(defineProps<{ title?: string; description?: string }>(), {
  title: '暂无数据',
  description: '',
})
</script>

<template>
  <div class="cb-empty">
    <p class="cb-empty__title">{{ title }}</p>
    <p v-if="description" class="cb-empty__description">{{ description }}</p>
    <div v-if="$slots.default" class="cb-empty__action">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.cb-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--cb-space-2);
  padding: var(--cb-space-6) var(--cb-space-4);
  border: 1px dashed var(--cb-border-strong);
  border-radius: var(--cb-radius-md);
  text-align: center;
}

.cb-empty__title {
  font-size: var(--cb-text-md);
  color: var(--cb-text);
}

.cb-empty__description {
  font-size: var(--cb-text-sm);
  color: var(--cb-text-muted);
  max-width: 46ch;
}

.cb-empty__action {
  margin-top: var(--cb-space-2);
}
</style>
