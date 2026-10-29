<script setup lang="ts">
/** 内联 SVG 图标（§46）：不引入图标库，只使用 currentColor。 */
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
    <path d="M3.5 10.5 12 4l8.5 6.5" />
    <path d="M5.5 9.5V20h13V9.5" />
    <path d="M9.5 20v-5h5v5" />
  </svg>
</template>
