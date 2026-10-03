<script setup lang="ts">
/**
 * 侧栏（§24-§25）：v1.0 的七个信息入口。
 *
 * 只有「总览」有真实路由；其余六项按计划以 aria-disabled 的占位项渲染
 * 「即将开放」，绝不指向尚未存在的路由。
 */
import type { Component } from 'vue'
import { RouterLink } from 'vue-router'

import IconAi from '@/components/icons/IconAi.vue'
import IconCharacter from '@/components/icons/IconCharacter.vue'
import IconHome from '@/components/icons/IconHome.vue'
import IconMedia from '@/components/icons/IconMedia.vue'
import IconMemory from '@/components/icons/IconMemory.vue'
import IconSocial from '@/components/icons/IconSocial.vue'
import IconSystem from '@/components/icons/IconSystem.vue'

interface NavEntry {
  key: string
  label: string
  icon: Component
  to?: string
  enabled: boolean
}

defineProps<{ collapsed: boolean }>()

const entries: NavEntry[] = [
  { key: 'dashboard', label: '总览', icon: IconHome, to: '/', enabled: true },
  { key: 'character', label: '角色', icon: IconCharacter, enabled: false },
  { key: 'ai', label: 'AI 与模型', icon: IconAi, enabled: false },
  { key: 'social', label: '社交', icon: IconSocial, enabled: false },
  { key: 'memory', label: '记忆', icon: IconMemory, enabled: false },
  { key: 'media', label: '媒体与能力', icon: IconMedia, enabled: false },
  { key: 'system', label: '系统', icon: IconSystem, enabled: false },
]

function accessibleLabel(entry: NavEntry): string {
  return entry.enabled ? entry.label : `${entry.label}（即将开放）`
}
</script>

<template>
  <nav class="cb-sidebar" :class="{ 'cb-sidebar--collapsed': collapsed }" aria-label="主导航">
    <ul class="cb-sidebar__list">
      <li v-for="entry in entries" :key="entry.key" class="cb-sidebar__item-wrap">
        <RouterLink
          v-if="entry.enabled && entry.to"
          :to="entry.to"
          custom
          v-slot="{ href, navigate, isActive }"
        >
          <a
            class="cb-sidebar__item"
            :class="{ 'is-active': isActive }"
            :href="href"
            :aria-current="isActive ? 'page' : undefined"
            :aria-label="collapsed ? accessibleLabel(entry) : undefined"
            :title="collapsed ? accessibleLabel(entry) : undefined"
            @click="navigate"
          >
            <component :is="entry.icon" :size="18" class="cb-sidebar__icon" />
            <span v-if="!collapsed" class="cb-sidebar__label">{{ entry.label }}</span>
          </a>
        </RouterLink>

        <button
          v-else
          type="button"
          class="cb-sidebar__item is-disabled"
          aria-disabled="true"
          :aria-label="collapsed ? accessibleLabel(entry) : undefined"
          :title="collapsed ? accessibleLabel(entry) : undefined"
        >
          <component :is="entry.icon" :size="18" class="cb-sidebar__icon" />
          <span v-if="!collapsed" class="cb-sidebar__label">{{ entry.label }}</span>
          <span v-if="!collapsed" class="cb-sidebar__soon">即将开放</span>
        </button>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.cb-sidebar {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: var(--cb-space-3);
}

.cb-sidebar__list {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.cb-sidebar__item {
  display: flex;
  align-items: center;
  gap: var(--cb-space-3);
  width: 100%;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid transparent;
  border-radius: var(--cb-radius-md);
  background: transparent;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-md);
  font-family: inherit;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.cb-sidebar__item:hover {
  background: var(--cb-surface);
  color: var(--cb-text);
  text-decoration: none;
}

.cb-sidebar__item.is-active {
  background: var(--cb-primary-soft);
  border-color: var(--cb-primary);
  color: var(--cb-primary-strong);
}

.cb-sidebar__item.is-disabled {
  cursor: default;
  color: var(--cb-text-faint);
}

.cb-sidebar__item.is-disabled:hover {
  background: transparent;
  color: var(--cb-text-faint);
}

.cb-sidebar__icon {
  flex: none;
}

.cb-sidebar__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cb-sidebar__soon {
  flex: none;
  padding: 1px var(--cb-space-2);
  border: 1px solid var(--cb-border-strong);
  border-radius: 999px;
  font-size: var(--cb-text-xs);
  color: var(--cb-text-faint);
}

.cb-sidebar--collapsed .cb-sidebar__item {
  justify-content: center;
  padding: var(--cb-space-2);
}
</style>
