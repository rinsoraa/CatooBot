<script setup lang="ts">
/**
 * 模型用途页（W4 §27-§30、§103）：九张角色卡 + 「默认聊天模型 = 故障转移链第一项」说明。
 *
 * 切换角色走 `aiStore.setRole`，成功后按后端返回的 restart_required 给出
 * 「已保存 / 已保存，重启后生效」两种提示；页面不自己猜是否需要重启。
 */
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import RoleCard from '@/components/ai/RoleCard.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'
import type { RoleItem } from '@/types/ai'

const aiStore = useAiStore()
const loading = ref(false)
const busyRole = ref('')
/** 本会话内刚刚确认生效的角色（显示「● 已生效」）。 */
const appliedRole = ref('')

async function load(): Promise<void> {
  loading.value = true
  await Promise.all([aiStore.loadRoles(), aiStore.loadModels()])
  loading.value = false
}

onMounted(load)

async function onRoleChange(item: RoleItem, model: string): Promise<void> {
  if (model === item.model) return
  busyRole.value = item.role
  const result = await aiStore.setRole(item.role, model)
  busyRole.value = ''
  if (!result) {
    toast.error(aiStore.error || '保存失败，请重试')
    return
  }
  appliedRole.value = item.role
  if (result.restart_required) {
    toast.success('已保存，重启后生效')
    return
  }
  toast.success('已保存')
}
</script>

<template>
  <div class="roles" data-test="ai-roles">
    <PageHeader title="模型用途" subtitle="每个角色分别使用哪个模型；未配置的角色会明确标出。" />

    <section class="roles__intro cb-card" data-test="roles-intro">
      <p class="roles__intro-text">
        <RouterLink
          class="roles__chain-link"
          :to="{ name: 'ai-failover' }"
          data-test="roles-chain-link"
        >
          默认聊天模型 = 故障转移链的第一个模型
        </RouterLink>
        。聊天回复始终从链首开始尝试；要更换默认聊天模型，请
        <RouterLink :to="{ name: 'ai-failover' }" data-test="roles-order-link">调整顺序</RouterLink>。
      </p>
      <p class="cb-caption">
        其余角色各自绑定独立模型；标注「已保存 · 重启后生效」的角色，需要重启 CatooBot 后才会使用新绑定。
      </p>
    </section>

    <ErrorState
      v-if="aiStore.error && aiStore.roles.length === 0"
      :message="aiStore.error"
      @retry="load"
    />
    <LoadingState v-else-if="loading && aiStore.roles.length === 0" label="正在读取角色绑定…" :rows="4" />
    <p v-else-if="aiStore.roles.length === 0" class="cb-muted" data-test="roles-empty">
      暂无角色数据。
    </p>

    <div v-else class="roles__grid" data-test="roles-grid">
      <div
        v-for="item in aiStore.roles"
        :key="item.role"
        class="roles__cell"
        :data-role="item.role"
      >
        <RoleCard
          :item="item"
          :models="aiStore.models"
          :busy="busyRole === item.role"
          @change="(model) => onRoleChange(item, model)"
        />
        <p
          v-if="appliedRole === item.role"
          class="roles__applied"
          role="status"
          data-test="roles-applied"
        >
          ● 已生效
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.roles {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.roles__intro {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.roles__intro-text {
  font-size: var(--cb-text-sm);
  color: var(--cb-text);
}

.roles__chain-link {
  font-weight: 600;
}

.roles__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--cb-space-3);
}

.roles__cell {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.roles__cell > :first-child {
  flex: 1;
}

.roles__applied {
  font-size: var(--cb-text-xs);
  color: var(--cb-success);
}
</style>
