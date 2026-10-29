<script setup lang="ts">
/**
 * 提示词页（v0.8 `/prompts` 迁移，归入 AI 区）：编辑记忆提取提示，
 * 并给出人设 System Prompt 的去处。
 *
 * 只提交改动的键；后端空对象会 400 `prompts.empty`，所以保存前必须 dirty。
 */
import { computed, onMounted, ref } from 'vue'
import { NInput } from 'naive-ui'
import { RouterLink } from 'vue-router'

import { errorMessage } from '@/api/client'
import { promptsApi } from '@/api/prompts'
import ErrorState from '@/components/ErrorState.vue'
import LoadingState from '@/components/LoadingState.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionHeader from '@/components/SectionHeader.vue'
import { toast } from '@/composables/toast'
import type { PromptPair } from '@/types/prompts'

const loading = ref(false)
const error = ref('')
const prompts = ref<PromptPair | null>(null)
const memoryPrompt = ref('')
const saving = ref(false)
const saveError = ref('')

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    prompts.value = await promptsApi.get()
    syncDraft()
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
})

function syncDraft(): void {
  memoryPrompt.value = prompts.value?.memory_extraction_prompt ?? ''
  saveError.value = ''
}

const dirty = computed(
  () => prompts.value !== null && memoryPrompt.value !== prompts.value.memory_extraction_prompt,
)

async function save(): Promise<void> {
  if (saving.value) return
  if (!dirty.value) {
    toast.info('没有需要保存的修改')
    return
  }
  saving.value = true
  saveError.value = ''
  try {
    const updated = await promptsApi.save({ memory_extraction_prompt: memoryPrompt.value })
    prompts.value = updated
    syncDraft()
    toast.success('提示词已保存', '记忆提取提示已更新')
  } catch (caught) {
    // 后端 message 原样展示，不改写。
    saveError.value = errorMessage(caught)
    toast.error('保存失败', saveError.value)
  } finally {
    saving.value = false
  }
}

function cancel(): void {
  if (!dirty.value) return
  syncDraft()
  toast.info('已放弃未保存的修改')
}
</script>

<template>
  <div class="prompts" data-test="ai-prompts">
    <PageHeader
      title="提示词"
      subtitle="记忆提取提示可以在这里覆盖；人设 System Prompt 在角色页编辑。"
    />

    <ErrorState v-if="error" :message="error" @retry="load" />

    <LoadingState v-else-if="loading && prompts === null" label="正在读取提示词…" :rows="4" />

    <template v-else-if="prompts">
      <section class="prompts__card cb-card" data-test="prompts-memory">
        <SectionHeader
          title="记忆提取提示"
          description="覆盖后台从对话里提取长期记忆时使用的提示；留空表示不使用自定义覆盖。"
        />
        <form class="prompts__form" data-test="prompts-form" @submit.prevent="save">
          <label class="prompts__field">
            <span class="cb-caption">memory_extraction_prompt</span>
            <NInput
              v-model:value="memoryPrompt"
              type="textarea"
              :rows="10"
              placeholder="留空 = 使用内置提取提示"
              data-test="prompts-memory-input"
            />
          </label>
          <div class="prompts__actions">
            <button
              type="submit"
              class="prompts__button prompts__button--primary"
              data-test="prompts-save"
              :disabled="saving || !dirty"
            >
              {{ saving ? '保存中…' : '保存提示词' }}
            </button>
            <button
              type="button"
              class="prompts__button"
              data-test="prompts-cancel"
              :disabled="saving || !dirty"
              @click="cancel"
            >
              放弃修改
            </button>
            <span class="cb-caption">{{ dirty ? '有未保存的修改' : '与服务端一致' }}</span>
          </div>
          <p v-if="saveError" class="prompts__error" role="alert" data-test="prompts-save-error">
            {{ saveError }}
          </p>
        </form>
      </section>

      <section class="prompts__card cb-card" data-test="prompts-persona">
        <SectionHeader
          title="人设 System Prompt"
          description="这段提示由角色页的人设编辑器维护，本页只读。"
        >
          <template #actions>
            <RouterLink class="prompts__link" to="/character" data-test="prompts-persona-link">
              去角色页编辑
            </RouterLink>
          </template>
        </SectionHeader>
        <pre class="prompts__value" data-test="prompts-persona-value">{{
          prompts.persona_system_prompt || '（空）'
        }}</pre>
      </section>
    </template>
  </div>
</template>

<style scoped>
.prompts {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-4);
  min-width: 0;
}

.prompts__card {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
  padding: var(--cb-space-4);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
}

.prompts__form {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-3);
}

.prompts__field {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  min-width: 0;
}

.prompts__field :deep(.n-input) {
  width: 100%;
}

.prompts__actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--cb-space-3);
}

.prompts__button {
  padding: var(--cb-space-2) var(--cb-space-4);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface);
  color: var(--cb-text);
  font-family: inherit;
  font-size: var(--cb-text-sm);
  cursor: pointer;
}

.prompts__button:hover:not(:disabled) {
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.prompts__button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.prompts__button--primary {
  border-color: var(--cb-primary);
  background: var(--cb-primary);
  color: var(--cb-bg);
}

.prompts__button--primary:hover:not(:disabled) {
  color: var(--cb-bg);
}

.prompts__error {
  color: var(--cb-danger);
  font-size: var(--cb-text-sm);
  overflow-wrap: anywhere;
}

.prompts__value {
  max-height: 320px;
  margin: 0;
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-bg-soft);
  color: var(--cb-text-muted);
  font-family: var(--cb-font-mono);
  font-size: var(--cb-text-sm);
  white-space: pre-wrap;
  overflow: auto;
  overflow-wrap: anywhere;
}

.prompts__link {
  flex: none;
  padding: var(--cb-space-1) var(--cb-space-3);
  border: 1px solid var(--cb-border-strong);
  border-radius: var(--cb-radius-sm);
  color: var(--cb-primary-strong);
  font-size: var(--cb-text-sm);
  text-decoration: none;
}

.prompts__link:hover {
  border-color: var(--cb-primary);
  text-decoration: none;
}
</style>
