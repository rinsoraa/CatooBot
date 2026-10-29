<script setup lang="ts">
/**
 * 人物卡（W5 §18-§19、§107）：主展示永远是后端给的显示名与关系，
 * `person_id` 是解析后的稳定标识，只放进 Expert 折叠区（§145）。
 */
import { computed } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { relationLabel, type SocialUser } from '@/types/social'

const props = defineProps<{ user: SocialUser }>()

const router = useRouter()

const relationText = computed(() =>
  props.user.relationship ? relationLabel(props.user.relationship.relation_type) : '尚无关系记录',
)

const lastInteraction = computed(
  () => props.user.last_seen ?? props.user.relationship?.last_interaction_at ?? null,
)

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

function open(event: MouseEvent): void {
  const target = event.target as HTMLElement | null
  if (target?.closest('details')) return
  void router.push(`/social/users/${encodeURIComponent(props.user.person_id)}`)
}
</script>

<template>
  <article
    class="cb-person-card"
    data-test="person-card"
    :data-person="user.person_id"
    @click="open"
  >
    <header class="cb-person-card__header">
      <RouterLink
        class="cb-person-card__name"
        data-test="person-name"
        :to="`/social/users/${encodeURIComponent(user.person_id)}`"
      >
        {{ user.display_name }}
      </RouterLink>
      <span class="cb-person-card__relation" data-test="person-relation">{{ relationText }}</span>
    </header>

    <dl class="cb-person-card__facts">
      <div class="cb-person-card__fact">
        <dt>QQ</dt>
        <dd data-test="person-qq">{{ user.qq || '—' }}</dd>
      </div>
      <div class="cb-person-card__fact">
        <dt>互动次数</dt>
        <dd data-test="person-interactions">{{ user.interaction_count }}</dd>
      </div>
      <div class="cb-person-card__fact">
        <dt>开放承诺</dt>
        <dd data-test="person-open-commitments">{{ user.open_commitments }}</dd>
      </div>
      <div class="cb-person-card__fact">
        <dt>最近互动</dt>
        <dd data-test="person-last-seen">{{ formatTime(lastInteraction) }}</dd>
      </div>
      <div class="cb-person-card__fact cb-person-card__fact--wide">
        <dt>最近共享经历</dt>
        <dd data-test="person-experience">{{ user.recent_experience || '—' }}</dd>
      </div>
    </dl>

    <details class="cb-person-card__expert">
      <summary class="cb-caption">Expert 标识</summary>
      <p class="cb-person-card__id" data-test="person-expert-id">
        person_id: {{ user.person_id }}
      </p>
    </details>
  </article>
</template>

<style scoped>
.cb-person-card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
  cursor: pointer;
}

.cb-person-card:hover {
  border-color: var(--cb-primary);
}

.cb-person-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--cb-space-2);
  min-width: 0;
}

.cb-person-card__name {
  color: var(--cb-text);
  font-size: var(--cb-text-md);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cb-person-card__name:hover {
  color: var(--cb-primary-strong);
}

.cb-person-card__relation {
  flex: none;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-person-card__facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--cb-space-2) var(--cb-space-3);
  margin: 0;
}

.cb-person-card__fact {
  min-width: 0;
}

.cb-person-card__fact--wide {
  grid-column: 1 / -1;
}

.cb-person-card__fact dt {
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-person-card__fact dd {
  margin: var(--cb-space-1) 0 0;
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.cb-person-card__expert summary {
  cursor: pointer;
}

.cb-person-card__id {
  margin-top: var(--cb-space-1);
  color: var(--cb-text-muted);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  overflow-wrap: anywhere;
}
</style>
