<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

const props = defineProps<{ recording: boolean; paused: boolean; insecure: boolean }>()
const emit = defineEmits<{
  start: [payload: {
    title: string
    spkThreshold: number
    speakerCount: number
    maxSpeakers: number
    vadMaxEndSilenceMs: number
    vadMaxSegmentSec: number
    spkMinSegSec: number
  }]
  pause: []
  resume: []
  stop: []
}>()

const form = reactive({
  title: '未命名会议',
  spkThreshold: 0.55,
  speakerCount: 0,
  // 高级参数（默认与后端 config.py 一致）
  maxSpeakers: 0,
  vadMaxEndSilenceMs: 350,
  vadMaxSegmentSec: 8,
  spkMinSegSec: 1.0,
})

const showAdvanced = ref(false)
// 录音开始后不允许改参数，面板自动收起
watch(() => props.recording, (rec) => { if (rec) showAdvanced.value = false })

function start() {
  if (!form.title.trim()) return
  const thr = Number(form.spkThreshold)
  emit('start', {
    title: form.title.trim(),
    spkThreshold: Number.isFinite(thr) && thr > 0 && thr < 1 ? thr : 0.55,
    // 0 = 不指定（后端自动估计）；>0 = 明确告知参会人数
    speakerCount: Number.isFinite(form.speakerCount) && form.speakerCount > 0 ? Math.floor(form.speakerCount) : 0,
    maxSpeakers: Number.isFinite(form.maxSpeakers) && form.maxSpeakers > 0 ? Math.floor(form.maxSpeakers) : 0,
    vadMaxEndSilenceMs: Math.max(100, Math.floor(form.vadMaxEndSilenceMs || 350)),
    vadMaxSegmentSec: Math.max(2, Math.floor(form.vadMaxSegmentSec || 8)),
    spkMinSegSec: Math.max(0, Number(form.spkMinSegSec) || 0),
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

    <!-- 高级设置（折叠面板）：说话人/VAD 调参 -->
    <div class="adv-toggle" @click="showAdvanced = !showAdvanced">
      <span>{{ showAdvanced ? '▾' : '▸' }} 高级设置</span>
      <span class="adv-hint">说话人区分 / 切句调优</span>
    </div>
    <div v-show="showAdvanced" class="adv-panel">
      <van-field label="聚类阈值">
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
          <span class="thr-tip">高=不易并人，低=更宽松</span>
        </template>
      </van-field>
      <van-field label="人数上限">
        <template #input>
          <van-stepper
            v-model="form.maxSpeakers"
            :min="0"
            :max="15"
            :integer="true"
            :disabled="recording"
          />
          <span class="thr-tip">0=不限制；>0 时实时聚类最多 N 人，超出并入最相近者</span>
        </template>
      </van-field>
      <van-field label="断句静音(ms)">
        <template #input>
          <van-stepper
            v-model="form.vadMaxEndSilenceMs"
            :min="100"
            :max="1500"
            :step="50"
            :integer="true"
            :disabled="recording"
          />
          <span class="thr-tip">小=切句快出字快，大=句子更完整</span>
        </template>
      </van-field>
      <van-field label="单段上限(s)">
        <template #input>
          <van-stepper
            v-model="form.vadMaxSegmentSec"
            :min="2"
            :max="60"
            :integer="true"
            :disabled="recording"
          />
          <span class="thr-tip">长段强制切；越小越碎，越大分人越稳</span>
        </template>
      </van-field>
      <van-field label="最短声纹段(s)">
        <template #input>
          <van-stepper
            :model-value="form.spkMinSegSec"
            :min="0"
            :max="5"
            :step="0.5"
            :decimal-length="1"
            :disabled="recording"
            @change="(v: number) => (form.spkMinSegSec = v)"
          />
          <span class="thr-tip">短于此的句子不做声纹（碎段声纹不稳）</span>
        </template>
      </van-field>
    </div>

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

.adv-toggle {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 16px; font-size: 13px; color: #1989fa; cursor: pointer;
  border-top: 1px solid #f0f0f0;
}
.adv-hint { font-size: 12px; color: #c8c9cc; }
.adv-panel { padding-bottom: 4px; }
</style>
