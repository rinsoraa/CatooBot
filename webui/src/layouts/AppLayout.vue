<script setup lang="ts">
/**
 * 应用外壳（§23、§36）：侧栏 + 顶栏 + 主内容的网格布局。
 *
 * ≥1024px 侧栏常驻（可折叠）；<1024px 变为覆盖式抽屉，
 * 切换路由或按 Esc 都会关闭，避免遮挡主内容。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import AppSidebar from '@/components/AppSidebar.vue'
import AppTopbar from '@/components/AppTopbar.vue'
import RestartBanner from '@/components/config/RestartBanner.vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()
const route = useRoute()

const drawerOpen = ref(false)
const isCompact = ref(false)
let media: MediaQueryList | null = null

function onMediaChange(event: MediaQueryListEvent | MediaQueryList): void {
  isCompact.value = event.matches
  if (!event.matches) drawerOpen.value = false
}

function handleToggle(): void {
  if (isCompact.value) drawerOpen.value = !drawerOpen.value
  else app.toggleSidebar()
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && drawerOpen.value) drawerOpen.value = false
}

watch(
  () => route.fullPath,
  () => {
    drawerOpen.value = false
  },
)

onMounted(() => {
  media = window.matchMedia('(max-width: 1023px)')
  onMediaChange(media)
  media.addEventListener('change', onMediaChange)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  media?.removeEventListener('change', onMediaChange)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div
    class="cb-shell"
    :class="{ 'cb-shell--collapsed': app.sidebarCollapsed, 'cb-shell--compact': isCompact }"
  >
    <a class="cb-shell__skip" href="#cb-main">跳到主内容</a>

    <aside v-if="!isCompact" class="cb-shell__sidebar" aria-label="侧栏">
      <AppSidebar :collapsed="app.sidebarCollapsed" />
    </aside>

    <div class="cb-shell__body">
      <AppTopbar @toggle-sidebar="handleToggle" />
      <main id="cb-main" class="cb-shell__main">
        <div class="cb-shell__inner">
          <!-- 全局重启提醒（§52/§90）：有 pending 时才渲染 -->
          <RestartBanner />
          <slot />
        </div>
      </main>
    </div>

    <Transition name="cb-drawer">
      <div v-if="isCompact && drawerOpen" class="cb-shell__drawer">
        <div
          class="cb-shell__drawer-backdrop"
          data-test="drawer-backdrop"
          aria-hidden="true"
          @click="drawerOpen = false"
        />
        <div class="cb-shell__drawer-panel" role="dialog" aria-modal="true" aria-label="侧栏">
          <AppSidebar :collapsed="false" />
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.cb-shell {
  position: relative;
  display: grid;
  grid-template-columns: var(--cb-sidebar-width) minmax(0, 1fr);
  min-height: 100vh;
  background: var(--cb-bg);
}

.cb-shell--collapsed {
  grid-template-columns: var(--cb-sidebar-width-collapsed) minmax(0, 1fr);
}

.cb-shell--compact {
  grid-template-columns: minmax(0, 1fr);
}

.cb-shell__skip {
  position: absolute;
  left: var(--cb-space-3);
  top: calc(-1 * var(--cb-space-6));
  z-index: 1000;
  padding: var(--cb-space-2) var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-sm);
  background: var(--cb-surface-raised);
  color: var(--cb-text);
  font-size: var(--cb-text-sm);
}

.cb-shell__skip:focus {
  top: var(--cb-space-3);
}

.cb-shell__sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
  border-right: 1px solid var(--cb-border);
  background: var(--cb-bg-soft);
}

.cb-shell__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 100vh;
}

.cb-shell__main {
  flex: 1;
  padding: var(--cb-space-5);
}

.cb-shell__inner {
  width: 100%;
  max-width: var(--cb-layout-max);
  margin: 0 auto;
}

.cb-shell__drawer {
  position: fixed;
  inset: 0;
  z-index: 900;
}

.cb-shell__drawer-backdrop {
  position: absolute;
  inset: 0;
  background: var(--cb-bg);
  opacity: 0.7;
}

.cb-shell__drawer-panel {
  position: absolute;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--cb-sidebar-width);
  overflow-y: auto;
  border-right: 1px solid var(--cb-border);
  background: var(--cb-bg-soft);
  box-shadow: var(--cb-shadow-md);
}

.cb-drawer-enter-active,
.cb-drawer-leave-active {
  transition: opacity 0.16s ease;
}

.cb-drawer-enter-active .cb-shell__drawer-panel,
.cb-drawer-leave-active .cb-shell__drawer-panel {
  transition: transform 0.16s ease;
}

.cb-drawer-enter-from,
.cb-drawer-leave-to {
  opacity: 0;
}

.cb-drawer-enter-from .cb-shell__drawer-panel,
.cb-drawer-leave-to .cb-shell__drawer-panel {
  transform: translateX(-16px);
}

@media (max-width: 1023px) {
  .cb-shell__main {
    padding: var(--cb-space-4);
  }
}
</style>
