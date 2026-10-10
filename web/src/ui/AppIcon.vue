<script setup lang="ts">
// 线性图标渲染：内联 SVG，stroke 1.8，尺寸走 font-size 语境
import { icons } from './icons'

withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="icons[name] || ''"
  />
</template>
