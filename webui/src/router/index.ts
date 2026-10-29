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
        { path: 'prompts', name: 'ai-prompts', component: () => import('@/pages/ai/AiPrompts.vue'), meta: { requiresAuth: true, title: 'AI · 提示词' } },
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
        { path: 'logs', name: 'system-logs', component: () => import('@/pages/system/Logs.vue'), meta: { requiresAuth: true, title: '系统 · 日志' } },
        { path: 'runtime', name: 'system-runtime', component: () => import('@/pages/system/Runtime.vue'), meta: { requiresAuth: true, title: '系统 · Runtime' } },
      ],
    },
    {
      path: '/character',
      component: () => import('@/pages/character/CharacterLayout.vue'),
      meta: { requiresAuth: true, section: 'character' },
      children: [
        { path: '', name: 'character', component: () => import('@/pages/character/Character.vue'), meta: { requiresAuth: true, title: '角色' } },
        { path: 'world', name: 'character-world', component: () => import('@/pages/character/World.vue'), meta: { requiresAuth: true, title: '角色 · 世界' } },
        { path: 'world/timeline', name: 'character-world-timeline', component: () => import('@/pages/character/WorldTimeline.vue'), meta: { requiresAuth: true, title: '角色 · 世界时间线' } },
      ],
    },
    {
      path: '/social',
      component: () => import('@/pages/social/SocialLayout.vue'),
      meta: { requiresAuth: true, section: 'social' },
      children: [
        { path: '', name: 'social-users', component: () => import('@/pages/social/SocialUsers.vue'), meta: { requiresAuth: true, title: '社交 · 用户' } },
        { path: 'users/:personId', name: 'social-user', component: () => import('@/pages/social/SocialUserDetail.vue'), meta: { requiresAuth: true, title: '社交 · 人物' } },
        { path: 'groups', name: 'social-groups', component: () => import('@/pages/social/SocialGroups.vue'), meta: { requiresAuth: true, title: '社交 · 群组' } },
        { path: 'relationships', name: 'social-relationships', component: () => import('@/pages/social/SocialRelationships.vue'), meta: { requiresAuth: true, title: '社交 · 关系' } },
        { path: 'commitments', name: 'social-commitments', component: () => import('@/pages/social/SocialCommitments.vue'), meta: { requiresAuth: true, title: '社交 · 承诺' } },
        { path: 'commitments/:commitmentId', name: 'social-commitment', component: () => import('@/pages/social/SocialCommitmentDetail.vue'), meta: { requiresAuth: true, title: '社交 · 承诺详情' } },
        { path: 'sessions', name: 'social-sessions', component: () => import('@/pages/social/SocialSessions.vue'), meta: { requiresAuth: true, title: '社交 · 会话' } },
      ],
    },
    {
      path: '/memory',
      component: () => import('@/pages/memory/MemoryLayout.vue'),
      meta: { requiresAuth: true, section: 'memory' },
      children: [
        { path: '', name: 'memory', component: () => import('@/pages/memory/MemoryBrowse.vue'), meta: { requiresAuth: true, title: '记忆' } },
        { path: 'timeline', name: 'memory-timeline', component: () => import('@/pages/memory/MemoryTimeline.vue'), meta: { requiresAuth: true, title: '记忆 · 时间线' } },
        { path: 'health', name: 'memory-health', component: () => import('@/pages/memory/MemoryHealth.vue'), meta: { requiresAuth: true, title: '记忆 · 健康度' } },
        { path: 'ops', name: 'memory-ops', component: () => import('@/pages/memory/MemoryOps.vue'), meta: { requiresAuth: true, title: '记忆 · 运维' } },
        { path: ':memoryId(\\d+)', name: 'memory-detail', component: () => import('@/pages/memory/MemoryDetail.vue'), meta: { requiresAuth: true, title: '记忆 · 详情' } },
      ],
    },
    {
      path: '/abilities',
      component: () => import('@/pages/abilities/AbilitiesLayout.vue'),
      meta: { requiresAuth: true, section: 'abilities' },
      children: [
        { path: '', redirect: { name: 'abilities-tools' } },
        { path: 'tools', name: 'abilities-tools', component: () => import('@/pages/abilities/Tools.vue'), meta: { requiresAuth: true, title: '能力 · 工具' } },
        { path: 'tools/:name', name: 'abilities-tool', component: () => import('@/pages/abilities/ToolDetail.vue'), meta: { requiresAuth: true, title: '能力 · 工具详情' } },
        { path: 'media', name: 'abilities-media', component: () => import('@/pages/abilities/Media.vue'), meta: { requiresAuth: true, title: '能力 · 媒体' } },
        { path: 'agent', name: 'abilities-agent', component: () => import('@/pages/abilities/Agent.vue'), meta: { requiresAuth: true, title: '能力 · Agent' } },
        { path: 'agent/tasks/:taskId', name: 'abilities-agent-task', component: () => import('@/pages/abilities/AgentTaskDetail.vue'), meta: { requiresAuth: true, title: '能力 · Agent 任务' } },
      ],
    },
    {
      path: '/minecraft',
      name: 'minecraft',
      component: () => import('@/pages/Minecraft.vue'),
      meta: { requiresAuth: true, title: 'Minecraft' },
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
