<script setup lang="ts">
/**
 * 当前角色页（W5 §6-§9、§62、§67-§75、§145）。
 *
 * 只读区块（状态条 / 当前状态 / runtime / 世界快照）保持原样；本页补齐两个
 * 编辑区：
 *  - 人设（identity / personality / speaking_style / behavior_rules /
 *    system_prompt）经 PATCH /character 保存并热加载；
 *  - 当前状态（mood / energy / activity / current_focus）经
 *    PATCH /character/state 保存。
 * 两个表单都只提交改动字段；persona 后端是顶层浅合并，因此某个嵌套组有
 * 改动时整组提交（否则未提交的兄弟字段会被 pydantic 默认值覆盖）。
 */
import { NInput, NInputNumber, NSwitch } from 'naive-ui'
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { errorMessage } from '@/api/client'
import { worldApi, type CharacterPayload, type CharacterState } from '@/api/world'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import ActionCard from '@/components/domain/ActionCard.vue'
import WorldStateCard from '@/components/domain/WorldStateCard.vue'
import { toast } from '@/composables/toast'
import { useRuntimeStore } from '@/stores/runtime'
import { useWorldStore } from '@/stores/world'
import type {
  CharacterExportDocument,
  CharacterIdentity,
  CharacterImportPreview,
  CharacterPersonaPatch,
  CharacterPersonality,
  CharacterSpeakingStyle,
} from '@/types/domain'

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
    syncPersonaDraft()
    syncStateDraft()
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

/** 只看名字；其余 persona 字段进入编辑区。 */
function text(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return fallback
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

function asString(value: unknown): string {
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return ''
}

function asBool(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => asString(item)).filter((item) => item.length > 0)
}

const personaValue = computed<Record<string, unknown>>(() => asRecord(character.value?.persona))
/** 状态条显示 persona 顶层 name（人物档案编译出的展示名）。 */
const characterName = computed(() => text(personaValue.value.name))
const sourceText = computed(() => text(character.value?.source))

// ---------------------------------------------------------------- 人设表单

const IDENTITY_KEYS = [
  'name',
  'nickname',
  'age',
  'birthday',
  'gender',
  'occupation',
  'location',
  'background',
] as const
type IdentityKey = (typeof IDENTITY_KEYS)[number]

/** 单行展示的身份字段；background 是宽文本域，单独渲染。 */
const IDENTITY_TEXT_KEYS = [
  'name',
  'nickname',
  'age',
  'birthday',
  'gender',
  'occupation',
  'location',
] as const

const IDENTITY_LABELS: Record<IdentityKey, string> = {
  name: '名字',
  nickname: '昵称',
  age: '年龄',
  birthday: '生日',
  gender: '性别',
  occupation: '职业',
  location: '所在地',
  background: '背景故事',
}

const PERSONALITY_KEYS = ['traits', 'likes', 'dislikes', 'habits', 'interests'] as const
type PersonalityKey = (typeof PERSONALITY_KEYS)[number]

const PERSONALITY_LABELS: Record<PersonalityKey, string> = {
  traits: '性格 traits',
  likes: '喜欢',
  dislikes: '不喜欢',
  habits: '习惯',
  interests: '兴趣',
}

interface PersonaDraft {
  identity: Record<IdentityKey, string>
  personality: Record<PersonalityKey, string>
  speaking: {
    tone: string
    lengthPreference: string
    emoji: boolean
    kaomoji: boolean
    notes: string
  }
  rulesText: string
  systemPrompt: string
}

function emptyPersonaDraft(): PersonaDraft {
  return {
    identity: {
      name: '',
      nickname: '',
      age: '',
      birthday: '',
      gender: '',
      occupation: '',
      location: '',
      background: '',
    },
    personality: { traits: '', likes: '', dislikes: '', habits: '', interests: '' },
    speaking: { tone: '', lengthPreference: 'mixed', emoji: true, kaomoji: false, notes: '' },
    rulesText: '',
    systemPrompt: '',
  }
}

const personaDraft = reactive<PersonaDraft>(emptyPersonaDraft())
const personaSaving = ref(false)
const personaError = ref('')

/** 与旧版 /character 表单一致：顿号、逗号、分号、换行都算列表分隔符。 */
function parseList(value: string): string[] {
  return value
    .split(/[、,，;；\n]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/** 与旧版 /character 表单一致：行为规则每行一条。 */
function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
}

function joinList(items: string[]): string {
  return items.join('、')
}

function setEmoji(value: boolean): void {
  personaDraft.speaking.emoji = value
}

function setKaomoji(value: boolean): void {
  personaDraft.speaking.kaomoji = value
}

function sameList(left: string[], right: string[]): boolean {
  return left.join('\u0000') === right.join('\u0000')
}

/** 用服务端 persona 覆盖草稿（首屏加载 / 保存成功 / 放弃修改）。 */
function syncPersonaDraft(): void {
  const current = personaValue.value
  const identity = asRecord(current.identity)
  const personality = asRecord(current.personality)
  const speaking = asRecord(current.speaking_style)
  for (const key of IDENTITY_KEYS) personaDraft.identity[key] = asString(identity[key])
  for (const key of PERSONALITY_KEYS) {
    personaDraft.personality[key] = joinList(asStringList(personality[key]))
  }
  personaDraft.speaking.tone = asString(speaking.tone)
  personaDraft.speaking.lengthPreference = asString(speaking.length_preference) || 'mixed'
  personaDraft.speaking.emoji = asBool(speaking.emoji, true)
  personaDraft.speaking.kaomoji = asBool(speaking.kaomoji, false)
  personaDraft.speaking.notes = asString(speaking.notes)
  personaDraft.rulesText = asStringList(asRecord(current.behavior_rules).rules).join('\n')
  personaDraft.systemPrompt = asString(current.system_prompt)
  personaError.value = ''
}

function buildPersonaPayload(): CharacterPersonaPatch {
  const patch: CharacterPersonaPatch = {}
  const current = personaValue.value

  const identity: CharacterIdentity = {
    name: personaDraft.identity.name.trim(),
    nickname: personaDraft.identity.nickname.trim(),
    age: personaDraft.identity.age.trim(),
    birthday: personaDraft.identity.birthday.trim(),
    gender: personaDraft.identity.gender.trim(),
    occupation: personaDraft.identity.occupation.trim(),
    location: personaDraft.identity.location.trim(),
    background: personaDraft.identity.background.trim(),
  }
  const currentIdentity = asRecord(current.identity)
  if (IDENTITY_KEYS.some((key) => identity[key] !== asString(currentIdentity[key]))) {
    patch.identity = identity
  }

  const personality: CharacterPersonality = {
    traits: parseList(personaDraft.personality.traits),
    likes: parseList(personaDraft.personality.likes),
    dislikes: parseList(personaDraft.personality.dislikes),
    habits: parseList(personaDraft.personality.habits),
    interests: parseList(personaDraft.personality.interests),
  }
  const currentPersonality = asRecord(current.personality)
  if (PERSONALITY_KEYS.some((key) => !sameList(personality[key], asStringList(currentPersonality[key])))) {
    patch.personality = personality
  }

  const currentSpeaking = asRecord(current.speaking_style)
  const speaking: CharacterSpeakingStyle = {
    // language 旧版不可编辑：保留服务端现值（缺省 zh-CN）。
    language: asString(currentSpeaking.language) || 'zh-CN',
    tone: personaDraft.speaking.tone.trim(),
    emoji: personaDraft.speaking.emoji,
    kaomoji: personaDraft.speaking.kaomoji,
    length_preference: personaDraft.speaking.lengthPreference.trim() || 'mixed',
    notes: personaDraft.speaking.notes.trim(),
  }
  const speakingCurrent: CharacterSpeakingStyle = {
    language: asString(currentSpeaking.language) || 'zh-CN',
    tone: asString(currentSpeaking.tone),
    emoji: asBool(currentSpeaking.emoji, true),
    kaomoji: asBool(currentSpeaking.kaomoji, false),
    length_preference: asString(currentSpeaking.length_preference) || 'mixed',
    notes: asString(currentSpeaking.notes),
  }
  const speakingKeys: (keyof CharacterSpeakingStyle)[] = [
    'language',
    'tone',
    'emoji',
    'kaomoji',
    'length_preference',
    'notes',
  ]
  if (speakingKeys.some((key) => speaking[key] !== speakingCurrent[key])) {
    patch.speaking_style = speaking
  }

  const rules = parseLines(personaDraft.rulesText)
  if (!sameList(rules, asStringList(asRecord(current.behavior_rules).rules))) {
    patch.behavior_rules = { rules }
  }

  const systemPrompt = personaDraft.systemPrompt.trim()
  if (systemPrompt !== asString(current.system_prompt)) patch.system_prompt = systemPrompt

  return patch
}

const personaDirty = computed(() => Object.keys(buildPersonaPayload()).length > 0)

async function savePersona(): Promise<void> {
  if (personaSaving.value) return
  const payload = buildPersonaPayload()
  if (Object.keys(payload).length === 0) {
    toast.info('没有需要保存的修改')
    return
  }
  personaSaving.value = true
  personaError.value = ''
  try {
    character.value = await worldApi.saveCharacter(payload)
    syncPersonaDraft()
    toast.success('人设已保存', '已热加载到运行中的角色')
  } catch (caught) {
    // 失败时保留草稿；后端 message 原样展示，不改写。
    personaError.value = errorMessage(caught)
    toast.error('保存失败', personaError.value)
  } finally {
    personaSaving.value = false
  }
}

function cancelPersona(): void {
  syncPersonaDraft()
  toast.info('已放弃未保存的人设修改')
}

// ------------------------------------------------------------ 当前状态编辑

const stateDraft = reactive<{ mood: string; energy: number | null; activity: string; currentFocus: string }>({
  mood: '',
  energy: null,
  activity: '',
  currentFocus: '',
})
const stateSaving = ref(false)
const stateError = ref('')

function syncStateDraft(): void {
  const current = state.value
  stateDraft.mood = asString(current.mood)
  stateDraft.energy =
    typeof current.energy === 'number' && Number.isFinite(current.energy) ? current.energy : null
  stateDraft.activity = asString(current.activity)
  stateDraft.currentFocus = asString(current.current_focus)
  stateError.value = ''
}

function buildStatePayload(): Partial<CharacterState> {
  const payload: Partial<CharacterState> = {}
  const current = state.value

  const mood = stateDraft.mood.trim()
  if (mood !== asString(current.mood)) payload.mood = mood

  if (typeof stateDraft.energy === 'number' && Number.isFinite(stateDraft.energy)) {
    const currentEnergy =
      typeof current.energy === 'number' && Number.isFinite(current.energy) ? current.energy : null
    if (currentEnergy === null || Math.abs(currentEnergy - stateDraft.energy) > 1e-9) {
      payload.energy = stateDraft.energy
    }
  }

  const activity = stateDraft.activity.trim()
  if (activity !== asString(current.activity)) payload.activity = activity

  const focus = stateDraft.currentFocus.trim()
  if (focus !== asString(current.current_focus)) payload.current_focus = focus

  return payload
}

const stateDirty = computed(() => Object.keys(buildStatePayload()).length > 0)

async function saveState(): Promise<void> {
  if (stateSaving.value) return
  const payload = buildStatePayload()
  if (Object.keys(payload).length === 0) {
    toast.info('没有需要保存的修改')
    return
  }
  stateSaving.value = true
  stateError.value = ''
  try {
    const updated = await worldApi.setState(payload)
    if (character.value) character.value.state = updated
    syncStateDraft()
    toast.success('状态已更新', '新的叙事状态已立即生效')
  } catch (caught) {
    stateError.value = errorMessage(caught)
    toast.error('状态更新失败', stateError.value)
  } finally {
    stateSaving.value = false
  }
}

function cancelState(): void {
  syncStateDraft()
  toast.info('已放弃未保存的状态修改')
}

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

const state = computed<Record<string, unknown>>(() => asRecord(character.value?.state))

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

const uptimeSeconds = computed(() => runtime.uptimeSeconds)
const uptimeText = computed(() =>
  uptimeSeconds.value === null ? '—' : formatDuration(uptimeSeconds.value),
)
const schedulerText = computed(() => {
  const running = runtime.scheduler?.running
  if (running === null || running === undefined) return '—'
  return running ? '运行中' : '已停止'
})

// ------------------------------------------------------------ 导入导出
/**
 * v0.8 `/character/export` + 两步式 `/character/import` 迁移：
 * 导出直接下载 JSON；导入先 POST 预览（applied=false），确认后才带
 * `confirm: "import"` 真正写入。
 */
const exporting = ref(false)
const exportError = ref('')
const importFileName = ref('')
const importText = ref('')
const importDocument = ref<CharacterExportDocument | null>(null)
const importPreviewing = ref(false)
const importApplying = ref(false)
const importPreview = ref<CharacterImportPreview | null>(null)
const importApplied = ref(false)
const importError = ref('')
const pendingImport = ref(false)

function downloadJson(doc: CharacterExportDocument): void {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  anchor.href = url
  anchor.download = `catoobot-character-${stamp}.json`
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

async function exportCharacter(): Promise<void> {
  if (exporting.value) return
  exporting.value = true
  exportError.value = ''
  try {
    const doc = await worldApi.exportCharacter()
    downloadJson(doc)
    const rows = Object.values(doc.counts ?? {}).reduce((sum, value) => sum + value, 0)
    toast.success('角色数据已导出', `共 ${rows} 行，已开始下载 JSON`)
  } catch (caught) {
    exportError.value = errorMessage(caught)
    toast.error('导出失败', exportError.value)
  } finally {
    exporting.value = false
  }
}

function resetImport(): void {
  importDocument.value = null
  importPreview.value = null
  importApplied.value = false
  importError.value = ''
}

function onImportTextInput(): void {
  resetImport()
  importFileName.value = ''
}

function parseImportDocument(raw: string): CharacterExportDocument | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    importError.value = 'JSON 解析失败：请检查文件内容'
    return null
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    importError.value = '导入文档必须是 JSON 对象'
    return null
  }
  return parsed as CharacterExportDocument
}

async function previewImport(): Promise<void> {
  if (importPreviewing.value || importApplying.value) return
  const raw = importText.value.trim()
  importPreview.value = null
  importApplied.value = false
  importError.value = ''
  if (!raw) {
    toast.warning('请先选择或粘贴要导入的 JSON 文档')
    return
  }
  const doc = parseImportDocument(raw)
  if (!doc) {
    toast.warning(importError.value || '导入文档无效')
    return
  }
  importDocument.value = doc
  importPreviewing.value = true
  try {
    const data = await worldApi.importCharacter(doc)
    importPreview.value = data.preview
    if (!data.preview.ok) {
      importError.value = `文档不可导入：${data.preview.reason ?? 'invalid_package'}`
      toast.error('文档不可导入', importError.value)
    }
  } catch (caught) {
    importDocument.value = null
    importError.value = errorMessage(caught)
    toast.error('预览失败', importError.value)
  } finally {
    importPreviewing.value = false
  }
}

async function onImportFileChange(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const text = await readFileText(file)
    importText.value = text
    resetImport()
    importFileName.value = file.name
    await previewImport()
  } catch (caught) {
    importError.value = errorMessage(caught)
    toast.error('读取文件失败', importError.value)
  } finally {
    // 允许再次选择同一个文件。
    input.value = ''
  }
}

/** FileReader 兼容性比 `File.text()` 广（jsdom 也只实现前者）。 */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'))
    reader.readAsText(file)
  })
}

const importCounts = computed(() => Object.entries(importPreview.value?.counts ?? {}))
const importRowCount = computed(() =>
  importPreview.value?.rows ?? importCounts.value.reduce((sum, [, count]) => sum + count, 0),
)

function askImport(): void {
  if (!importDocument.value || !importPreview.value?.ok || importApplying.value) return
  pendingImport.value = true
}

async function confirmImport(): Promise<void> {
  const doc = importDocument.value
  pendingImport.value = false
  if (!doc || importApplying.value) return
  importApplying.value = true
  importError.value = ''
  try {
    const data = await worldApi.importCharacter(doc, 'import')
    importPreview.value = data.preview
    importApplied.value = data.applied
    toast.success('角色数据已导入', data.applied ? '已按文档覆盖写入' : '后端未应用')
  } catch (caught) {
    importError.value = errorMessage(caught)
    toast.error('导入失败', importError.value)
  } finally {
    importApplying.value = false
  }
}
</script>

<template>
  <div class="cb-character" data-test="character-page">
    <ErrorState v-if="error" :message="error" @retry="retry" />

    <LoadingState v-if="loading && character === null" label="正在读取角色状态…" :rows="3" />

    <template v-else>
      <p class="cb-character__hint" data-test="character-editable-hint">
        人设与当前状态可直接在本页编辑；保存后会热加载到运行中的角色。
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

      <!-- 人设编辑（identity / personality / speaking_style / behavior_rules / system_prompt） -->
      <section class="cb-card cb-character__editor" data-test="character-persona">
        <SectionHeader
          title="人设"
          description="身份 / 性格 / 说话风格 / 行为规则 / System Prompt；保存后立即热加载。"
        >
          <template #actions>
            <span v-if="personaDirty" class="cb-character__dirty" data-test="persona-dirty">
              有未保存的修改
            </span>
          </template>
        </SectionHeader>

        <form class="cb-character__form" data-test="persona-form" @submit.prevent="savePersona">
          <fieldset class="cb-character__group" data-test="persona-group-identity">
            <legend class="cb-character__group-title">身份</legend>
            <div class="cb-character__grid">
              <label
                v-for="key in IDENTITY_TEXT_KEYS"
                :key="key"
                class="cb-character__field"
              >
                <span class="cb-caption">{{ IDENTITY_LABELS[key] }}</span>
                <NInput v-model:value="personaDraft.identity[key]" :data-test="`persona-identity-${key}`" />
              </label>
              <label class="cb-character__field cb-character__field--wide">
                <span class="cb-caption">{{ IDENTITY_LABELS.background }}</span>
                <NInput
                  v-model:value="personaDraft.identity.background"
                  type="textarea"
                  :rows="3"
                  data-test="persona-identity-background"
                />
              </label>
            </div>
          </fieldset>

          <fieldset class="cb-character__group" data-test="persona-group-personality">
            <legend class="cb-character__group-title">性格</legend>
            <div class="cb-character__grid">
              <label
                v-for="key in PERSONALITY_KEYS"
                :key="key"
                class="cb-character__field"
              >
                <span class="cb-caption">{{ PERSONALITY_LABELS[key] }}（用「、」或逗号分隔）</span>
                <NInput
                  v-model:value="personaDraft.personality[key]"
                  type="textarea"
                  :rows="2"
                  :data-test="`persona-personality-${key}`"
                />
              </label>
            </div>
          </fieldset>

          <fieldset class="cb-character__group" data-test="persona-group-speaking">
            <legend class="cb-character__group-title">说话风格</legend>
            <div class="cb-character__grid">
              <label class="cb-character__field">
                <span class="cb-caption">语气 tone</span>
                <NInput v-model:value="personaDraft.speaking.tone" data-test="persona-speaking-tone" />
              </label>
              <label class="cb-character__field">
                <span class="cb-caption">回复长度偏好（short / mixed / long）</span>
                <NInput
                  v-model:value="personaDraft.speaking.lengthPreference"
                  data-test="persona-speaking-length"
                />
              </label>
              <label class="cb-character__field cb-character__switch-row">
                <NSwitch
                  :value="personaDraft.speaking.emoji"
                  data-test="persona-speaking-emoji"
                  @update:value="setEmoji"
                />
                <span class="cb-caption">允许 Emoji</span>
              </label>
              <label class="cb-character__field cb-character__switch-row">
                <NSwitch
                  :value="personaDraft.speaking.kaomoji"
                  data-test="persona-speaking-kaomoji"
                  @update:value="setKaomoji"
                />
                <span class="cb-caption">允许颜文字</span>
              </label>
              <label class="cb-character__field cb-character__field--wide">
                <span class="cb-caption">风格备注 style_notes</span>
                <NInput
                  v-model:value="personaDraft.speaking.notes"
                  type="textarea"
                  :rows="2"
                  data-test="persona-speaking-notes"
                />
              </label>
            </div>
          </fieldset>

          <fieldset class="cb-character__group" data-test="persona-group-rules">
            <legend class="cb-character__group-title">行为规则</legend>
            <label class="cb-character__field">
              <span class="cb-caption">每行一条，保存时去掉空行</span>
              <NInput
                v-model:value="personaDraft.rulesText"
                type="textarea"
                :rows="4"
                data-test="persona-rules"
              />
            </label>
          </fieldset>

          <fieldset class="cb-character__group" data-test="persona-group-system-prompt">
            <legend class="cb-character__group-title">System Prompt</legend>
            <label class="cb-character__field">
              <span class="cb-caption">角色自由补充（会拼在结构化人设之后）</span>
              <NInput
                v-model:value="personaDraft.systemPrompt"
                type="textarea"
                :rows="5"
                data-test="persona-system-prompt"
              />
            </label>
          </fieldset>

          <div class="cb-character__actions">
            <button
              type="submit"
              class="cb-character__button cb-character__button--primary"
              data-test="persona-save"
              :disabled="personaSaving || !personaDirty"
            >
              {{ personaSaving ? '保存中…' : '保存人设' }}
            </button>
            <button
              type="button"
              class="cb-character__button"
              data-test="persona-cancel"
              :disabled="personaSaving || !personaDirty"
              @click="cancelPersona"
            >
              放弃修改
            </button>
            <span class="cb-caption">只提交改动的字段；保存后热加载。</span>
          </div>
          <p
            v-if="personaError"
            class="cb-character__error"
            role="alert"
            data-test="persona-save-error"
          >
            {{ personaError }}
          </p>
        </form>
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

      <!-- 叙事状态编辑（mood / energy / activity / current_focus） -->
      <section class="cb-card cb-character__editor" data-test="character-state-editor">
        <SectionHeader
          title="编辑当前状态"
          description="mood / energy / activity / current_focus；保存后立即写回叙事状态。"
        >
          <template #actions>
            <span v-if="stateDirty" class="cb-character__dirty" data-test="state-dirty">
              有未保存的修改
            </span>
          </template>
        </SectionHeader>

        <form class="cb-character__form" data-test="state-form" @submit.prevent="saveState">
          <div class="cb-character__grid">
            <label class="cb-character__field">
              <span class="cb-caption">心情 mood</span>
              <NInput v-model:value="stateDraft.mood" data-test="state-mood" />
            </label>
            <label class="cb-character__field">
              <span class="cb-caption">精力 energy（0–1）</span>
              <NInputNumber
                v-model:value="stateDraft.energy"
                :min="0"
                :max="1"
                :step="0.05"
                data-test="state-energy"
              />
            </label>
            <label class="cb-character__field">
              <span class="cb-caption">正在做 activity</span>
              <NInput v-model:value="stateDraft.activity" data-test="state-activity" />
            </label>
            <label class="cb-character__field">
              <span class="cb-caption">关注 current_focus</span>
              <NInput v-model:value="stateDraft.currentFocus" data-test="state-current-focus" />
            </label>
          </div>

          <div class="cb-character__actions">
            <button
              type="submit"
              class="cb-character__button cb-character__button--primary"
              data-test="state-save"
              :disabled="stateSaving || !stateDirty"
            >
              {{ stateSaving ? '保存中…' : '保存状态' }}
            </button>
            <button
              type="button"
              class="cb-character__button"
              data-test="state-cancel"
              :disabled="stateSaving || !stateDirty"
              @click="cancelState"
            >
              放弃修改
            </button>
          </div>
          <p
            v-if="stateError"
            class="cb-character__error"
            role="alert"
            data-test="state-save-error"
          >
            {{ stateError }}
          </p>
        </form>
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
      </details>

      <section class="cb-card cb-character__transfer" data-test="character-transfer">
        <SectionHeader
          title="角色数据导入导出"
          description="导出一份完整 JSON；导入是两步式：先预览将写入的内容，再确认覆盖。"
        />
        <div class="cb-character__transfer-grid">
          <div class="cb-character__transfer-block" data-test="character-export-card">
            <p class="cb-character__transfer-title">导出</p>
            <p class="cb-caption">导出人设 / 记忆 / 关系等完整角色文档，保存为 JSON 文件。</p>
            <div class="cb-character__actions">
              <button
                type="button"
                class="cb-character__button cb-character__button--primary"
                data-test="character-export"
                :disabled="exporting"
                @click="exportCharacter"
              >
                {{ exporting ? '导出中…' : '下载 JSON' }}
              </button>
            </div>
            <p
              v-if="exportError"
              class="cb-character__error"
              role="alert"
              data-test="character-export-error"
            >
              {{ exportError }}
            </p>
          </div>

          <div class="cb-character__transfer-block" data-test="character-import">
            <p class="cb-character__transfer-title">导入</p>
            <p class="cb-caption">先预览计数，确认后才会覆盖写入；导入前后端会先备份现有数据。</p>
            <label class="cb-character__field">
              <span class="cb-caption">选择 JSON 文件</span>
              <input
                type="file"
                accept=".json,application/json"
                data-test="import-file"
                @change="onImportFileChange"
              />
            </label>
            <label class="cb-character__field">
              <span class="cb-caption">或粘贴 JSON 文档</span>
              <NInput
                v-model:value="importText"
                type="textarea"
                :rows="6"
                placeholder='{"format": "catoobot.character", ...}'
                data-test="import-text"
                @update:value="onImportTextInput"
              />
            </label>
            <div class="cb-character__actions">
              <button
                type="button"
                class="cb-character__button"
                data-test="import-preview"
                :disabled="importPreviewing || importApplying"
                @click="previewImport"
              >
                {{ importPreviewing ? '预览中…' : '预览导入' }}
              </button>
              <span v-if="importFileName" class="cb-caption" data-test="import-file-name">
                文件：{{ importFileName }}
              </span>
            </div>
            <p v-if="importError" class="cb-character__error" role="alert" data-test="import-error">
              {{ importError }}
            </p>
            <div
              v-if="importPreview && importPreview.ok"
              class="cb-character__transfer-preview"
              data-test="import-preview-result"
            >
              <p class="cb-caption">
                预览（尚未写入）：共 {{ importRowCount }} 行，将覆盖现有角色数据。
              </p>
              <ul v-if="importCounts.length > 0" class="cb-character__transfer-counts">
                <li v-for="[table, count] in importCounts" :key="table" data-test="import-count">
                  <span>{{ table }}</span>
                  <span>{{ count }}</span>
                </li>
              </ul>
              <p v-else class="cb-caption">文档里没有可写入的表。</p>
              <p
                v-if="importPreview.settings && importPreview.settings.length > 0"
                class="cb-caption"
                data-test="import-settings"
              >
                设置项：{{ importPreview.settings.join('、') }}
              </p>
              <div class="cb-character__actions">
                <button
                  type="button"
                  class="cb-character__button cb-character__button--danger"
                  data-test="import-confirm"
                  :disabled="importApplying"
                  @click="askImport"
                >
                  {{ importApplying ? '导入中…' : '确认导入' }}
                </button>
                <span
                  v-if="importApplied"
                  class="cb-character__transfer-applied"
                  role="status"
                  data-test="import-applied"
                >
                  ● 已应用
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <ConfirmDialog
        :show="pendingImport"
        title="确认导入角色数据"
        message="导入会覆盖当前角色数据（人设 / 记忆 / 关系等），确定继续吗？"
        detail="导入前会自动备份现有数据；覆盖本身不可撤销。"
        confirm-text="导入"
        danger
        @confirm="confirmImport"
        @cancel="pendingImport = false"
      />
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

.cb-character__hint {
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

/* ---------------------------------------------------------- 编辑区 */

.cb-character__editor {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-character__dirty {
  color: var(--cb-warning);
  font-size: var(--cb-text-xs);
}

.cb-character__form {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-character__group {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  min-width: 0;
  margin: 0;
  padding: var(--cb-space-3) var(--cb-space-4) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
}

.cb-character__group-title {
  padding: 0 var(--cb-space-1);
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
  font-weight: 500;
}

.cb-character__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cb-space-3);
  min-width: 0;
}

.cb-character__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.cb-character__field--wide {
  grid-column: 1 / -1;
}

.cb-character__field :deep(.n-input),
.cb-character__field :deep(.n-input-number) {
  width: 100%;
}

.cb-character__switch-row {
  flex-direction: row;
  align-items: center;
  gap: var(--cb-space-2);
}

.cb-character__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-3);
}

.cb-character__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-character__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-character__button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-character__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary);
  color: var(--cb-bg);
}

.cb-character__button--primary:hover:not(:disabled) {
  color: var(--cb-bg);
}

.cb-character__button--danger {
  border-color: var(--cb-danger);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
}

.cb-character__button--danger:hover:not(:disabled) {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}

.cb-character__error {
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
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

/* ---------------------------------------------------------- 导入导出 */

.cb-character__transfer {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.cb-character__transfer-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: var(--cb-space-4);
  align-items: start;
}

.cb-character__transfer-block {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  min-width: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface-raised);
}

.cb-character__transfer-title {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
}

.cb-character__transfer-block input[type='file'] {
  color: var(--cb-text-muted);
  font-size: var(--cb-text-sm);
}

.cb-character__transfer-preview {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
}

.cb-character__transfer-counts {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-character__transfer-counts li {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-3);
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-character__transfer-applied {
  color: var(--cb-success);
  font-size: var(--cb-text-xs);
}
</style>
