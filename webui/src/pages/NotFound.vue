<script setup lang="ts">
/** 404 页（W3 §27）：应用壳内的简单兜底，引导回总览。 */
</script>

<template>
  <section class="notfound cb-card" data-testid="not-found">
    <p class="notfound__code cb-faint">404</p>
    <h1 class="notfound__title">页面不存在</h1>
    <p class="cb-muted">这个地址没有对应的页面，可能链接已失效。</p>
    <RouterLink class="notfound__link" to="/">返回总览</RouterLink>
  </section>
</template>

<style scoped>
.notfound {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  align-items: flex-start;
}

.notfound__code {
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-2xl);
  line-height: 1;
}

.notfound__link {
  font-weight: 600;
}
</style>
