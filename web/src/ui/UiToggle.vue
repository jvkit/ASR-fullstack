<script setup lang="ts">
// 开关（toggle）：替代 van-switch
defineProps<{ modelValue: boolean; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [v: boolean] }>()
</script>

<template>
  <button
    type="button"
    class="toggle"
    :class="{ on: modelValue }"
    :disabled="disabled"
    role="switch"
    :aria-checked="modelValue"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span class="knob" />
  </button>
</template>

<style scoped>
.toggle {
  position: relative;
  width: 40px;
  height: 22px;
  border: none;
  border-radius: 999px;
  background: #d3d6db;
  cursor: pointer;
  transition: background 0.18s ease-out;
  padding: 0;
  flex-shrink: 0;
}
.toggle.on { background: var(--accent); }
.toggle:disabled { opacity: 0.45; cursor: not-allowed; }
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
  transition: transform 0.18s ease-out;
}
.toggle.on .knob { transform: translateX(18px); }
</style>
