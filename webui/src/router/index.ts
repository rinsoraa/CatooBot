/** 路由与登录守卫（§9、§41）。SPA 的路径由 aiohttp 的 fallback 兜底。 */

import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/pages/Dashboard.vue'),
      meta: { requiresAuth: true, title: '总览' },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/pages/Login.vue'),
      meta: { guest: true, title: '登录' },
    },
    {
      path: '/ai',
      component: () => import('@/pages/ai/AiLayout.vue'),
      meta: { requiresAuth: true, section: 'ai' },
      children: [
        { path: '', name: 'ai-overview', component: () => import('@/pages/ai/AiOverview.vue'), meta: { requiresAuth: true, title: 'AI 与模型' } },
        { path: 'providers', name: 'ai-providers', component: () => import('@/pages/ai/AiProviders.vue'), meta: { requiresAuth: true, title: 'AI · 服务商' } },
        { path: 'models', name: 'ai-models', component: () => import('@/pages/ai/AiModels.vue'), meta: { requiresAuth: true, title: 'AI · 模型' } },
        { path: 'roles', name: 'ai-roles', component: () => import('@/pages/ai/AiRoles.vue'), meta: { requiresAuth: true, title: 'AI · 模型用途' } },
        { path: 'failover', name: 'ai-failover', component: () => import('@/pages/ai/AiFailover.vue'), meta: { requiresAuth: true, title: 'AI · 故障转移' } },
        { path: 'usage', name: 'ai-usage', component: () => import('@/pages/ai/AiUsage.vue'), meta: { requiresAuth: true, title: 'AI · 用量' } },
        { path: 'test', name: 'ai-test', component: () => import('@/pages/ai/AiTest.vue'), meta: { requiresAuth: true, title: 'AI · 测试台' } },
        { path: 'setup', name: 'ai-setup', component: () => import('@/pages/ai/AiSetup.vue'), meta: { requiresAuth: true, title: 'AI · 配置向导' } },
      ],
    },
    {
      path: '/system',
      component: () => import('@/pages/system/SystemLayout.vue'),
      meta: { requiresAuth: true, section: 'system' },
      children: [
        { path: '', redirect: { name: 'system-settings' } },
        { path: 'settings', name: 'system-settings', component: () => import('@/pages/system/Settings.vue'), meta: { requiresAuth: true, title: '系统 · 设置' } },
        { path: 'settings/restart-pending', name: 'system-restart-pending', component: () => import('@/pages/system/RestartPending.vue'), meta: { requiresAuth: true, title: '系统 · 等待重启' } },
        { path: 'settings/advanced', name: 'system-advanced', component: () => import('@/pages/system/Advanced.vue'), meta: { requiresAuth: true, title: '系统 · 高级（YAML）' } },
        { path: 'credentials', name: 'system-credentials', component: () => import('@/pages/system/Credentials.vue'), meta: { requiresAuth: true, title: '系统 · 凭据' } },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'not-found',
      component: () => import('@/pages/NotFound.vue'),
      meta: { requiresAuth: true, title: '未找到' },
    },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.ensureLoaded()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: to.fullPath === '/' ? {} : { redirect: to.fullPath } }
  }
  if (to.meta.guest && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
