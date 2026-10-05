<script setup lang="ts">
/**
 * 配置类导航栏（设置页搜索栏下方）。
 *
 * 设置页把字段按「区域 → 配置类」两级分组，这个组件渲染第二级的跳转入口：
 * 每个类一枚 chip，按区域分列；点击平滑滚动到该类的锚点，滚动时高亮当前所在类。
 * 纯展示 + 事件，不碰配置数据（草稿与生效状态都在页面/store 里）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export interface NavSection {
  area: string
  section: string
  label: string
  count: number
}

const props = defineProps<{
  sections: NavSection[]
}>()

const emit = defineEmits<{ jump: [section: string] }>()

const activeSection = ref('')
let observer: IntersectionObserver | null = null

const grouped = computed(() => {
  const order: string[] = []
  const map = new Map<string, NavSection[]>()
  for (const item of props.sections) {
    const list = map.get(item.area)
    if (list) list.push(item)
    else {
      map.set(item.area, [item])
      order.push(item.area)
    }
  }
  return order.map((area) => ({ area, items: map.get(area) ?? [] }))
})

function anchorId(section: string): string {
  return `section-${section.replace(/[.[\]<>]/g, '-')}`
}

function jump(section: string): void {
  activeSection.value = section
  emit('jump', section)
  const element = document.getElementById(anchorId(section))
  element?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}

/** 高亮当前视口里的类：取第一个进入顶部三分之一的锚点。 */
function observeAnchors(): void {
  observer?.disconnect()
  if (typeof IntersectionObserver === 'undefined') return
  const targets = props.sections
    .map((item) => document.getElementById(anchorId(item.section)))
    .filter((element): element is HTMLElement => element !== null)
  if (targets.length === 0) return
  observer = new IntersectionObserver(
    (entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top)
      const first = visible[0]?.target as HTMLElement | undefined
      const section = first?.dataset.section
      if (section) activeSection.value = section
    },
    { rootMargin: '-10% 0px -70% 0px', threshold: 0 },
  )
  for (const target of targets) observer.observe(target)
}

onMounted(observeAnchors)
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <nav v-if="sections.length > 0" class="cb-config-nav" aria-label="配置类导航" data-test="config-nav">
    <div v-for="group in grouped" :key="group.area" class="cb-config-nav__group">
      <span class="cb-config-nav__area">{{ group.area }}</span>
      <div class="cb-config-nav__chips">
        <button
          v-for="item in group.items"
          :key="item.section"
          type="button"
          class="cb-config-nav__chip"
          :class="{ 'cb-config-nav__chip--active': activeSection === item.section }"
          :title="`${item.label}（${item.section}） · ${item.count} 项`"
          :data-test="`config-nav-${item.section}`"
          @click="jump(item.section)"
        >
          {{ item.label }}
          <span class="cb-config-nav__count">{{ item.count }}</span>
        </button>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.cb-config-nav {
  display: flex;
  flex-direction: column;
  gap: var(--cb-space-2);
  padding: var(--cb-space-3);
  border: 1px solid var(--cb-border);
  border-radius: var(--cb-radius-md);
  background: var(--cb-surface);
  max-height: 30vh;
  overflow-y: auto;
}

.cb-config-nav__group {
  display: flex;
  align-items: baseline;
  gap: var(--cb-space-2);
}

.cb-config-nav__area {
  flex: none;
  width: 7em;
  color: var(--cb-text-faint);
  font-size: var(--cb-text-xs);
}

.cb-config-nav__chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--cb-space-1);
}

.cb-config-nav__chip {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  padding: 2px 8px;
  border: 1px solid var(--cb-border);
  border-radius: 999px;
  background: transparent;
  color: var(--cb-text-muted);
  font-size: var(--cb-text-xs);
  cursor: pointer;
}

.cb-config-nav__chip:hover {
  border-color: var(--cb-primary);
  color: var(--cb-text);
}

.cb-config-nav__chip--active {
  border-color: var(--cb-primary);
  color: var(--cb-primary);
  background: color-mix(in srgb, var(--cb-primary) 12%, transparent);
}

.cb-config-nav__count {
  color: var(--cb-text-faint);
  font-size: 10px;
}
</style>
