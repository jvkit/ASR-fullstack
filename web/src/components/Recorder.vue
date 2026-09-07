<script setup lang="ts">
import { reactive } from 'vue'

const props = defineProps<{ recording: boolean; paused: boolean; insecure: boolean }>()
const emit = defineEmits<{
  start: [payload: { title: string; spkThreshold: number; speakerCount: number }]
  pause: []
  resume: []
  stop: []
}>()

const form = reactive({ title: '未命名会议', spkThreshold: 0.55, speakerCount: 0 })

function start() {
  if (!form.title.trim()) return
  const thr = Number(form.spkThreshold)
  emit('start', {
    title: form.title.trim(),
    spkThreshold: Number.isFinite(thr) && thr > 0 && thr < 1 ? thr : 0.55,
    // 0 = 不指定（后端自动估计）；>0 = 明确告知参会人数
    speakerCount: Number.isFinite(form.speakerCount) && form.speakerCount > 0 ? Math.floor(form.speakerCount) : 0,
  })
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
    <van-field label="说话人阈值">
      <template #input>
        <van-stepper
          :model-value="form.spkThreshold"
          :min="0.3"
          :max="0.95"
          :step="0.05"
          :decimal-length="2"
          :disabled="recording"
          @change="(v: number) => (form.spkThreshold = v)"
        />
        <span class="thr-tip">高=更严格不易并人，低=更宽松</span>
      </template>
    </van-field>
    <van-field label="参会人数">
      <template #input>
        <van-stepper
          v-model="form.speakerCount"
          :min="0"
          :max="15"
          :integer="true"
          :disabled="recording"
        />
        <span class="thr-tip">0=自动估计；填真实人数可显著提升说话人区分</span>
      </template>
    </van-field>

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
.thr-tip { font-size: 12px; color: #969799; margin-left: 8px; }
@media (min-width: 768px) { .rec-tag { flex: 0; min-width: 80px; } }
</style>
