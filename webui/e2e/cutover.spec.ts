/**
 * W6 cutover browser E2E（§27/§28/§29/§31/§119/§120）。
 *
 * A real Chromium session against the real CatooBot booted by
 * `scripts/webui_e2e_server.py`（temp DB/logs/overrides、无 OneBot 端口、无 AI key）：
 * 登录 → 总览 → AI/系统/角色/世界/社交/记忆/能力/日志/运行时/凭据巡游 →
 * 设置热更新 → 危险操作门禁 → 登出与守卫。每个页面的 h1/顶栏标题与至少一个
 * 真实值（来自后端/种子数据）都被断言；任何 console.error、页面异常或
 * chunk/asset 加载失败都会让测试失败。
 */
import { expect, test as base } from '@playwright/test'
import type { Page, Request } from '@playwright/test'

const PASSWORD = process.env.CATOOBOT_E2E_PASSWORD ?? 'e2e-test-password'

interface Diagnostics {
  consoleErrors: string[]
  pageErrors: string[]
  failedAssets: string[]
}

/**
 * 收集浏览器侧噪声并在每个测试结束时断言为空。
 * 只有 chunk/asset（/assets/*、js/css/字体/图片）加载失败会被判失败；
 * 页面主动取消的 API 请求不算。
 *
 * 唯一的显式豁免（documented exception）：登录页必须在不带 Cookie 时
 * `GET /api/v1/session` 探测会话（auth store `skipAuthHandler`），后端按设计
 * 返回 401 —— Chromium 会把它记成 console error，应用无法避免。豁免严格限定为
 * 「来源 URL 是 /api/v1/session 且文本含 401」，其它任何 console.error 一律失败。
 */
const AUTH_PROBE_URL = /\/api\/v1\/session$/
const AUTH_PROBE_401 = /401 \(Unauthorized\)/

const test = base.extend<{ diagnostics: Diagnostics }>({
  diagnostics: [
    async ({ page }, use) => {
      const diagnostics: Diagnostics = { consoleErrors: [], pageErrors: [], failedAssets: [] }
      let sessionProbe401 = 0
      const documentedProbeErrors: string[] = []
      page.on('response', (response) => {
        if (response.status() === 401 && AUTH_PROBE_URL.test(response.url())) sessionProbe401 += 1
      })
      page.on('console', (message) => {
        if (message.type() !== 'error') return
        if (AUTH_PROBE_URL.test(message.location().url) && AUTH_PROBE_401.test(message.text())) {
          documentedProbeErrors.push(message.text())
          return
        }
        diagnostics.consoleErrors.push(message.text())
      })
      page.on('pageerror', (error) => diagnostics.pageErrors.push(error.message))
      page.on('requestfailed', (request: Request) => {
        const url = request.url()
        const isAsset =
          url.includes('/assets/') ||
          /\.(?:js|css|woff2?|ttf|otf|png|jpe?g|svg|ico|webp|map)(?:\?|$)/i.test(url)
        if (isAsset) {
          diagnostics.failedAssets.push(
            `${request.method()} ${url} — ${request.failure()?.errorText ?? 'unknown'}`,
          )
        }
      })
      await use(diagnostics)
      expect
        .soft(diagnostics.pageErrors, `页面未捕获异常：\n${diagnostics.pageErrors.join('\n')}`)
        .toEqual([])
      expect
        .soft(diagnostics.consoleErrors, `console.error：\n${diagnostics.consoleErrors.join('\n')}`)
        .toEqual([])
      expect
        .soft(diagnostics.failedAssets, `资源加载失败：\n${diagnostics.failedAssets.join('\n')}`)
        .toEqual([])
      expect
        .soft(
          documentedProbeErrors.length,
          `匿名会话探测记录数（${documentedProbeErrors.length}）不得超过实际 401 响应数（${sessionProbe401}）`,
        )
        .toBeLessThanOrEqual(sessionProbe401)
    },
    { auto: true },
  ],
})

async function loginAsAdmin(page: Page): Promise<void> {
  await page.goto('/login')
  await expect(page.getByTestId('login-view')).toBeVisible()
  await page.locator('input[name="username"]').fill('admin')
  await page.locator('input[name="password"]').fill(PASSWORD)
  await page.getByTestId('login-submit').click()
  await expect(page.getByTestId('dashboard')).toBeVisible()
}

interface PageCheck {
  path: string
  /** 顶栏标题 = 路由 meta.title，逐页唯一 */
  topbar: string
  /** 分区布局的 h1 */
  h1: string
  /** 页面真实内容容器（数据到达后可见，不是永远骨架） */
  marker: string
}

/** 打开页面 → 顶栏标题 → h1 → 真实内容容器可见。 */
async function expectPage(page: Page, check: PageCheck): Promise<void> {
  await page.goto(check.path)
  await expect(page.locator('.cb-topbar__title')).toHaveText(check.topbar)
  await expect(page.locator('h1').filter({ hasText: check.h1 }).first()).toBeVisible()
  await expect(page.locator(check.marker).first()).toBeVisible({ timeout: 15_000 })
}

test('登录 → 总览：真实运行数据与快捷入口', async ({ page }) => {
  await loginAsAdmin(page)

  await expect(page.locator('h1')).toHaveText('总览')
  const metrics = page.getByTestId('dashboard-metrics')
  await expect(metrics).toBeVisible()
  for (const label of ['QQ', 'AI', '世界', '运行时', '社交']) {
    await expect(metrics.locator('.cb-metric').filter({ hasText: label }).first()).toBeVisible()
  }

  // 真实值：AI 关（配置）、沙箱 phase=running（后端世界）、运行时 uptime 已产生
  await expect(metrics).toContainText('已停用')
  await expect(metrics).toContainText('running')
  const uptimeCard = metrics.locator('.cb-metric').filter({ hasText: '运行时' })
  await expect(uptimeCard.locator('.cb-metric__value')).not.toHaveText('—')

  const quick = page.getByTestId('dashboard-quick-actions')
  for (const label of ['配置 AI', '查看世界', '查看日志', '查看社交', '查看记忆']) {
    await expect(quick.getByRole('link', { name: label })).toBeVisible()
  }

  await expect(page.getByTestId('dashboard-world')).toContainText('客厅')
  await expect(page.getByTestId('dashboard-events')).toBeVisible()
})

test('AI 分区：页面与真实数据（provider/model/roles/usage/setup）', async ({ page }) => {
  await loginAsAdmin(page)

  await expectPage(page, {
    path: '/ai',
    topbar: 'AI 与模型',
    h1: 'AI 与模型',
    marker: '[data-test="ai-overview"]',
  })
  // 健康徽标有真实文案（AI 未启用 → 未配置）
  await expect(page.locator('[data-test="ai-status-badge"]').first()).not.toBeEmpty()

  await expectPage(page, {
    path: '/ai/providers',
    topbar: 'AI · 服务商',
    h1: 'AI 与模型',
    marker: '[data-test="provider-e2eoffline"]',
  })
  // 真实 provider 卡片 + 缺少 key 的真实状态 + 已注册模型数量
  const providerCard = page.locator('[data-test="provider-e2eoffline"]')
  await expect(providerCard).toContainText('e2eoffline')
  await expect(providerCard).toContainText('缺少 API Key')
  await expect(providerCard).toContainText('模型数量')
  await expect(providerCard).toContainText('1 个')

  await expectPage(page, {
    path: '/ai/models',
    topbar: 'AI · 模型',
    h1: 'AI 与模型',
    marker: '[data-test="model-row-e2e-model"]',
  })
  await expect(page.locator('[data-test="model-row-e2e-model"]')).toContainText('e2eoffline')

  await expectPage(page, {
    path: '/ai/roles',
    topbar: 'AI · 模型用途',
    h1: 'AI 与模型',
    marker: '[data-test="roles-grid"]',
  })
  // chat 角色绑定的是配置里的 e2e-model
  await expect(page.locator('[data-test="role-card"][data-role="chat"]')).toContainText('e2e-model')

  await expectPage(page, {
    path: '/ai/failover',
    topbar: 'AI · 故障转移',
    h1: 'AI 与模型',
    marker: '[data-test="failover-list"]',
  })
  // 链首 = e2e-model
  await expect(page.locator('[data-test="fb-name"]')).toHaveText('e2e-model')
  await expect(page.locator('[data-test="fb-chat"]')).toBeVisible()

  await expectPage(page, {
    path: '/ai/usage',
    topbar: 'AI · 用量',
    h1: 'AI 与模型',
    marker: '[data-test="usage-table"]',
  })
  // 种子里的 2 次调用（1 失败，160 tokens）真实聚合
  const usageRow = page.locator('[data-test="usage-row"]').first()
  await expect(usageRow).toContainText('e2e-model')
  await expect(usageRow).toContainText('160')

  await expectPage(page, {
    path: '/ai/setup',
    topbar: 'AI · 配置向导',
    h1: 'AI 与模型',
    marker: '[data-test="setup-step-provider"]',
  })
  // 五步向导全部渲染
  for (const step of ['provider', 'key', 'model', 'chat', 'test']) {
    await expect(page.locator(`[data-test="setup-step-${step}"]`)).toBeVisible()
  }
})

test('系统设置：级别切换（基础 → 高级 → 专家）与草稿 UI', async ({ page }) => {
  await loginAsAdmin(page)
  await expectPage(page, {
    path: '/system/settings',
    topbar: '系统 · 设置',
    h1: '系统',
    marker: '[data-test="settings-page"]',
  })

  const basicFields = await page.locator('[data-test="settings-field"]').count()
  expect(basicFields).toBeGreaterThan(0)

  await expect(page.locator('[data-test="level-basic"]')).toHaveAttribute('aria-pressed', 'true')
  await page.locator('[data-test="level-advanced"]').click()
  await expect(page.locator('[data-test="level-advanced"]')).toHaveAttribute('aria-pressed', 'true')
  await page.locator('[data-test="level-expert"]').click()
  await expect(page.locator('[data-test="level-expert"]')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[data-test="expert-warning"]')).toBeVisible()
  const expertFields = await page.locator('[data-test="settings-field"]').count()
  expect(expertFields).toBeGreaterThan(basicFields)

  // 回到基础级别，设置项仍在
  await page.locator('[data-test="level-basic"]').click()
  await expect(page.locator('[data-test="level-basic"]')).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('[data-test="settings-field"]').first()).toBeVisible()
})

test('角色/世界/社交/记忆/能力/系统：逐页真实数据', async ({ page }) => {
  await loginAsAdmin(page)

  // 角色：人物档案从 bible 同步 → 真实名字与状态
  await expectPage(page, {
    path: '/character',
    topbar: '角色',
    h1: '角色',
    marker: '[data-test="character-page"]',
  })
  await expect(page.locator('[data-test="character-name"]')).toHaveText('罐头（人物档案）')
  await expect(page.locator('[data-test="character-state"]')).toBeVisible()

  // 世界：种子空间/地点 + 运行阶段
  await expectPage(page, {
    path: '/character/world',
    topbar: '角色 · 世界',
    h1: '角色',
    marker: '[data-test="world-state"]',
  })
  await expect(page.locator('[data-test="world-location"]')).toContainText('客厅')
  await expect(page.locator('[data-test="world-phase"]')).toContainText('running')

  // 世界时间线：沙箱初始化事件
  await expectPage(page, {
    path: '/character/world/timeline',
    topbar: '角色 · 世界时间线',
    h1: '角色',
    marker: '[data-test="timeline-item"]',
  })
  await expect(page.locator('[data-test="timeline-event"]').first()).not.toBeEmpty()

  // 社交：种子用户
  await expectPage(page, { path: '/social', topbar: '社交 · 用户', h1: '社交', marker: '[data-test="person-card"]' })
  await expect(page.locator('[data-test="person-name"]').first()).toContainText('澈澈')

  // 社交：种子群
  await expectPage(page, { path: '/social/groups', topbar: '社交 · 群组', h1: '社交', marker: '[data-test="group-row"]' })
  await expect(page.locator('[data-test="group-row"]')).toContainText('猫图群')

  // 社交：种子承诺（sandbox_commitments）
  await expectPage(page, {
    path: '/social/commitments',
    topbar: '社交 · 承诺',
    h1: '社交',
    marker: '[data-test="commitment-card"]',
  })
  await expect(page.locator('[data-test="commitment-summary"]')).toContainText('E2E 约定')

  // 社交会话：真实空态（无进行中的会话）
  await expectPage(page, {
    path: '/social/sessions',
    topbar: '社交 · 会话',
    h1: '社交',
    marker: '[data-test="sessions-statement"]',
  })
  await expect(page.locator('[data-test="session-card"]')).toHaveCount(0)

  // 记忆：3 条种子记忆
  await expectPage(page, { path: '/memory', topbar: '记忆', h1: '记忆', marker: '[data-test="memory-list"]' })
  await expect(page.locator('[data-test="memory-card"]')).toHaveCount(3)
  await expect(page.locator('[data-test="memory-list"]')).toContainText('阿澈最喜欢猫娘表情包')

  // 记忆时间线（v1 SPA 路由，不能被旧 SSR 抢走）
  await expectPage(page, {
    path: '/memory/timeline',
    topbar: '记忆 · 时间线',
    h1: '记忆',
    marker: '[data-test="timeline-item"]',
  })
  await expect(page.locator('[data-test="memory-timeline"] [data-test="timeline-item"]')).toHaveCount(3)

  // 记忆健康度：总数 = 种子 3 条
  await expectPage(page, {
    path: '/memory/health',
    topbar: '记忆 · 健康度',
    h1: '记忆',
    marker: '[data-test="health-grid"]',
  })
  await expect(page.locator('[data-test="health-total"] .cb-metric__value')).toHaveText('3')

  // 工具：注册表真实渲染
  await expectPage(page, {
    path: '/abilities/tools',
    topbar: '能力 · 工具',
    h1: '媒体与能力',
    marker: '[data-test="tool-card-calculator"]',
  })
  await expect(page.locator('[data-test="tool-card-calculator"]')).toContainText('Calculator')

  // 媒体：表情库区块与统计（真实空库）
  await expectPage(page, {
    path: '/abilities/media',
    topbar: '能力 · 媒体',
    h1: '媒体与能力',
    marker: '[data-test="media-stickers"]',
  })
  await expect(page.locator('[data-test="sticker-query"]')).toBeVisible()

  // Agent：就绪状态的真实原因文案
  await expectPage(page, {
    path: '/abilities/agent',
    topbar: '能力 · Agent',
    h1: '媒体与能力',
    marker: '[data-test="agent-status"]',
  })
  await expect(page.locator('[data-test="agent-reason"]')).not.toBeEmpty()

  // 日志：进程真实日志行（含 CatooBot 启动行）
  await expectPage(page, { path: '/system/logs', topbar: '系统 · 日志', h1: '系统', marker: '[data-test="log-row"]' })
  await expect(page.locator('[data-test="log-row"]').filter({ hasText: 'CatooBot' }).first()).toBeVisible()

  // 运行时：进程版本与 Python
  await expectPage(page, {
    path: '/system/runtime',
    topbar: '系统 · Runtime',
    h1: '系统',
    marker: '[data-test="runtime-process"]',
  })
  await expect(page.locator('[data-test="runtime-process"]')).toContainText('v2.0')

  // 凭据：web/admin 与 onebot 两类真实条目
  await expectPage(page, {
    path: '/system/credentials',
    topbar: '系统 · 凭据',
    h1: '系统',
    marker: '[data-test="credential-row"]',
  })
  await expect(page.locator('[data-test="credential-row"]').filter({ hasText: 'admin' })).toBeVisible()
  await expect(
    page.locator('[data-test="credential-row"]').filter({ hasText: 'CATOOBOT_ONEBOT_ACCESS_TOKEN' }),
  ).toBeVisible()
})

test('设置热更新：修改 → 应用（toast+徽标）→ 改回', async ({ page }) => {
  await loginAsAdmin(page)
  await page.goto('/system/settings')

  const field = page.locator(
    '[data-test="settings-field"][data-key="logging.narrate_world_ticks"]',
  )
  await field.scrollIntoViewIfNeeded()
  const badge = field.locator('[data-test="config-status-badge"]')
  const toggle = field.locator('[data-test="field-bool-switch"]')
  await expect(badge).toHaveAttribute('data-status', 'effective')
  const before = (await toggle.getAttribute('aria-checked')) === 'true'

  // --- 第一次修改：真实 PATCH，确认后才提交
  await toggle.click()
  await expect(page.locator('[data-test="dirty-bar"]')).toBeVisible()
  await expect(page.locator('[data-test="config-status-badge"][data-status="unsaved"]')).toBeVisible()
  await page.locator('[data-test="dirty-apply"]').click()
  await expect(page.locator('.cb-dialog')).toContainText('确认应用修改')
  await page.locator('[data-test="confirm"]').click()
  await expect(page.locator('.toast--success').last()).toContainText('已保存')
  await expect(badge).toHaveAttribute('data-status', 'effective')
  await expect(toggle).toHaveAttribute('aria-checked', before ? 'false' : 'true')
  await expect(page.locator('[data-test="dirty-bar"]')).toHaveCount(0)

  // --- 改回：同一路径再来一次，把 overrides 恢复到原值
  await toggle.click()
  await expect(page.locator('[data-test="dirty-bar"]')).toBeVisible()
  await page.locator('[data-test="dirty-apply"]').click()
  await page.locator('[data-test="confirm"]').click()
  await expect(page.locator('.toast--success').last()).toContainText('已保存')
  await expect(toggle).toHaveAttribute('aria-checked', before ? 'true' : 'false')
  await expect(badge).toHaveAttribute('data-status', 'effective')
})

test('危险操作门禁：工具测试与 Provider 删除都必须先确认（取消不发请求）', async ({ page }) => {
  await loginAsAdmin(page)

  const toolTestRequests: string[] = []
  const providerDeleteRequests: string[] = []
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/api\/v1\/tools\/[^/]+\/test$/.test(request.url())) {
      toolTestRequests.push(request.url())
    }
    if (request.method() === 'DELETE' && /\/api\/v1\/ai\/providers\//.test(request.url())) {
      providerDeleteRequests.push(request.url())
    }
  })

  // 工具列表页：注册表真实渲染
  await expectPage(page, {
    path: '/abilities/tools',
    topbar: '能力 · 工具',
    h1: '媒体与能力',
    marker: '[data-test="abilities-tools"]',
  })
  await expect(page.locator('[data-test="tool-card-calculator"]')).toBeVisible()

  // 工具详情：测试必须先过确认框；取消后一个请求都不许发
  await page.goto('/abilities/tools/calculator')
  await expect(page.locator('[data-test="tool-detail"]')).toBeVisible()
  await page.locator('[data-test="tool-test"]').click()
  await expect(page.locator('.cb-dialog')).toBeVisible()
  await expect(page.locator('.cb-dialog')).toContainText('测试工具')
  await page.locator('[data-test="cancel"]').click()
  await expect(page.locator('.cb-dialog')).toHaveCount(0)
  await page.waitForTimeout(500)
  expect(toolTestRequests).toEqual([])

  // Provider 删除：确认框取消 → 没有 DELETE，provider 仍在
  await page.goto('/ai/providers')
  const providerCard = page.locator('[data-test="provider-e2eoffline"]')
  await expect(providerCard).toBeVisible()
  await providerCard.locator('[data-test="delete-provider"]').click()
  await expect(page.locator('.cb-dialog')).toBeVisible()
  await expect(page.locator('.cb-dialog')).toContainText('删除')
  await page.locator('[data-test="cancel"]').click()
  await expect(page.locator('.cb-dialog')).toHaveCount(0)
  await page.waitForTimeout(500)
  expect(providerDeleteRequests).toEqual([])
  await page.reload()
  await expect(page.locator('[data-test="provider-e2eoffline"]')).toBeVisible()
})

test('登出：回到登录页；未登录访问 /memory 仍被守卫拦截', async ({ page }) => {
  await loginAsAdmin(page)

  await page.locator('[data-test="user-menu"] summary').click()
  await page.locator('[data-test="logout"]').click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByTestId('login-view')).toBeVisible()

  await page.goto('/memory')
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByTestId('login-view')).toBeVisible()
})
