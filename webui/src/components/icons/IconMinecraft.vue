<script setup lang="ts">
/** 内联 SVG 图标（§46）：不引入图标库，只使用 currentColor。Minecraft = 等距方块。 */
withDefaults(defineProps<{ size?: number }>(), { size: 18 })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path d="M12 3.2 20.5 7.6 12 12 3.5 7.6z" />
    <path d="M3.5 7.6v8.8L12 20.8l8.5-4.4V7.6" />
    <path d="M12 12v8.8" />
  </svg>
</template>
