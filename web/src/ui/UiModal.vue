<script setup lang="ts">
// 通用弹窗：遮罩 + 卡片，Esc/点遮罩关闭（可配）
import AppIcon from './AppIcon.vue'

withDefaults(
  defineProps<{ show: boolean; title?: string; width?: number; closable?: boolean }>(),
  { title: '', width: 520, closable: true },
)
const emit = defineEmits<{ close: [] }>()

function onMask() {
  emit('close')
}
</script>

<template>
  <Transition name="modal">
    <div v-if="show" class="modal-mask" @click.self="closable && onMask()">
      <div class="modal-card card" :style="{ maxWidth: width + 'px' }" role="dialog">
        <div class="modal-head">
          <span class="modal-title">{{ title }}</span>
          <button v-if="closable" class="icon-btn modal-x" @click="emit('close')">
            <AppIcon name="close" :size="15" />
          </button>
        </div>
        <div class="modal-body">
          <slot />
        </div>
        <div v-if="$slots.footer" class="modal-foot">
          <slot name="footer" />
        </div>
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 900;
  background: rgba(23, 24, 26, 0.42);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 10vh 24px 24px;
  overflow-y: auto;
}
.modal-card {
  width: 100%;
  box-shadow: var(--shadow-float);
  display: flex;
  flex-direction: column;
  max-height: 80vh;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--border);
}
.modal-title { font-size: 15px; font-weight: 600; }
.modal-x { width: 28px; height: 28px; border: none; background: transparent; }
.modal-body { padding: 16px 20px; overflow-y: auto; }
.modal-foot {
  padding: 12px 20px 16px;
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
</style>
