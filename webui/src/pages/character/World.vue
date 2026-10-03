<script setup lang="ts">
/**
 * 世界页（W5 §10-§16、§62、§67-§75、§145）。
 *
 * 快照来自 world store（首屏 REST + realtime 变更驱动重新拉取，绝不轮询）；
 * 默认不展示大块 JSON：原始结构只在 Expert 折叠区里，且必须由 API 真实返回。
 * 管理员操作会影响运行中的世界：每个动作都先 ConfirmDialog 二次确认，
 * reset / reinitialize 明确标注不可撤销；失败时展示后端 message。
 */
import { computed, onMounted, ref } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ActionCard from '@/components/domain/ActionCard.vue'
import GoalCard from '@/components/domain/GoalCard.vue'
import NeedList from '@/components/domain/NeedList.vue'
import WorldStateCard from '@/components/domain/WorldStateCard.vue'
import { toast } from '@/composables/toast'
import { useWorldStore } from '@/stores/world'

const store = useWorldStore()

onMounted(() => {
  if (!store.world && !store.loading) void store.loadWorld()
})

function reload(): void {
  void store.loadWorld()
}

// ---------------------------------------------------------------- 安全文本

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

function fractionText(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  return `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`
}

// ---------------------------------------------------------------- 数据投影

interface FactRow {
  label: string
  value: string
}

interface SpaceRow {
  id: string
  name: string
  kind: string
}

const spaceRows = computed<SpaceRow[]>(() =>
  (store.world?.spaces ?? []).map((space) => {
    const record = space as unknown as Record<string, unknown>
    return {
      id: text(record.space_id ?? record.id, ''),
      name: text(record.name, '未命名空间'),
      kind: text(record.kind, ''),
    }
  }),
)

const objectRows = computed<FactRow[]>(() =>
  (store.world?.objects ?? []).map((raw) => {
    const record = raw as Record<string, unknown>
    return {
      label: text(record.name, '未命名物件'),
      value: [text(record.kind, ''), text(record.space_id, '')]
        .filter((part) => part !== '')
        .join(' · ') || '—',
    }
  }),
)

function itemsText(items: unknown): string {
  if (items === null || items === undefined) return '—'
  if (typeof items === 'object' && !Array.isArray(items)) {
    const entries = Object.entries(items as Record<string, unknown>)
    if (entries.length === 0) return '空'
    return entries.map(([name, count]) => `${name}×${text(count)}`).join('、')
  }
  return text(items)
}

const inventoryRows = computed<FactRow[]>(() =>
  Object.entries(store.world?.inventories ?? {}).map(([key, raw]) => {
    const record = (raw ?? {}) as Record<string, unknown>
    const capacity = record.capacity
    return {
      label: text(record.name, key),
      value: `${itemsText(record.items)}${
        typeof capacity === 'number' && capacity > 0 ? ` · 容量 ${capacity}` : ' · 容量不限'
      }`,
    }
  }),
)

const pet = computed<Record<string, unknown> | null>(() => {
  const raw = store.world?.pet
  return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : null
})

const petRows = computed<FactRow[]>(() => {
  const record = pet.value
  if (!record) return []
  return [
    { label: '名字', value: text(record.name) },
    { label: '种类', value: text(record.species) },
    { label: '活动', value: text(record.activity) },
    { label: '地点', value: text(record.location) },
    { label: '心情', value: text(record.mood) },
    { label: '饥饿', value: fractionText(record.hunger) },
    { label: '精力', value: fractionText(record.energy) },
    { label: '亲密度', value: fractionText(record.affection) },
  ]
})

const petLine = computed(() => (typeof pet.value?.line === 'string' ? pet.value.line : ''))

const PRESENCE_LABELS: Record<string, string> = {
  active: '在场',
  lurking: '潜水',
  offline: '离线',
}

const socialSpaceRows = computed<FactRow[]>(() =>
  (store.world?.social_spaces ?? []).map((raw) => {
    const record = raw as Record<string, unknown>
    const presence = text(record.character_presence, '')
    const participants = record.participants
    return {
      label: text(record.name, '未命名空间'),
      value: [
        text(record.kind, ''),
        presence ? (PRESENCE_LABELS[presence] ?? presence) : '',
        Array.isArray(participants) ? `${participants.length} 人` : '',
      ]
        .filter((part) => part !== '')
        .join(' · ') || '—',
    }
  }),
)

const goals = computed(() => {
  const full = store.world?.goals_full
  if (full && full.length > 0) return full
  return store.world?.goals ?? []
})

const interrupted = computed(() => store.world?.interrupted ?? null)
const interruptedPercent = computed<number | null>(() => {
  const raw = interrupted.value?.progress
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return null
  return Math.round(Math.min(1, Math.max(0, raw)) * 100)
})
const interruptedRemaining = computed(() => {
  const minutes = interrupted.value?.remaining_minutes
  if (typeof minutes !== 'number' || !Number.isFinite(minutes)) return '—'
  return `${minutes} 分钟`
})

const rawJson = computed(() => (store.world ? JSON.stringify(store.world, null, 2) : ''))

// ------------------------------------------------------------ 管理员操作

type ControlAction = 'pause' | 'resume' | 'reset' | 'reinitialize'

interface ControlMeta {
  label: string
  title: string
  message: string
  detail: string
  confirmText: string
  danger: boolean
}

const CONTROL_META: Record<ControlAction, ControlMeta> = {
  pause: {
    label: '暂停',
    title: '暂停世界',
    message: '确定暂停沙盒世界吗？',
    detail: '暂停后调度不再推进；可以随时恢复。',
    confirmText: '暂停',
    danger: false,
  },
  resume: {
    label: '恢复',
    title: '恢复世界',
    message: '确定恢复沙盒世界吗？',
    detail: '恢复后调度继续推进，世界从暂停点接着运行。',
    confirmText: '恢复',
    danger: false,
  },
  reset: {
    label: '重置',
    title: '重置世界',
    message: '确定重置沙盒世界吗？',
    detail: '重置不可撤销：世界回到初始种子状态，运行中的动作与进度都会丢失。',
    confirmText: '重置',
    danger: true,
  },
  reinitialize: {
    label: '重新初始化',
    title: '重新初始化世界',
    message: '确定重新初始化沙盒世界吗？',
    detail: '重新初始化不可撤销：会按当前种子重建世界，运行期状态全部清空。',
    confirmText: '重新初始化',
    danger: true,
  },
}

const CONTROL_ACTIONS = Object.keys(CONTROL_META) as ControlAction[]

const pending = ref<ControlAction | null>(null)
const controlError = ref('')

function askControl(control: ControlAction): void {
  controlError.value = ''
  pending.value = control
}

async function confirmControl(): Promise<void> {
  const control = pending.value
  pending.value = null
  if (control === null) return
  const ok = await store.control(control, control)
  if (ok) {
    toast.success(`已执行「${CONTROL_META[control].label}」`)
  } else {
    controlError.value = store.error
    toast.error(store.error || '操作失败')
  }
}
</script>

<template>
  <div class="cb-world" data-test="world-page">
    <ErrorState v-if="store.error" :message="store.error" @retry="reload" />

    <LoadingState v-if="store.loading && !store.world" label="正在读取世界状态…" :rows="4" />

    <template v-else-if="store.world">
      <div class="cb-world__grid">
        <WorldStateCard :world="store.world" :stale="store.stale" />
        <ActionCard :action="store.world.action ?? null" />
      </div>

      <NeedList :needs="store.world.needs_full ?? []" />

      <section class="cb-world__section" data-test="world-goals">
        <SectionHeader title="目标" description="来自运行期目标；显示真实状态与进度，不做美化" />
        <EmptyState
          v-if="goals.length === 0"
          title="暂无目标"
          description="角色尚未形成目标，或沙盒未运行。"
        />
        <div v-else class="cb-world__grid">
          <GoalCard v-for="goal in goals" :key="goal.goal_id || goal.title" :goal="goal" />
        </div>
      </section>

      <section
        v-if="interrupted && interrupted.active"
        class="cb-card cb-world__section"
        data-test="world-interrupted"
      >
        <SectionHeader title="被打断的动作" description="InterruptedAction 只读记录，WebUI 不提供修改" />
        <dl class="cb-world__facts">
          <div class="cb-world__fact">
            <dt class="cb-caption">动作定义</dt>
            <dd data-test="interrupted-definition">{{ text(interrupted.definition_id) }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">剩余时间</dt>
            <dd data-test="interrupted-remaining">{{ interruptedRemaining }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">打断原因</dt>
            <dd data-test="interrupted-reason">{{ text(interrupted.reason) }}</dd>
          </div>
          <div class="cb-world__fact">
            <dt class="cb-caption">进度</dt>
            <dd data-test="interrupted-progress">
              {{ interruptedPercent === null ? '—' : `${interruptedPercent}%` }}
            </dd>
          </div>
        </dl>
        <p class="cb-world__readonly" data-test="interrupted-readonly">只读</p>
      </section>

      <div v-if="spaceRows.length > 0 || objectRows.length > 0 || inventoryRows.length > 0 || pet" class="cb-world__grid">
        <section v-if="spaceRows.length > 0" class="cb-card cb-world__section" data-test="world-spaces">
          <SectionHeader title="空间" description="世界里的地点（只读）" />
          <ul class="cb-world__list">
            <li v-for="space in spaceRows" :key="space.id || space.name" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="space-name">{{ space.name }}</span>
              <span class="cb-world__list-meta">{{ space.kind || '—' }}</span>
            </li>
          </ul>
        </section>

        <section v-if="objectRows.length > 0" class="cb-card cb-world__section" data-test="world-objects">
          <SectionHeader title="物件" description="可交互物品（只读）" />
          <ul class="cb-world__list">
            <li v-for="(row, index) in objectRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="object-name">{{ row.label }}</span>
              <span class="cb-world__list-meta">{{ row.value }}</span>
            </li>
          </ul>
        </section>

        <section v-if="inventoryRows.length > 0" class="cb-card cb-world__section" data-test="world-inventory">
          <SectionHeader title="库存" description="容器与随身物品（只读）" />
          <ul class="cb-world__list">
            <li v-for="(row, index) in inventoryRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
              <span class="cb-world__list-name" data-test="inventory-name">{{ row.label }}</span>
              <span class="cb-world__list-meta">{{ row.value }}</span>
            </li>
          </ul>
        </section>

        <section v-if="pet" class="cb-card cb-world__section" data-test="world-pet">
          <SectionHeader title="宠物" description="运行期宠物状态（只读）" />
          <dl class="cb-world__facts">
            <div v-for="row in petRows" :key="row.label" class="cb-world__fact">
              <dt class="cb-caption">{{ row.label }}</dt>
              <dd>{{ row.value }}</dd>
            </div>
          </dl>
          <p v-if="petLine" class="cb-world__pet-line">{{ petLine }}</p>
        </section>
      </div>

      <section class="cb-card cb-world__section" data-test="world-social-spaces">
        <SectionHeader title="社交空间" description="角色在线的社交场所（只读）" />
        <EmptyState
          v-if="socialSpaceRows.length === 0"
          title="暂无社交空间"
          description="沙盒未启用社交空间或尚未产生数据。"
        />
        <ul v-else class="cb-world__list">
          <li v-for="(row, index) in socialSpaceRows" :key="`${row.label}-${index}`" class="cb-world__list-item">
            <span class="cb-world__list-name" data-test="social-space-name">{{ row.label }}</span>
            <span class="cb-world__list-meta">{{ row.value }}</span>
          </li>
        </ul>
      </section>

      <details class="cb-card cb-world__expert" data-test="world-expert">
        <summary class="cb-world__expert-summary">Expert 数据：action_defs / modes_defs / 原始 JSON</summary>
        <div class="cb-world__expert-body">
          <h3 class="cb-world__expert-title">action_defs</h3>
          <EmptyState
            v-if="(store.world.action_defs ?? []).length === 0"
            title="无 action_defs"
            description="接口未返回动作定义。"
          />
          <ul v-else class="cb-world__list" data-test="world-action-defs">
            <li
              v-for="definition in store.world.action_defs ?? []"
              :key="definition.definition_id"
              class="cb-world__list-item"
            >
              <span class="cb-world__list-name">{{ definition.definition_id }}</span>
              <span class="cb-world__list-meta">{{ definition.name }}</span>
            </li>
          </ul>

          <h3 class="cb-world__expert-title">modes_defs</h3>
          <ul v-if="(store.world.modes_defs ?? []).length > 0" class="cb-world__list" data-test="world-modes-defs">
            <li
              v-for="mode in store.world.modes_defs ?? []"
              :key="mode.key"
              class="cb-world__list-item"
            >
              <span class="cb-world__list-name">{{ mode.key }}</span>
              <span class="cb-world__list-meta">{{ mode.label }}</span>
            </li>
          </ul>
          <p v-else class="cb-muted">接口未返回 modes_defs。</p>

          <h3 class="cb-world__expert-title">原始 JSON</h3>
          <pre class="cb-world__json" data-test="world-expert-json">{{ rawJson }}</pre>
        </div>
      </details>
    </template>

    <EmptyState
      v-else-if="!store.loading"
      title="暂无世界数据"
      description="沙盒未运行，或接口尚未返回世界快照。"
    />

    <section class="cb-card cb-world__section cb-world__admin">
      <details data-test="world-admin">
        <summary class="cb-world__admin-summary">管理员操作</summary>
        <p class="cb-caption">
          这些操作直接影响运行中的世界；执行前会再次确认。reset / reinitialize 不可撤销。
        </p>
        <div class="cb-world__admin-actions">
          <button
            v-for="control in CONTROL_ACTIONS"
            :key="control"
            type="button"
            class="cb-world__control"
            :class="{ 'cb-world__control--danger': CONTROL_META[control].danger }"
            :data-test="`world-control-${control}`"
            @click="askControl(control)"
          >
            {{ CONTROL_META[control].label }}
          </button>
        </div>
        <p v-if="controlError" class="cb-world__control-error" data-test="world-control-error" role="alert">
          {{ controlError }}
        </p>
      </details>
    </section>

    <ConfirmDialog
      :show="pending !== null"
      :title="pending ? CONTROL_META[pending].title : ''"
      :message="pending ? CONTROL_META[pending].message : ''"
      :detail="pending ? CONTROL_META[pending].detail : ''"
      :confirm-text="pending ? CONTROL_META[pending].confirmText : '确认'"
      :danger="pending ? CONTROL_META[pending].danger : false"
      @confirm="confirmControl"
      @cancel="pending = null"
    />
  </div>
</template>

<style scoped>
.cb-world {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-world__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: var(--cb-space-4);
  align-items: start;
}

.cb-world__section {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-world__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--cb-space-3) var(--cb-space-4);
  margin: 0;
}

.cb-world__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-world__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-world__list-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding-bottom: var(--cb-space-2);
  border-bottom: 1px solid var(--cb-border);
}

.cb-world__list-item:last-child {
  padding-bottom: 0;
  border-bottom: none;
}

.cb-world__list-name {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-world__list-meta {
  flex: none;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  text-align: right;
  overflow-wrap: anywhere;
}

.cb-world__readonly {
  align-self: flex-start;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
}

.cb-world__pet-line {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-world__expert summary {
  cursor: pointer;
}

.cb-world__expert-summary {
  color: var(--cb-text-md);
}

.cb-world__expert-body {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin-top: var(--cb-space-3);
}

.cb-world__expert-title {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-family: var(--cb-font-mono);
}

.cb-world__json {
  max-height: 360px;
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.cb-world__admin summary {
  cursor: pointer;
}

.cb-world__admin-summary {
  color: var(--cb-text-md);
}

.cb-world__admin-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-2);
  margin-top: var(--cb-space-3);
}

.cb-world__control {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-world__control:hover {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-world__control--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-world__control--danger:hover {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}

.cb-world__control-error {
  margin-top: var(--cb-space-3);
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}
</style>
