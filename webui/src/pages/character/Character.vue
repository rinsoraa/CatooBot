<script setup lang="ts">
/**
 * 当前角色页（W5 §6-§9、§62、§67-§75、§145）。
 *
 * 只展示运行状态（persona 名字 + character state + 世界快照 + runtime 概览）；
 * Character Bible（identity/personality/speaking_style/behavior_rules/system_prompt）
 * 绝不通过本页返回：出现即跳过，只在 Expert 折叠区标注「已隐藏」。
 * 修改请到系统设置；本页不发起任何写请求，也不轮询（首屏 REST + realtime 驱动）。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { errorMessage } from '@/api/client'
import { worldApi, type CharacterPayload } from '@/api/world'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ActionCard from '@/components/domain/ActionCard.vue'
import WorldStateCard from '@/components/domain/WorldStateCard.vue'
import { useRuntimeStore } from '@/stores/runtime'
import { useWorldStore } from '@/stores/world'

const runtime = useRuntimeStore()
const worldStore = useWorldStore()

const character = ref<CharacterPayload | null>(null)
const loading = ref(false)
const error = ref('')

async function loadCharacter(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    character.value = await worldApi.character()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadCharacter()
  if (!worldStore.world && !worldStore.loading) void worldStore.loadWorld()
  if (!runtime.overview && !runtime.loading) void runtime.refresh()
})

function retry(): void {
  void loadCharacter()
  void worldStore.loadWorld()
  void runtime.refresh()
}

// ------------------------------------------------------------------ persona

/** 只看名字；其余 persona 字段一律不渲染（可能是 Character Bible）。 */
function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

const persona = computed<Record<string, unknown>>(() => character.value?.persona ?? {})
const characterName = computed(() => text(persona.value.name))

/** Bible / 背景类键：出现即隐藏（§6「绝不返回真实 Character Bible」）。 */
const HIDDEN_KEY_PATTERN =
  /bible|background|backstory|personality|speaking[_-]?style|behavior[_-]?rules|system[_-]?prompt|identity|prompt|背景|设定/i
const hiddenKeys = computed(() => Object.keys(persona.value).filter((key) => HIDDEN_KEY_PATTERN.test(key)))
const hiddenText = computed(() =>
  hiddenKeys.value.length > 0
    ? `${hiddenKeys.value.join('、')}（已隐藏）`
    : '无',
)
const sourceText = computed(() => text(character.value?.source))

// ------------------------------------------------------------ 当前状态卡

const MOOD_LABELS: Record<string, string> = {
  down: '低落',
  quiet: '安静',
  neutral: '平静',
  happy: '开心',
  cheerful: '兴奋',
}
const SOCIAL_LABELS: Record<string, string> = {
  alone: '独处',
  chatting: '聊天中',
  with_friends: '与朋友一起',
  quiet: '安静',
}
const SCHEDULE_LABELS: Record<string, string> = {
  awake: '清醒',
  resting: '休息中',
  sleeping: '睡眠中',
  busy: '忙碌',
}

const state = computed<Record<string, unknown>>(
  () => (character.value?.state ?? {}) as Record<string, unknown>,
)

function rawStateValue(key: string): unknown {
  return state.value[key]
}

function stateText(key: string): string {
  return text(rawStateValue(key))
}

function mappedStateText(key: string, labels: Record<string, string>): string {
  const raw = text(rawStateValue(key), '')
  if (!raw) return '—'
  return labels[raw] ?? raw
}

function energyText(): string {
  const raw = rawStateValue('energy')
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return '—'
  return `${Math.round(Math.min(1, Math.max(0, raw)) * 100)}%`
}

interface StateRow {
  key: string
  label: string
  value: string
}

const stateRows = computed<StateRow[]>(() => [
  { key: 'mood', label: '心情', value: mappedStateText('mood', MOOD_LABELS) },
  { key: 'energy', label: '精力', value: energyText() },
  { key: 'activity', label: '正在做什么', value: stateText('activity') },
  { key: 'current_focus', label: '当前关注', value: stateText('current_focus') },
  { key: 'location', label: '地点', value: stateText('location') },
  { key: 'social_state', label: '社交状态', value: mappedStateText('social_state', SOCIAL_LABELS) },
  {
    key: 'schedule_state',
    label: '日程状态',
    value: mappedStateText('schedule_state', SCHEDULE_LABELS),
  },
  { key: 'current_goal', label: '当前目标', value: stateText('current_goal') },
  { key: 'current_project', label: '当前项目', value: stateText('current_project') },
])

// ------------------------------------------------------------ 状态条

const onlineText = computed(() => {
  const online = runtime.overview?.qq?.online
  if (online === null || online === undefined) return '—'
  return online ? '在线' : '离线'
})
const qqText = computed(() => {
  const selfId = runtime.overview?.qq?.self_id
  if (selfId === null || selfId === undefined) return '—'
  return String(selfId)
})
const aiText = computed(() => {
  const enabled = runtime.overview?.ai?.enabled
  if (enabled === null || enabled === undefined) return '—'
  return enabled ? '已启用' : '已停用'
})

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days}天 ${hours}小时`
  if (hours > 0) return `${hours}小时 ${minutes}分`
  return `${minutes}分 ${total % 60}秒`
}

const uptimeSeconds = computed(() => {
  const detail = runtime.runtime?.uptime_seconds
  if (detail !== null && detail !== undefined) return detail
  return runtime.overview?.runtime?.uptime_seconds ?? null
})
const uptimeText = computed(() =>
  uptimeSeconds.value === null ? '—' : formatDuration(uptimeSeconds.value),
)
const schedulerText = computed(() => {
  const running = runtime.scheduler?.running
  if (running === null || running === undefined) return '—'
  return running ? '运行中' : '已停止'
})
</script>

<template>
  <div class="cb-character" data-test="character-page">
    <ErrorState v-if="error" :message="error" @retry="retry" />

    <LoadingState v-if="loading && character === null" label="正在读取角色状态…" :rows="3" />

    <template v-else>
      <p class="cb-character__readonly" data-test="character-readonly">
        角色页面只展示运行状态；修改请到系统设置。
      </p>

      <section class="cb-card cb-character__statusbar" data-test="character-statusbar">
        <div class="cb-character__identity">
          <p class="cb-caption">角色</p>
          <p class="cb-character__name" data-test="character-name">{{ characterName }}</p>
        </div>
        <dl class="cb-character__status-facts">
          <div class="cb-character__status-fact">
            <dt class="cb-caption">在线</dt>
            <dd data-test="character-online">{{ onlineText }}</dd>
          </div>
          <div class="cb-character__status-fact">
            <dt class="cb-caption">Runtime</dt>
            <dd data-test="character-uptime">{{ uptimeText }}</dd>
          </div>
          <div class="cb-character__status-fact">
            <dt class="cb-caption">AI</dt>
            <dd data-test="character-ai">{{ aiText }}</dd>
          </div>
          <div class="cb-character__status-fact">
            <dt class="cb-caption">QQ</dt>
            <dd data-test="character-qq">{{ qqText }}</dd>
          </div>
        </dl>
      </section>

      <section class="cb-card cb-character__state" data-test="character-state">
        <SectionHeader title="当前状态" description="mood / energy / activity 等，全部来自运行期快照" />
        <dl class="cb-character__state-facts">
          <div
            v-for="row in stateRows"
            :key="row.key"
            class="cb-character__state-fact"
          >
            <dt class="cb-caption">{{ row.label }}</dt>
            <dd :data-test="`character-state-${row.key}`">{{ row.value }}</dd>
          </div>
        </dl>
      </section>

      <section class="cb-card cb-character__runtime" data-test="character-runtime">
        <SectionHeader title="运行状态" description="进程与调度器的真实读数" />
        <dl class="cb-character__runtime-facts">
          <div class="cb-character__runtime-fact">
            <dt class="cb-caption">运行时长</dt>
            <dd data-test="character-runtime-uptime">{{ uptimeText }}</dd>
          </div>
          <div class="cb-character__runtime-fact">
            <dt class="cb-caption">调度器</dt>
            <dd data-test="character-runtime-scheduler">{{ schedulerText }}</dd>
          </div>
          <div class="cb-character__runtime-fact">
            <dt class="cb-caption">AI</dt>
            <dd data-test="character-runtime-ai">{{ aiText }}</dd>
          </div>
        </dl>
      </section>

      <div class="cb-character__world-head">
        <SectionHeader title="当前世界" description="只读摘要；完整信息在世界页" />
        <RouterLink class="cb-character__world-link" to="/character/world" data-test="character-view-world">
          查看世界
        </RouterLink>
      </div>
      <WorldStateCard :world="worldStore.world" :stale="worldStore.stale" />
      <ActionCard :action="worldStore.world?.action ?? null" />

      <details class="cb-character__expert" data-test="character-expert">
        <summary class="cb-caption">Expert 信息</summary>
        <p class="cb-character__expert-text" data-test="character-source">
          来源: {{ sourceText }}
        </p>
        <p class="cb-character__expert-text" data-test="character-hidden">
          Character Bible 字段: {{ hiddenText }}
        </p>
      </details>
    </template>
  </div>
</template>

<style scoped>
.cb-character {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-character__readonly {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-character__statusbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--cb-space-4);
}

.cb-character__name {
  margin-top: var(--cb-space-1);
  color: var(--cb-text);
  font-size: var(--cb-text-xl);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.cb-character__status-facts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-5);
  margin: 0;
}

.cb-character__status-fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  font-variant-numeric: tabular-nums;
}

.cb-character__state,
.cb-character__runtime {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-character__state-facts,
.cb-character__runtime-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: var(--cb-space-3) var(--cb-space-4);
  margin: 0;
}

.cb-character__state-fact dd,
.cb-character__runtime-fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-character__world-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--cb-space-3);
}

.cb-character__world-link {
  flex: none;
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  font-size: var(--cb-text-sm);
}

.cb-character__world-link:hover {
  border-color: var(--cb-primary);
  text-decoration: none;
}

.cb-character__expert summary {
  cursor: pointer;
}

.cb-character__expert-text {
  margin-top: var(--cb-space-2);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}
</style>
