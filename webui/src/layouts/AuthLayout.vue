<script setup lang="ts">
/** 认证壳（§23）：居中卡片，无侧栏 / 顶栏，只承载登录表单。 */
</script>

<template>
  <main class="cb-auth">
    <div class="cb-auth__panel">
      <p class="cb-auth__wordmark">CatooBot</p>
      <slot />
    </div>
  </main>
</template>

<style scoped>
.cb-auth {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--cb-space-5);
  background: var(--cb-bg);
}

.cb-auth__panel {
  width: 100%;
  max-width: 420px;
}

.cb-auth__wordmark {
  margin-bottom: var(--cb-space-5);
  text-align: center;
  font-size: var(--cb-text-2xl);
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--cb-primary-strong);
}
</style>
