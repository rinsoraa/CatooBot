<script setup lang="ts">
/**
 * 群组页（W5 §20）：只读列表 + 参与开关（服务端确认后整页刷新，不做乐观更新）。
 * 群不是人物：群号只用于空间映射，关系永远挂在 person 上。
 */
import { computed, onMounted, ref } from 'vue'

import EmptyState from '@/components/EmptyState.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import { useSocialStore } from '@/stores/social'
import type { GroupRow } from '@/types/social'

const store = useSocialStore()

const loading = ref(false)
const toggling = ref('')

async function load(): Promise<void> {
  loading.value = true
  await store.loadGroups()
  loading.value = false
}

onMounted(() => {
  void load()
})

const groups = computed<GroupRow[]>(() => store.groups)

function formatTime(value: number | null): string {
  if (value === null || value === undefined || value <= 0) return '—'
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false })
}

async function toggle(group: GroupRow): Promise<void> {
  if (toggling.value) return
  const next = !group.participation_enabled
  toggling.value = group.group_id
  const ok = await store.setGroupParticipation(group.group_id, next)
  toggling.value = ''
  if (!ok) {
    toast.error('切换失败', store.error)
    return
  }
  const name = group.name || group.group_id
  toast.success(next ? `已开启「${name}」的参与` : `已关闭「${name}」的参与`)
}
</script>

<template>
  <div class="cb-social-groups" data-test="social-groups">
    <SectionHeader title="群组" description="群参与开关直接影响机器人是否在该群响应。" />

    <p class="cb-social-groups__warning" data-test="group-warning">
      群 ≠ 人物：群号不是关系对象，关系永远挂在具体的 person 上。
    </p>

    <ErrorState v-if="store.error" :message="store.error" @retry="load()" />

    <LoadingState v-else-if="loading && groups.length === 0" label="正在读取群组…" :rows="3" />

    <EmptyState
      v-else-if="groups.length === 0"
      title="还没有配置的群"
      description="机器人加入过的 QQ 群会出现在这里。"
    />

    <div v-else class="cb-social-groups__table-wrap">
      <table class="cb-social-groups__table">
        <caption class="cb-visually-hidden">群组列表与参与状态</caption>
        <thead>
          <tr>
            <th scope="col">群号</th>
            <th scope="col">名称</th>
            <th scope="col">最近活动</th>
            <th scope="col">互动次数</th>
            <th scope="col">参与</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="group in groups" :key="group.group_id" data-test="group-row" :data-group="group.group_id">
            <td><code class="cb-social-groups__id">{{ group.group_id }}</code></td>
            <td>{{ group.name || '—' }}</td>
            <td data-test="group-last-seen">{{ formatTime(group.last_seen) }}</td>
            <td data-test="group-interactions">{{ group.interaction_count }}</td>
            <td>
              <button
                type="button"
                class="cb-social-groups__switch"
                :class="{ 'cb-social-groups__switch--on': group.participation_enabled }"
                role="switch"
                :aria-checked="group.participation_enabled"
                data-test="group-toggle"
                :disabled="toggling === group.group_id"
                @click="toggle(group)"
              >
                {{ toggling === group.group_id ? '切换中…' : group.participation_enabled ? '已参与' : '未参与' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.cb-social-groups {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.cb-social-groups__warning {
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-warning);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-warning-soft);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-social-groups__table-wrap {
  overflow-x: auto;
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.cb-social-groups__table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--cb-text-sm);
}

.cb-social-groups__table th,
.cb-social-groups__table td {
  padding: var(--cb-space-2) var(--cb-space-3);
  border-bottom: 1px solid var(--cb-border);
  text-align: left;
  vertical-align: middle;
}

.cb-social-groups__table th {
  color: var(--cb-text-muted);
  font-weight: 500;
  font-size: var(--cb-text-xs);
  white-space: nowrap;
}

.cb-social-groups__id {
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
}

.cb-social-groups__switch {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-surface);
  color: var(--cb-text-muted);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-social-groups__switch--on {
  border-color: var(--cb-success);
  background: var(--cb-success-soft);
  color: var(--cb-success);
}

.cb-social-groups__switch:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
