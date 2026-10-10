<script setup lang="ts">
// toast 宿主：顶部居中堆叠，滑入淡出
import { toastState } from './toast'
import AppIcon from './AppIcon.vue'
</script>

<template>
  <div class="toast-host">
    <TransitionGroup name="toast">
      <div v-for="t in toastState.items" :key="t.id" class="toast" :class="`toast-${t.type}`">
        <AppIcon
          :name="t.type === 'success' ? 'check' : t.type === 'error' ? 'warning' : 'doc'"
          :size="15"
        />
        <span>{{ t.message }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 480px;
  padding: 9px 16px;
  font-size: 13.5px;
  color: var(--text-1);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow-float);
}
.toast-success :deep(svg) { color: var(--green); }
.toast-error :deep(svg) { color: var(--rec); }
.toast-info :deep(svg) { color: var(--accent); }
.toast-enter-active { transition: all 0.22s ease-out; }
.toast-leave-active { transition: all 0.18s ease-in; }
.toast-enter-from { opacity: 0; transform: translateY(-10px); }
.toast-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
