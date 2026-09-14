<script setup lang="ts">
import { reactive } from 'vue'

const props = defineProps<{ recording: boolean; paused: boolean; insecure: boolean }>()
const emit = defineEmits<{
  start: [payload: { title: string }]
  pause: []
  resume: []
  stop: []
}>()

// 非实时批次架构：说话人/VAD/阈值全部收敛在服务端 config，前端无调参项
const form = reactive({
  title: '未命名会议',
})

function start() {
  if (!form.title.trim()) return
  emit('start', { title: form.title.trim() })
}

function togglePause() {
  props.paused ? emit('resume') : emit('pause')
}
</script>

<template>
  <van-cell-group inset class="recorder">
    <van-field
      v-model="form.title"
      label="会议标题"
      placeholder="未命名会议"
      :disabled="recording"
    />

    <div class="actions">
      <template v-if="!recording">
        <van-button type="danger" round block :disabled="insecure" @click="start">
          ● 开始记录
        </van-button>
      </template>
      <template v-else>
        <div class="rec-row">
          <van-tag :type="paused ? 'default' : 'danger'" size="large" class="rec-tag">
            {{ paused ? '已暂停' : 'REC' }}
          </van-tag>
          <van-button type="warning" round @click="togglePause">
            {{ paused ? '继续' : '暂停' }}
          </van-button>
          <van-button type="danger" round @click="emit('stop')">■ 结束会议</van-button>
        </div>
      </template>
    </div>
  </van-cell-group>
</template>

<style scoped>
.recorder { margin-bottom: 8px; }
.actions { padding: 8px 16px 16px; }
.rec-row { display: flex; align-items: center; gap: 12px; justify-content: flex-end; }
.rec-tag { flex: 1; }
@media (min-width: 768px) { .rec-tag { flex: 0; min-width: 80px; } }
</style>
