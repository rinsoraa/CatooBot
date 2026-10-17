'use strict'
/**
 * R1-G6：find_blocks 的 coverage / captured_at / dimension 合同（只读观察元数据）。
 *
 * 覆盖：全部 chunk 已加载 → COMPLETE；部分加载 → PARTIAL；无加载 → UNKNOWN；
 * 没有 chunk 列 API / 没有 dimension → UNKNOWN（绝不默认 COMPLETE）；空命中 + COMPLETE
 * 才是「该范围确实没有」。
 *
 * 运行：node minecraft_runtime/test/find_blocks_coverage.test.js
 */

const { Vec3 } = require('vec3')
const registry = require('prismarine-registry')('1.21.1')
const { findBlocksCoverage } = require('../runtime.js')

let failures = 0
let checks = 0
function assert(condition, label) {
  checks += 1
  if (condition) console.log('  ✓ ' + label)
  else {
    failures += 1
    console.error('  ✗ ' + label)
  }
}

function makeBot({ dimension = 'minecraft:overworld', loaded = () => true, throws = false } = {}) {
  return {
    game: dimension === null ? {} : { dimension },
    world: {
      getColumn(cx, cz) {
        if (throws) throw new Error('boom')
        return loaded(cx, cz) ? { cx, cz } : null
      },
    },
  }
}

const point = new Vec3(0, 64, 0)

console.log('[find-blocks-coverage] R1-G6：覆盖完整性语义')
{
  const view = findBlocksCoverage(makeBot({ loaded: () => true }), point, 8)
  assert(view.coverage === 'COMPLETE', '全部 chunk 已加载 → COMPLETE')
  assert(view.coverage_reason === 'all_chunks_loaded', 'reason=all_chunks_loaded')
  assert(typeof view.captured_at === 'number' && view.captured_at > 0, 'captured_at 来自真实搜索帧')
  assert(view.dimension === 'minecraft:overworld', 'dimension 来自真实 game 状态')
  assert(view.center.x === 0 && view.center.y === 64 && view.center.z === 0, 'center 为真实方块格中心')
  assert(view.requested_radius === 8, 'requested_radius 如实回显请求半径')
  assert(view.chunks_total === 4 && view.chunks_loaded === 4, '半径 8 → 2×2 chunk 全部已加载')
}

{
  const view = findBlocksCoverage(makeBot({ loaded: (cx) => cx === 0 }), point, 8)
  assert(view.coverage === 'PARTIAL', '只加载一部分 → PARTIAL（不能宣称完整）')
  assert(view.coverage_reason === 'some_chunks_unloaded', 'reason=some_chunks_unloaded')
  assert(view.chunks_loaded < view.chunks_total, '已加载数 < 总数')
}

{
  const view = findBlocksCoverage(makeBot({ loaded: () => false }), point, 8)
  assert(view.coverage === 'UNKNOWN', '一个都没加载 → UNKNOWN')
  assert(view.coverage_reason === 'no_chunks_loaded', 'reason=no_chunks_loaded')
}

{
  const view = findBlocksCoverage(makeBot({ throws: true }), point, 8)
  assert(view.coverage === 'UNKNOWN', 'chunk 探测抛错 → UNKNOWN（绝不当 COMPLETE）')
}

{
  const noApi = { game: { dimension: 'minecraft:overworld' } }
  const view = findBlocksCoverage(noApi, point, 8)
  assert(view.coverage === 'UNKNOWN', '没有 chunk 列 API → UNKNOWN')
  assert(view.coverage_reason === 'chunk_api_unavailable', 'reason=chunk_api_unavailable')
}

{
  const noDim = findBlocksCoverage(makeBot({ dimension: null, loaded: () => true }), point, 8)
  assert(noDim.coverage === 'UNKNOWN', '没有 dimension → UNKNOWN（不猜 overworld）')
  assert(noDim.coverage_reason === 'dimension_unavailable', 'reason=dimension_unavailable')
}

{
  // 负坐标：chunk 边界必须用 floor（-1 属于 chunk -1，不是 0）
  const seen = []
  const view = findBlocksCoverage(
    makeBot({
      loaded: (cx, cz) => {
        seen.push([cx, cz])
        return true
      },
    }),
    new Vec3(-1, 64, -1),
    1,
  )
  assert(view.coverage === 'COMPLETE', '负坐标同样 COMPLETE')
  assert(
    seen.some(([cx]) => cx === -1),
    '负坐标按 floor 落到 chunk -1（向零截断会得到 0，' + JSON.stringify(seen) + '）',
  )
}

if (failures > 0) {
  console.error('[find-blocks-coverage] ' + failures + ' 项失败 / ' + checks + ' 项检查')
  process.exit(1)
}
console.log('[find-blocks-coverage] ' + checks + '/' + checks + ' checks passed')
console.log('[find-blocks-coverage] ALL CHECKS PASSED')
