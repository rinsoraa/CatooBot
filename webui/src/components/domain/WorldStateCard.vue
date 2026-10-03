<script setup lang="ts">
/**
 * 世界状态卡（W5 §6-§10、§145）：只展示运行期事实，缺失字段显示「—」；
 * 当前没有动作 = 「空闲」；revision 属于诊断信息，收进 Expert 折叠区。
 * WebSocket 断开时不清空数据，只提示「数据可能不是最新」。
 */
import { computed } from 'vue'

import SectionHeader from '@/components/SectionHeader.vue'
import type { WorldData } from '@/types/domain'

const props = withDefaults(defineProps<{ world: WorldData | null; stale?: boolean }>(), {
  stale: false,
})

/** 安全文本：对象取常见标签字段，绝不渲染成 [object Object]。 */
function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    const parts = value.map((item) => text(item, '')).filter((part) => part !== '')
    return parts.length > 0 ? parts.join('、') : fallback
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    for (const key of ['label', 'name', 'key', 'id']) {
      const candidate = record[key]
      if (candidate !== undefined && candidate !== null && candidate !== '') {
        return text(candidate, fallback)
      }
    }
  }
  return fallback
}

const hasWorld = computed(() => props.world !== null)
const actionText = computed(() => (hasWorld.value ? text(props.world?.action?.name, '空闲') : '—'))
const locationText = computed(() => (hasWorld.value ? text(props.world?.location) : '—'))
const phaseText = computed(() => (hasWorld.value ? text(props.world?.phase) : '—'))
const modes = computed(() => props.world?.modes ?? [])
const pressing = computed(() => props.world?.needs?.pressing ?? [])
const worldRevision = computed(() => text(props.world?.world_revision))
const cognitiveRevision = computed(() => text(props.world?.cognitive_revision))
</script>

<template>
  <section class="cb-card cb-world-state" data-test="world-state">
    <SectionHeader title="当前世界" description="当前活动、地点与运行阶段" />

    <p v-if="stale" class="cb-world-state__stale" data-test="world-stale" role="status">
      数据可能不是最新（实时连接已断开）
    </p>

    <dl class="cb-world-state__facts">
      <div class="cb-world-state__fact">
        <dt class="cb-caption">当前活动</dt>
        <dd data-test="world-action">{{ actionText }}</dd>
      </div>
      <div class="cb-world-state__fact">
        <dt class="cb-caption">地点</dt>
        <dd data-test="world-location">{{ locationText }}</dd>
      </div>
      <div class="cb-world-state__fact">
        <dt class="cb-caption">阶段</dt>
        <dd data-test="world-phase">{{ phaseText }}</dd>
      </div>
      <div class="cb-world-state__fact cb-world-state__fact--wide">
        <dt class="cb-caption">模式</dt>
        <dd class="cb-world-state__chips">
          <template v-if="modes.length > 0">
            <span
              v-for="(mode, index) in modes"
              :key="`${text(mode)}-${index}`"
              class="cb-world-state__chip"
              data-test="world-mode"
            >
              {{ text(mode) }}
            </span>
          </template>
          <span v-else data-test="world-modes-empty">—</span>
        </dd>
      </div>
      <div class="cb-world-state__fact cb-world-state__fact--wide">
        <dt class="cb-caption">正在变得强烈的需求</dt>
        <dd class="cb-world-state__chips">
          <template v-if="pressing.length > 0">
            <span
              v-for="(need, index) in pressing"
              :key="`${text(need)}-${index}`"
              class="cb-world-state__chip cb-world-state__chip--warn"
              data-test="world-pressing"
            >
              {{ text(need) }}
            </span>
          </template>
          <span v-else data-test="world-pressing-empty">—</span>
        </dd>
      </div>
    </dl>

    <details class="cb-world-state__expert">
      <summary class="cb-caption">Expert 修订号</summary>
      <p class="cb-world-state__expert-text" data-test="world-revision">
        world_revision: {{ worldRevision }} · cognitive_revision: {{ cognitiveRevision }}
      </p>
    </details>
  </section>
</template>

<style scoped>
.cb-world-state {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-world-state__stale {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-sm);
}

.cb-world-state__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3) var(--cb-space-4);
  margin: 0;
}

.cb-world-state__fact--wide {
  grid-column: 1 / -1;
}

.cb-world-state__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-world-state__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
}

.cb-world-state__chip {
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-world-state__chip--warn {
  border-color: var(--cb-warning);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
}

.cb-world-state__expert summary {
  cursor: pointer;
}

.cb-world-state__expert-text {
  margin-top: var(--cb-space-2);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-family: var(--cb-font-mono);
  overflow-wrap: anywhere;
}
</style>
