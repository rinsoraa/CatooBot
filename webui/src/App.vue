<script setup lang="ts">
/**
 * 应用外壳：主题注入 + 布局选择 + 实时连接生命周期（§14、§23、§34）。
 *
 * 布局选择由路由决定：`meta.guest` → AuthLayout，其余 → AppLayout。
 */
import { NConfigProvider, darkTheme, lightTheme } from 'naive-ui'
import { computed, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ToastHost from '@/components/ToastHost.vue'
import AppLayout from '@/layouts/AppLayout.vue'
import AuthLayout from '@/layouts/AuthLayout.vue'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { useRealtimeStore } from '@/stores/realtime'
import { useRuntimeStore } from '@/stores/runtime'
import { useNaiveOverrides } from '@/theme/naive'

const app = useAppStore()
const auth = useAuthStore()
const realtime = useRealtimeStore()
const runtime = useRuntimeStore()
const route = useRoute()
const router = useRouter()

const isGuestRoute = computed(() => Boolean(route.meta.guest))
const naiveTheme = computed(() => (app.isDark ? darkTheme : lightTheme))
const overrides = useNaiveOverrides(computed(() => app.isDark))

watch(
  () => auth.isAuthenticated,
  async (authenticated, previous) => {
    if (authenticated) {
      realtime.connect()
      if (!runtime.overview) await runtime.loadInitial()
      return
    }
    realtime.disconnect()
    runtime.clear()
    // A session that dies mid-use (any 401) lands on the v1 login view; the
    // route guard does the same for cold loads (§12).
    if (previous && route.meta.requiresAuth) {
      await router.replace({ name: 'login', query: { redirect: route.fullPath } })
    }
  },
  { immediate: true },
)

onUnmounted(() => realtime.disconnect())
</script>

<template>
  <NConfigProvider :theme="naiveTheme" :theme-overrides="overrides">
    <AuthLayout v-if="isGuestRoute">
      <RouterView />
    </AuthLayout>
    <AppLayout v-else>
      <RouterView />
    </AppLayout>
    <ToastHost />
  </NConfigProvider>
</template>
