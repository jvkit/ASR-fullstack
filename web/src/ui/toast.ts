/**
 * 轻量 toast：替代 vant showToast。
 * 用法：import { showToast } from '../ui/toast'；宿主组件 ToastHost 挂在 App 根。
 */
import { reactive } from 'vue'

export interface ToastItem {
  id: number
  message: string
  type: 'success' | 'error' | 'info'
}

export const toastState = reactive<{ items: ToastItem[] }>({ items: [] })

let seq = 0

export function showToast(message: string, type: ToastItem['type'] = 'info', duration = 3000): void {
  const id = ++seq
  toastState.items.push({ id, message, type })
  setTimeout(() => {
    const i = toastState.items.findIndex((t) => t.id === id)
    if (i >= 0) toastState.items.splice(i, 1)
  }, duration)
}
