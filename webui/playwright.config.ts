import { existsSync } from 'node:fs'

import { defineConfig } from '@playwright/test'

/**
 * W6 browser E2E: a real Chromium session against a real CatooBot.
 *
 * `webServer` boots tests/webui_e2e_server.py (temp DB/logs/overrides, no
 * OneBot port, no AI keys) and waits until /login answers. The suite is
 * deliberately serial: one worker, no parallel tests, so the shared server
 * state stays deterministic.
 */
// The E2E server needs the *project* dependencies: locally and in CI they live
// in the uv venv, never in whatever `python` happens to be first on PATH.
// cmd.exe additionally needs the path quoted (it treats `/` in an unquoted
// relative path as a switch separator); POSIX shells are happy either way.
const venvPython = process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python'
const fallback = process.env.CATOOBOT_E2E_PYTHON ?? 'python'
const python = existsSync(venvPython) ? `"${venvPython}"` : fallback

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:8611',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `${python} ../tests/webui_e2e_server.py`,
    url: 'http://127.0.0.1:8611/login',
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'pipe',
  },
})
