<script setup lang="ts">
/**
 * 人物详情（W5 §19、§21、§107）：五个区块（身份 / 关系 / 开放承诺 /
 * 最近共享经历 / 相关记忆）+ 社交空间列表。
 *
 * 可编辑字段只有后端允许的四个（nickname_override / notes / tags /
 * initiative_enabled），保存只提交改动字段；其余全部只读。
 */
import { computed, reactive, ref, watch } from 'vue'
import { NInput } from 'naive-ui'
import { RouterLink, useRoute } from 'vue-router'

import { errorMessage } from '@/api/client'
import { socialApi } from '@/api/social'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import CommitmentCard from '@/components/domain/CommitmentCard.vue'
import RelationshipCard from '@/components/domain/RelationshipCard.vue'
import { toast } from '@/composables/toast'
import { useSocialStore } from '@/stores/social'
import type { SocialSessionRow, SocialUserDetail } from '@/types/social'

const route = useRoute()
const store = useSocialStore()

const personId = computed(() => String(route.params.personId ?? ''))

const detail = computed<SocialUserDetail | null>(() => store.detail)
const person = computed(() => detail.value?.person ?? null)

const notFound = computed(
  () => !store.loading && detail.value === null && /不存在|user_not_found/.test(store.error),
)
const failed = computed(
  () => !store.loading && detail.value === null && store.error !== '' && !notFound.value,
)

async function load(id: string): Promise<void> {
  if (!id) return
  await store.loadUser(id)
}

watch(
  () => route.params.personId,
  (value) => {
    void load(String(value ?? ''))
  },
  { immediate: true },
)

// 空间名只是展示辅助：加载失败也不阻塞详情页。
void store.loadSpaces()

// ------------------------------------------------------------ 会话上下文
/**
 * 本页没有会话列表，因此按任务允许的降级路径提供 session_id 输入：
 * 有活动会话时优先推导 `group:<群号>`（经空间映射）或 `private:<QQ>`。
 * 读取/清空都走 socialApi，错误只落在本区块，不污染详情页的 store.error。
 */
const sessionRows = ref<SocialSessionRow[]>([])
const sessionLoading = ref(false)
const sessionLoadError = ref('')
const sessionId = ref('')
const sessionTouched = ref(false)

const personSession = computed(
  () => sessionRows.value.find((row) => row.person_id === person.value?.person_id) ?? null,
)

function deriveSessionId(): string {
  const active = personSession.value
  if (active) {
    const group = Object.entries(store.spaceMap).find(([, spaceId]) => spaceId === active.social_space_id)
    if (group) return `group:${group[0]}`
  }
  const qq = person.value?.qq
  return qq ? `private:${qq}` : ''
}

function syncSessionId(): void {
  if (!sessionTouched.value) sessionId.value = deriveSessionId()
}

async function loadSessions(): Promise<void> {
  sessionLoading.value = true
  sessionLoadError.value = ''
  try {
    const data = await socialApi.sessions()
    sessionRows.value = data.items
    syncSessionId()
  } catch (caught) {
    sessionRows.value = []
    sessionLoadError.value = errorMessage(caught)
  } finally {
    sessionLoading.value = false
  }
}

void loadSessions()

watch(person, () => syncSessionId())

function onSessionIdInput(): void {
  sessionTouched.value = true
}

const pendingClear = ref(false)
const clearing = ref(false)
const clearError = ref('')

function askClear(): void {
  clearError.value = ''
  if (!sessionId.value.trim()) {
    toast.warning('请先填写要清空的 session_id')
    return
  }
  pendingClear.value = true
}

async function confirmClear(): Promise<void> {
  const id = sessionId.value.trim()
  pendingClear.value = false
  if (!id || clearing.value) return
  clearing.value = true
  clearError.value = ''
  try {
    const result = await socialApi.clearSession(id)
    toast.success('已清空会话上下文', result.session_id)
    await loadSessions()
  } catch (caught) {
    // 409 session.confirm_required / 后端 message 原样展示，不改写。
    clearError.value = errorMessage(caught)
    toast.error('清空失败', clearError.value)
  } finally {
    clearing.value = false
  }
}

const sessionStatusText = computed(() => {
  if (sessionLoading.value) return '正在读取会话状态…'
  if (sessionLoadError.value) return `会话状态读取失败：${sessionLoadError.value}`
  if (personSession.value) {
    const space = personSession.value.social_space_id || '未知空间'
    return `当前有活动会话（${space} · ${personSession.value.turns} 回合）`
  }
  return '当前没有活动会话；可手动填写要清空的 session_id。'
})

// ------------------------------------------------------------------ 可编辑区
interface UserPayload {
  nickname_override?: string
  notes?: string
  tags?: string[]
  initiative_enabled?: boolean
}

const OPEN_STATUSES = new Set([
  'pending',
  'scheduled',
  'active',
  'in_progress',
  'rescheduled',
  'proposed',
  'accepted',
])

const form = reactive({
  nickname_override: '',
  notes: '',
  tagsText: '',
  initiative_enabled: true,
})

const saving = ref(false)
const saveError = ref('')

watch(
  () => store.detail,
  (value) => {
    if (!value) return
    form.nickname_override = value.person.nickname_override || ''
    form.notes = value.person.notes || ''
    form.tagsText = (value.person.tags ?? []).join('、')
    form.initiative_enabled = Boolean(value.person.initiative_enabled)
    saveError.value = ''
  },
  { immediate: true },
)

function parsedTags(): string[] {
  return form.tagsText
    .split(/[,，、]/)
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0)
}

function buildPayload(): UserPayload {
  const current = person.value
  if (!current) return {}
  const payload: UserPayload = {}
  const nickname = form.nickname_override.trim()
  if (nickname !== (current.nickname_override || '')) payload.nickname_override = nickname
  if (form.notes !== (current.notes || '')) payload.notes = form.notes
  const tags = parsedTags()
  if (tags.join('\u0000') !== (current.tags ?? []).join('\u0000')) payload.tags = tags
  if (form.initiative_enabled !== Boolean(current.initiative_enabled)) {
    payload.initiative_enabled = form.initiative_enabled
  }
  return payload
}

const dirty = computed(() => Object.keys(buildPayload()).length > 0)

async function save(): Promise<void> {
  if (saving.value) return
  const payload = buildPayload()
  if (Object.keys(payload).length === 0) {
    toast.info('没有需要保存的修改')
    return
  }
  saving.value = true
  saveError.value = ''
  const ok = await store.saveUser(personId.value, payload)
  saving.value = false
  if (!ok) {
    // store.error 就是后端 message（§13），原样展示，不改写。
    saveError.value = store.error
    return
  }
  toast.success('已保存', '人物资料已更新')
}

// ------------------------------------------------------------------- 投影
const openCommitments = computed(() =>
  (detail.value?.commitments ?? []).filter((item) => OPEN_STATUSES.has(item.status)),
)

const experiences = computed(() => detail.value?.experiences ?? [])
const memoriesAvailable = computed(() => Boolean(detail.value) && detail.value?.memories !== null)
const memoryItems = computed(() => detail.value?.memories ?? [])

const lastInteraction = computed(
  () => person.value?.last_seen ?? detail.value?.relationship?.last_interaction_at ?? null,
)

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  const seconds = value > 1e11 ? value / 1000 : value
  return new Date(seconds * 1000).toLocaleString('zh-CN', { hour12: false })
}

function rowText(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = row[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return ''
}

function rowTime(row: Record<string, unknown>, key: string): string {
  const value = row[key]
  const numeric = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return '—'
  return formatTime(numeric)
}

function rowActors(row: Record<string, unknown>): string {
  const value = row['actors']
  if (!Array.isArray(value) || value.length === 0) return ''
  return value.map((item) => String(item)).join('、')
}

function spaceName(spaceId: string): string {
  const found = store.spaces.find((space) => space.space_id === spaceId)
  return found ? found.name || found.space_id : '未知空间'
}
</script>

<template>
  <div class="cb-social-detail" data-test="social-user-detail">
    <ErrorState v-if="notFound" message="找不到这个人物" :detail="store.error" />
    <RouterLink
      v-if="notFound"
      class="cb-social-detail__back"
      data-test="user-back"
      to="/social"
    >
      返回用户列表
    </RouterLink>

    <ErrorState v-else-if="failed" :message="store.error" @retry="load(personId)" />

    <LoadingState v-else-if="store.loading && !detail" label="正在读取人物详情…" :rows="5" />

    <template v-else-if="detail && person">
      <SectionHeader title="身份" description="显示名与关系来自后端；内部标识只在 Expert 区展示。">
        <template #actions>
          <RouterLink class="cb-social-detail__back" to="/social">返回列表</RouterLink>
        </template>
      </SectionHeader>

      <dl class="cb-social-detail__facts">
        <div>
          <dt>显示名</dt>
          <dd data-test="detail-display-name">{{ person.display_name }}</dd>
        </div>
        <div>
          <dt>QQ</dt>
          <dd data-test="detail-qq">{{ person.qq || '—' }}</dd>
        </div>
        <div>
          <dt>昵称</dt>
          <dd data-test="detail-nickname">{{ person.nickname || '—' }}</dd>
        </div>
        <div>
          <dt>互动次数</dt>
          <dd>{{ person.interaction_count }}</dd>
        </div>
        <div>
          <dt>最近互动</dt>
          <dd data-test="detail-last-seen">{{ formatTime(lastInteraction) }}</dd>
        </div>
        <div>
          <dt>标签</dt>
          <dd data-test="detail-tags">{{ person.tags.length > 0 ? person.tags.join('、') : '—' }}</dd>
        </div>
        <div class="cb-social-detail__fact--wide">
          <dt>备注</dt>
          <dd data-test="detail-notes">{{ person.notes || '—' }}</dd>
        </div>
      </dl>

      <details class="cb-social-detail__expert">
        <summary class="cb-caption">Expert 标识</summary>
        <p class="cb-social-detail__id" data-test="detail-person-id">
          person_id: {{ person.person_id }}
        </p>
      </details>

      <form class="cb-social-detail__edit" data-test="user-edit-form" @submit.prevent="save">
        <p class="cb-social-detail__edit-title">可编辑资料</p>
        <div class="cb-social-detail__edit-grid">
          <label class="cb-social-detail__field">
            <span class="cb-caption">覆盖昵称</span>
            <input v-model="form.nickname_override" data-test="edit-nickname" type="text" />
          </label>
          <label class="cb-social-detail__field">
            <span class="cb-caption">标签（用「、」或逗号分隔）</span>
            <input v-model="form.tagsText" data-test="edit-tags" type="text" />
          </label>
          <label class="cb-social-detail__field cb-social-detail__field--wide">
            <span class="cb-caption">备注</span>
            <textarea v-model="form.notes" data-test="edit-notes" rows="3" />
          </label>
          <label class="cb-social-detail__checkbox">
            <input v-model="form.initiative_enabled" data-test="edit-initiative" type="checkbox" />
            <span>允许主动发起对话</span>
          </label>
        </div>
        <div class="cb-social-detail__edit-actions">
          <button type="submit" data-test="user-save" :disabled="saving || !dirty">
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <span v-if="!dirty" class="cb-caption">没有未保存的修改</span>
        </div>
        <p v-if="saveError" class="cb-social-detail__error" role="alert" data-test="user-save-error">
          {{ saveError }}
        </p>
      </form>

      <section class="cb-social-detail__block" data-test="block-session">
        <SectionHeader
          title="会话上下文"
          description="清空后她会忘掉这段对话的短期上下文；记忆与关系不受影响。"
        />
        <div class="cb-social-detail__session">
          <label class="cb-social-detail__field cb-social-detail__field--wide">
            <span class="cb-caption">session_id（私聊为 private:QQ，群聊为 group:群号）</span>
            <NInput
              v-model:value="sessionId"
              data-test="session-id"
              placeholder="private:10001"
              @update:value="onSessionIdInput"
            />
          </label>
          <p class="cb-caption" data-test="session-status">{{ sessionStatusText }}</p>
          <div class="cb-social-detail__edit-actions">
            <button
              type="button"
              class="cb-social-detail__danger"
              data-test="session-clear"
              :disabled="clearing || !sessionId.trim()"
              @click="askClear"
            >
              {{ clearing ? '清空中…' : '清空会话上下文' }}
            </button>
            <span class="cb-caption">清空前会再次确认。</span>
          </div>
          <p
            v-if="clearError"
            class="cb-social-detail__error"
            role="alert"
            data-test="session-clear-error"
          >
            {{ clearError }}
          </p>
        </div>
      </section>

      <ConfirmDialog
        :show="pendingClear"
        title="清空会话上下文"
        :message="`确定清空会话「${sessionId.trim()}」的上下文吗？`"
        detail="清空不可撤销：她会忘掉这段对话的短期上下文（记忆与关系不受影响）。"
        confirm-text="清空"
        danger
        @confirm="confirmClear"
        @cancel="pendingClear = false"
      />

      <section class="cb-social-detail__block" data-test="block-relationship">
        <SectionHeader
          title="关系"
          description="只读：关系状态由互动历史累积，WebUI 不提供修改（§22）。"
        />
        <RelationshipCard :relationship="detail.relationship" />
      </section>

      <section class="cb-social-detail__block" data-test="block-commitments">
        <SectionHeader title="开放承诺" description="仍然欠着对方、尚未产生结果的承诺。" />
        <div v-if="openCommitments.length > 0" class="cb-social-detail__cards">
          <CommitmentCard
            v-for="item in openCommitments"
            :key="item.commitment_id"
            :commitment="item"
          />
        </div>
        <EmptyState v-else title="没有开放承诺" description="当前没有需要追踪的承诺。" />
      </section>

      <section class="cb-social-detail__block" data-test="block-experiences">
        <SectionHeader title="最近共享经历" description="来自沙盒经历表的最新记录（最多 20 条）。" />
        <ul v-if="experiences.length > 0" class="cb-social-detail__list">
          <li v-for="(row, index) in experiences" :key="index" data-test="experience-item">
            <p class="cb-social-detail__list-title">
              {{ rowText(row, ['summary', 'kind']) || '（无摘要）' }}
            </p>
            <p class="cb-caption">
              {{ rowTime(row, 'created_at') }}
              <template v-if="rowActors(row)"> · 参与者：{{ rowActors(row) }}</template>
            </p>
          </li>
        </ul>
        <EmptyState v-else title="还没有共享经历" description="共同经历过的事件会记录在这里。" />
      </section>

      <section class="cb-social-detail__block" data-test="block-memories">
        <SectionHeader title="相关记忆" description="只读投影；记忆未启用时后端返回 null。" />
        <EmptyState
          v-if="!memoriesAvailable"
          title="记忆未启用"
          description="当前 Runtime 没有可用的记忆模块，因此无法列出相关记忆。"
        />
        <ul v-else-if="memoryItems.length > 0" class="cb-social-detail__list">
          <li v-for="(row, index) in memoryItems" :key="index" data-test="memory-item">
            <p class="cb-social-detail__list-title">
              {{ rowText(row, ['summary', 'content']) || '（无内容）' }}
            </p>
            <p class="cb-caption">
              {{ rowText(row, ['category', 'layer']) || '记忆' }} · {{ rowTime(row, 'created_at') }}
            </p>
          </li>
        </ul>
        <EmptyState v-else title="没有相关记忆" description="记忆模块已启用，但没有命中这个人的记录。" />
      </section>

      <section class="cb-social-detail__block" data-test="block-spaces">
        <SectionHeader
          title="社交空间"
          description="人物与社交空间是两个独立维度：空间是场景，不是关系对象（§21）。"
        />
        <ul v-if="detail.spaces.length > 0" class="cb-social-detail__list">
          <li v-for="spaceId in detail.spaces" :key="spaceId" data-test="detail-space">
            <p class="cb-social-detail__list-title">{{ spaceName(spaceId) }}</p>
            <p class="cb-caption cb-social-detail__id">space_id: {{ spaceId }}</p>
          </li>
        </ul>
        <EmptyState v-else title="没有参与中的社交空间" description="这个人物当前不在任何已配置的社交空间里。" />
      </section>
    </template>
  </div>
</template>

<style scoped>
.cb-social-detail {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-detail__back {
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  text-decoration: none;
  align-self: flex-start;
}

.cb-social-detail__back:hover {
  text-decoration: underline;
}

.cb-social-detail__facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: var(--cb-space-3);
  margin: 0;
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-detail__fact--wide {
  grid-column: 1 / -1;
}

.cb-social-detail__facts dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-social-detail__facts dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-social-detail__expert summary {
  cursor: pointer;
}

.cb-social-detail__id {
  color: var(--cb-text-muted);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  overflow-wrap: anywhere;
}

.cb-social-detail__edit {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-detail__edit-title {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-social-detail__edit-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--cb-space-3);
}

.cb-social-detail__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
}

.cb-social-detail__field--wide {
  grid-column: 1 / -1;
}

.cb-social-detail__edit input[type='text'],
.cb-social-detail__edit textarea {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  resize: vertical;
}

.cb-social-detail__edit input[type='text']:focus,
.cb-social-detail__edit textarea:focus {
  outline: none;
  border-color: var(--cb-primary);
  box-shadow: var(--cb-focus);
}

.cb-social-detail__checkbox {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-social-detail__edit-actions {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
}

.cb-social-detail__edit-actions button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-primary);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-primary);
  color: var(--cb-bg);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-social-detail__edit-actions button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-social-detail__error {
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
}

.cb-social-detail__session {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-detail__session :deep(.n-input) {
  width: 100%;
}

.cb-social-detail__danger {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.cb-social-detail__danger:hover:not(:disabled) {
  border-color: var(--cb-danger);
  color: var(--cb-danger);
}

.cb-social-detail__danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cb-social-detail__block {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.cb-social-detail__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--cb-space-3);
}

.cb-social-detail__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-social-detail__list li {
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-detail__list-title {
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-social-detail__list .cb-caption {
  margin-top: var(--cb-space-1);
}
</style>
