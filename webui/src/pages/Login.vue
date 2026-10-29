<script setup lang="ts">
/**
 * 登录页（W3 §9、§26）：只做一件事——把用户名/密码交给后端换取会话。
 *
 * 失败原因永远来自后端 `ApiError.message`（auth store 已原样保存），
 * 前端不编造、不猜测；Enter 提交、提交中禁用按钮、错误区 aria-live。
 */

import { NButton, NInput } from 'naive-ui'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const username = ref('')
const password = ref('')

const canSubmit = computed(() => username.value.trim().length > 0 && password.value.length > 0)

/** 登录成功后回到守卫记录的原始地址；只接受站内路径，避免开放重定向。 */
function redirectTarget(): string {
  const target = route.query.redirect
  if (typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')) return target
  return '/'
}

async function submit(): Promise<void> {
  if (auth.loading || !canSubmit.value) return
  const ok = await auth.login(username.value.trim(), password.value)
  if (!ok) return
  password.value = ''
  await router.replace(redirectTarget())
}
</script>

<template>
  <section class="login" data-testid="login-view">
    <div class="cb-card login__card">
      <header class="login__head">
        <h1 class="login__title">登录 CatooBot</h1>
        <p class="cb-muted login__subtitle">使用控制台账号进入管理界面</p>
      </header>

      <form class="login__form" @submit.prevent="submit">
        <div class="login__field">
          <span class="cb-caption">用户名</span>
          <NInput
            v-model:value="username"
            :input-props="{ name: 'username', autocomplete: 'username' }"
            placeholder="用户名"
            :disabled="auth.loading"
          />
        </div>

        <div class="login__field">
          <span class="cb-caption">密码</span>
          <NInput
            v-model:value="password"
            type="password"
            :input-props="{ name: 'password', autocomplete: 'current-password' }"
            placeholder="密码"
            :disabled="auth.loading"
          />
        </div>

        <p v-if="auth.error" class="login__error" role="alert" aria-live="polite" data-testid="login-error">
          {{ auth.error }}
        </p>

        <NButton
          attr-type="submit"
          type="primary"
          block
          data-testid="login-submit"
          :loading="auth.loading"
          :disabled="auth.loading || !canSubmit"
        >
          登录
        </NButton>
      </form>

      <p class="cb-faint login__footer">CatooBot 控制台</p>
    </div>
  </section>
</template>

<style scoped>
.login {
  display: flex;
  justify-content: center;
  width: 100%;
}

.login__card {
  width: min(400px, 100%);
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-5);
}

.login__head {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.login__title {
  font-size: var(--cb-text-xl);
}

.login__form {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.login__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.login__error {
  margin: 0;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
}

.login__footer {
  text-align: center;
  font-size: var(--cb-text-xs);
}
</style>
