'use strict'
/** R1-N8：受控 thinkTimeout 覆盖的生产隔离 + 5 次受控 timeout 分支（E2，本地/CI 可选）。 */
const assert = require('assert')
const path = require('path')
process.env.MC_TEST_MODE = process.env.MC_TEST_MODE || ''
const runtime = require('../runtime.js')

function withEnv(vars, fn) {
  const saved = {}
  for (const key of Object.keys(vars)) {
    saved[key] = process.env[key]
    if (vars[key] === undefined) delete process.env[key]
    else process.env[key] = vars[key]
  }
  try { return fn() } finally {
    for (const key of Object.keys(saved)) {
      if (saved[key] === undefined) delete process.env[key]
      else process.env[key] = saved[key]
    }
  }
}

let checks = 0
function check(cond, label) {
  checks += 1
  if (!cond) { console.error('  ✗ ' + label); process.exitCode = 1 } else console.log('  ✓ ' + label)
}

console.log('[r1-n8] 生产隔离（默认不得改变）')
check(runtime.testThinkTimeoutOverride() === null, '没有 MC_TEST_MODE 时不生效')
withEnv({ MC_TEST_MODE: '1', MC_TEST_THINK_TIMEOUT_MS: undefined }, () => {
  check(runtime.testThinkTimeoutOverride() === null, '缺值时不生效')
})
for (const bad of ['0', '-5', 'abc', '999999999', '1.5']) {
  withEnv({ MC_TEST_MODE: '1', MC_TEST_THINK_TIMEOUT_MS: bad }, () => {
    check(runtime.testThinkTimeoutOverride() === null, '非法值 ' + bad + ' 被忽略')
  })
}
withEnv({ MC_TEST_MODE: '0', MC_TEST_THINK_TIMEOUT_MS: '5' }, () => {
  check(runtime.testThinkTimeoutOverride() === null, 'MC_TEST_MODE=0 时不生效')
})
withEnv({ MC_TEST_MODE: '1', MC_TEST_THINK_TIMEOUT_MS: '5' }, () => {
  check(runtime.testThinkTimeoutOverride() === 5, '合法测试值生效')
})
console.log('[r1-n8] ' + checks + ' checks')
