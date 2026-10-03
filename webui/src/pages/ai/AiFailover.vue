<script setup lang="ts">
/**
 * 故障转移页（W4 §31-§34、§104、§35/§71/§105）。
 *
 * 顺序保存在 `ai.models` 的顺序里；Model Router 决定怎么失败重试，
 * 这里只配置顺序与启停。重置 Router 只清内存态，绝不能触碰模型配置。
 */
import { onMounted, ref, useId } from 'vue'

import ConfirmDialog from '@/components/ConfirmDialog.vue'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import FailoverList from '@/components/ai/FailoverList.vue'
import { toast } from '@/composables/toast'
import { useAiStore } from '@/stores/ai'

const aiStore = useAiStore()
const order = ref<string[]>([])
const loading = ref(false)
const confirmingReset = ref(false)
const resetting = ref(false)

const uid = useId()
const tip429Id = `cb-failover-429-${uid}`
const tip5xxId = `cb-failover-5xx-${uid}`

async function load(): Promise<void> {
  loading.value = true
  await Promise.all([aiStore.loadModels(), aiStore.loadStatus()])
  order.value = aiStore.models.map((model) => model.name)
  loading.value = false
}

onMounted(load)

/** §104：保存成功后 store 会重新拉取模型，顺序以服务端为准。 */
async function onSave(next: string[]): Promise<void> {
  const ok = await aiStore.reorder(next)
  if (!ok) {
    toast.error(aiStore.error || '保存顺序失败')
    return
  }
  order.value = aiStore.models.map((model) => model.name)
  toast.success('顺序已保存')
}

/** §35/§71/§105：二次确认后清空 Router 内存态（cooldown / 失败计数），不删配置。 */
async function onResetRouter(): Promise<void> {
  resetting.value = true
  const ok = await aiStore.resetRouter()
  resetting.value = false
  if (!ok) {
    toast.error(aiStore.error || '重置失败')
    return
  }
  order.value = aiStore.models.map((model) => model.name)
  toast.success('Router 内存状态已重置')
}
</script>

<template>
  <div class="failover" data-test="ai-failover">
    <PageHeader title="故障转移" subtitle="请求失败时，模型路由器按这个顺序尝试下一个可用模型。" />

    <section class="failover__explain cb-card" data-test="failover-explain">
      <SectionHeader
        title="故障转移由 Model Router 执行；这里只配置顺序与启停"
        description="链首（第 1 项）就是当前聊天模型。已停用或处于冷却中的模型会被跳过，具体重试策略由 Model Router 决定。"
      />
      <ul class="failover__terms">
        <li class="failover__term-line">
          <span class="failover__term" tabindex="0" :aria-describedby="tip429Id" data-test="tip-429">
            429
            <span :id="tip429Id" class="failover__tip" role="tooltip" data-test="tip-429-text">
              请求过多：该模型进入 cooldown，暂时跳过；具体策略由 Model Router 决定。
            </span>
          </span>
          <span class="cb-caption">请求过多 → 进入 cooldown</span>
        </li>
        <li class="failover__term-line">
          <span class="failover__term" tabindex="0" :aria-describedby="tip5xxId" data-test="tip-5xx">
            5xx
            <span :id="tip5xxId" class="failover__tip" role="tooltip" data-test="tip-5xx-text">
              服务商/模型服务端错误；具体策略由 Model Router 决定。
            </span>
          </span>
          <span class="cb-caption">服务端错误</span>
        </li>
      </ul>
    </section>

    <section class="failover__chain">
      <SectionHeader title="故障转移链" description="用上移/下移调整顺序；改动先留在本地，保存后写入配置。" />

      <ErrorState v-if="aiStore.error" :message="aiStore.error" @retry="load" />
      <LoadingState v-if="loading && aiStore.models.length === 0" label="正在读取模型顺序…" :rows="4" />
      <p v-else-if="aiStore.models.length === 0" class="cb-muted" data-test="failover-empty">
        还没有模型。请先在「模型」页添加模型，再回到这里调整顺序。
      </p>
      <FailoverList
        v-else
        v-model:order="order"
        :items="aiStore.models"
        @save="onSave"
      />
    </section>

    <details class="failover__advanced" data-test="failover-advanced">
      <summary class="failover__advanced-summary">高级操作</summary>
      <div class="failover__advanced-body">
        <p class="cb-caption">
          重置只影响运行时内存：清除当前运行时 cooldown / 临时状态；不会删除模型配置。
        </p>
        <button
          type="button"
          class="failover__reset"
          :disabled="resetting"
          data-test="failover-reset"
          @click="confirmingReset = true"
        >
          {{ resetting ? '正在重置…' : '重置 Router 内存状态' }}
        </button>
      </div>
    </details>

    <ConfirmDialog
      v-model:show="confirmingReset"
      title="重置 Router 内存状态"
      message="清除当前运行时 cooldown / 临时状态；不会删除模型配置。"
      confirm-text="重置"
      danger
      @confirm="onResetRouter"
    />
  </div>
</template>

<style scoped>
.failover {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
}

.failover__explain,
.failover__chain {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.failover__terms {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-4);
  margin: 0;
  padding: 0;
  list-style: none;
}

.failover__term-line {
  display: flex;
  align-items: center;
  gap: var(--cb-space-2);
}

.failover__term {
  position: relative;
  display: inline-flex;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  background: var(--cb-bg-soft);
  color: var(--cb-text);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-xs);
  cursor: help;
}

.failover__tip {
  position: absolute;
  bottom: calc(100% + var(--cb-space-2));
  left: 0;
  z-index: 40;
  width: max-content;
  max-width: 320px;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  box-shadow: var(--cb-shadow-md);
  font-family: var(--cb-font-sans);
  font-size: var(--cb-text-xs);
  line-height: var(--cb-line);
  white-space: normal;
  opacity: 0;
  visibility: hidden;
}

.failover__term:hover .failover__tip,
.failover__term:focus .failover__tip,
.failover__term:focus-within .failover__tip {
  opacity: 1;
  visibility: visible;
}

.failover__advanced {
  padding: var(--cb-space-3) var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-bg-soft);
}

.failover__advanced-summary {
  cursor: pointer;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.failover__advanced-body {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--cb-space-3);
  padding-top: var(--cb-space-3);
  flex-wrap: wrap;
}

.failover__reset {
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-danger);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-danger-soft);
  color: var(--cb-danger);
  font-family: inherit;
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.failover__reset:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
