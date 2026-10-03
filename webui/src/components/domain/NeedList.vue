<script setup lang="ts">
/**
 * 需求列表（W5 §14）：label / band / level 全部来自后端快照；
 * 只显示当前读数与 critical/pressing 标记，不显示 growth，也不做趋势推断。
 */
import { computed } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import type { NeedFullRow } from '@/types/domain'

const props = defineProps<{ needs: NeedFullRow[] }>()

/** strong / intense 统一显示「强烈」，其它 band 原样显示（后端枚举即文案）。 */
const BAND_LABELS: Record<string, string> = {
  strong: '强烈',
  intense: '强烈',
}

function bandText(band: string): string {
  if (!band) return '—'
  return BAND_LABELS[band] ?? band
}

function percentOf(level: number): number | null {
  if (!Number.isFinite(level)) return null
  return Math.round(Math.min(1, Math.max(0, level)) * 100)
}

function percentText(need: NeedFullRow): string {
  const percent = percentOf(need.level)
  return percent === null ? '—' : `${percent}%`
}

function barWidth(need: NeedFullRow): string {
  return `${percentOf(need.level) ?? 0}%`
}

function labelText(need: NeedFullRow): string {
  return need.label || need.key || '—'
}

const rows = computed(() => props.needs)
</script>

<template>
  <section class="cb-card cb-need-list" data-test="need-list">
    <SectionHeader title="需求" description="世界运行期的真实读数；只看当前状态，不做趋势推断" />

    <EmptyState
      v-if="rows.length === 0"
      title="暂无需求数据"
      description="沙盒未运行或尚未产生需求读数时，这里为空。"
    />

    <ul v-else class="cb-need-list__items">
      <li
        v-for="need in rows"
        :key="need.key"
        class="cb-need-list__item"
        data-test="need-row"
      >
        <div class="cb-need-list__head">
          <span class="cb-need-list__label" data-test="need-label">{{ labelText(need) }}</span>
          <span class="cb-need-list__band" data-test="need-band">{{ bandText(need.band) }}</span>
          <span v-if="need.critical" class="cb-need-list__tag" data-test="need-critical">临界</span>
          <span v-if="need.pressing" class="cb-need-list__tag" data-test="need-pressing">紧迫</span>
        </div>
        <div class="cb-need-list__meter">
          <div
            class="cb-need-list__bar"
            role="progressbar"
            :aria-valuenow="percentOf(need.level) ?? 0"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="`${labelText(need)}需求水平`"
          >
            <span
              class="cb-need-list__fill"
              :class="{ 'cb-need-list__fill--critical': need.critical }"
              :style="{ width: barWidth(need) }"
              data-test="need-bar"
            />
          </div>
          <span class="cb-need-list__percent" data-test="need-percent">{{ percentText(need) }}</span>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.cb-need-list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-need-list__items {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-need-list__item {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
}

.cb-need-list__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
}

.cb-need-list__label {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
}

.cb-need-list__band {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-need-list__tag {
  padding: 0 var(--cb-space-2);
  border: 1px solid var(--cb-warning);
  border-radius: 999px;
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.cb-need-list__meter {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.cb-need-list__bar {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: var(--cb-border);
  overflow: hidden;
}

.cb-need-list__fill {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--cb-primary);
}

.cb-need-list__fill--critical {
  background: var(--cb-danger);
}

.cb-need-list__percent {
  flex: none;
  min-width: 4ch;
  text-align: right;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-variant-numeric: tabular-nums;
}
</style>
