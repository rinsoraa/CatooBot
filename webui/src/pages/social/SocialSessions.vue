<script setup lang="ts">
/**
 * 会话页（W5 §25）：只展示运行期会话状态（谁、在哪个空间、起止、回合数、是否被打断），
 * 不包含任何聊天正文。
 */
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { useSocialStore } from '@/stores/social'
import type { SocialSessionRow } from '@/types/social'

const store = useSocialStore()

const loading = ref(false)

async function load(): Promise<void> {
  loading.value = true
  await store.loadSessions()
  loading.value = false
}

onMounted(() => {
  void load()
})

const sessions = computed<SocialSessionRow[]>(() => store.sessions)
const hasActive = computed(() => store.sessionActive && sessions.value.length > 0)

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="cb-social-sessions" data-test="social-sessions">
    <SectionHeader title="会话" description="运行期社交会话的快照。" />

    <p class="cb-social-sessions__statement" data-test="sessions-statement">
      这里只显示运行期会话状态，不包含聊天正文。会话内容不会出现在 WebUI。
    </p>

    <ErrorState v-if="store.error" :message="store.error" @retry="load()" />

    <LoadingState v-else-if="loading && !hasActive" label="正在读取会话状态…" :rows="3" />

    <EmptyState
      v-else-if="!hasActive"
      title="当前没有活动会话"
      description="机器人与用户或群组交互时，这里会出现运行期会话状态。"
    />

    <div v-else class="cb-social-sessions__list">
      <article
        v-for="session in sessions"
        :key="`${session.person_id}/${session.social_space_id}`"
        class="cb-social-sessions__card"
        data-test="session-card"
        :data-person="session.person_id"
      >
        <header class="cb-social-sessions__header">
          <span class="cb-social-sessions__person" data-test="session-person">
            {{ session.person_name || session.person_id }}
          </span>
          <span
            class="cb-social-sessions__interrupted"
            :class="{ 'cb-social-sessions__interrupted--on': session.interrupted }"
            data-test="session-interrupted"
          >
            {{ session.interrupted ? '已打断' : '进行中' }}
          </span>
        </header>
        <dl class="cb-social-sessions__facts">
          <div>
            <dt>社交空间</dt>
            <dd data-test="session-space">{{ session.social_space_id || '—' }}</dd>
          </div>
          <div>
            <dt>开始时间</dt>
            <dd data-test="session-started">{{ formatTime(session.started_at) }}</dd>
          </div>
          <div>
            <dt>最近活动</dt>
            <dd data-test="session-last-activity">{{ formatTime(session.last_activity_at) }}</dd>
          </div>
          <div>
            <dt>回合数</dt>
            <dd data-test="session-turns">{{ session.turns }}</dd>
          </div>
        </dl>
        <RouterLink
          class="cb-social-sessions__link"
          :to="`/social/users/${encodeURIComponent(session.person_id)}`"
        >
          查看人物详情
        </RouterLink>
      </article>
    </div>
  </div>
</template>

<style scoped>
.cb-social-sessions {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-sessions__statement {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-info);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-info-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-social-sessions__list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: var(--cb-space-3);
}

.cb-social-sessions__card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-sessions__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-2);
}

.cb-social-sessions__person {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow-wrap: anywhere;
}

.cb-social-sessions__interrupted {
  flex: none;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-success);
  border-radius: 999px;
  background: var(--cb-success-soft);
  color: var(--cb-success);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-social-sessions__interrupted--on {
  border-color: var(--cb-warning);
  background: var(--cb-warning-soft);
  color: var(--cb-warning);
}

.cb-social-sessions__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-social-sessions__facts dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-social-sessions__facts dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-social-sessions__link {
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  text-decoration: none;
  align-self: flex-start;
}

.cb-social-sessions__link:hover {
  text-decoration: underline;
}
</style>
