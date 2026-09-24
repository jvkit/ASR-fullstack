<script setup lang="ts">
import { computed, reactive } from 'vue'

const props = defineProps<{
  recording: boolean; paused: boolean; insecure: boolean
  online: boolean      // 线上会议模式（录制中）：显示「我开麦了」门控开关
  micOn: boolean       // 麦克风门控当前状态
}>()
const emit = defineEmits<{
  start: [payload: { title: string; online: boolean }]
  pause: []
  resume: []
  stop: []
  micToggle: [on: boolean]
}>()

// 非实时批次架构：说话人/VAD/阈值全部收敛在服务端 config，前端无调参项
const form = reactive({
  title: '未命名会议',
  online: false, // 线上会议：额外抓屏（系统声+屏幕内容）走视觉通道
})

// 线上会议模式中显示麦克风门控（方案 A：闭麦不释放设备，系统检测不可行，只能手动）
const showMicGate = computed(() => props.recording && props.online)

function start() {
  if (!form.title.trim()) return
  emit('start', { title: form.title.trim(), online: form.online })
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

    <van-cell title="线上会议" :border="false" class="online-cell">
      <template #label>
        <span class="online-hint">分享屏幕+系统声音，屏幕内容定时截图供 AI 纪要参考</span>
      </template>
      <van-switch v-model="form.online" :disabled="recording" size="22px" />
    </van-cell>

    <!-- 麦克风门控（方案 A）：腾讯会议闭麦不释放麦克风设备，系统层检测不到，
         只能手动。闭麦时关掉 → 不录办公室闲聊；开麦说话时打开 → 你的声音才上行 -->
    <van-cell v-if="showMicGate" :border="false" class="mic-cell">
      <template #title>
        <span :class="micOn ? 'mic-on' : 'mic-off'">
          {{ micOn ? '🎤 我开麦了（我的声音正在记录）' : '🔇 我闭麦了（只记录会议声音）' }}
        </span>
      </template>
      <template #label>
        <span class="online-hint">腾讯会议点开麦时打开本开关，闭麦时关掉；避免录进办公室闲聊</span>
      </template>
      <van-switch :model-value="micOn" size="22px" @update:model-value="(v: boolean) => emit('micToggle', v)" />
    </van-cell>

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
.online-hint { font-size: 12px; color: #999; }
.mic-cell { background: #fff8e6; }
.mic-on { font-weight: 600; color: #3aa675; }
.mic-off { font-weight: 600; color: #999; }
@media (min-width: 768px) { .rec-tag { flex: 0; min-width: 80px; } }
</style>
