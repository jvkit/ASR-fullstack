<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import type { SourceMode, VersionMode } from '../ws/client'

const props = defineProps<{
  recording: boolean; paused: boolean; insecure: boolean
  online: boolean      // 线上会议模式（录制中）：显示「我开麦了」门控开关
  micOn: boolean       // 麦克风门控当前状态
}>()
const emit = defineEmits<{
  start: [payload: {
    title: string
    online: boolean
    versionMode: VersionMode
    sourceMode: SourceMode
    hotwords: string
  }]
  pause: []
  resume: []
  stop: []
  micToggle: [on: boolean]
  gotoSpeakers: []     // 声纹注册提醒 → 跳声纹管理页
}>()

// 非实时批次架构：说话人/VAD/阈值全部收敛在服务端 config，前端无调参项
// 开会配置（2026-09-28 双版本架构）：点「开始会议」先弹配置卡片，确认后才 ws start
const showConfig = ref(false)
const cfg = reactive({
  title: '',
  versionMode: 'both' as VersionMode, // 默认都走：实时版草稿 + 会末版权威
  sourceMode: 'mic' as SourceMode,    // 线下=纯麦克风；线上=屏幕+系统声（含我开麦了门控）
  hotwords: '',
})
const speakerEmpty = ref(false) // 声纹库为空：弹醒目提醒（会前注册大幅提升分人准确度）
let speakerChecked = false

const versionOptions: { value: VersionMode; label: string; hint: string }[] = [
  { value: 'both', label: '都走（推荐）', hint: '会中实时转写+纪要，会末全量重跑权威版' },
  { value: 'live', label: '只走实时版', hint: '只要会中批次转写与段纪要，不跑会末重跑' },
  { value: 'final', label: '只会末版', hint: '录音只存档，结束后全量重跑出权威版' },
]
const sourceOptions: { value: SourceMode; label: string; hint: string }[] = [
  { value: 'mic', label: '线下会议', hint: '麦克风采集现场声音' },
  { value: 'online', label: '线上会议', hint: '分享屏幕+系统声音，屏幕内容供 AI 纪要参考' },
]

// 线上会议模式中显示麦克风门控（方案 A：闭麦不释放设备，系统检测不可行，只能手动）
const showMicGate = computed(() => props.recording && props.online)

function openConfig() {
  showConfig.value = true
  // 声纹注册提醒：弹窗打开时查一次（后端 /api/speakers 转发 GPU 权威库）
  if (!speakerChecked) {
    speakerChecked = true
    fetch('/api/speakers')
      .then((r) => r.json())
      .then((d) => {
        const list = d.speakers || []
        speakerEmpty.value = Array.isArray(list) && list.length === 0
      })
      .catch(() => { speakerChecked = false }) // 查失败（GPU 不可用）下次再查，不阻塞开会
  }
}

function confirmConfig() {
  const title = cfg.title.trim() || '未命名会议'
  showConfig.value = false
  emit('start', {
    title,
    online: cfg.sourceMode === 'online',
    versionMode: cfg.versionMode,
    sourceMode: cfg.sourceMode,
    hotwords: cfg.hotwords.trim(),
  })
}

function gotoSpeakers() {
  showConfig.value = false
  emit('gotoSpeakers')
}

function togglePause() {
  props.paused ? emit('resume') : emit('pause')
}
</script>

<template>
  <van-cell-group inset class="recorder">
    <div class="actions">
      <template v-if="!recording">
        <van-button type="danger" round block :disabled="insecure" @click="openConfig">
          ● 开始会议
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
  </van-cell-group>

  <!-- 开会配置卡片：版本选择 / 音源模式 / 标题 / 热词 / 声纹注册提醒 -->
  <van-dialog
    v-model:show="showConfig"
    title="开始会议"
    show-cancel-button
    confirm-button-text="开始"
    confirm-button-color="#ee0a24"
    @confirm="confirmConfig"
  >
    <div class="cfg">
      <van-notice-bar
        v-if="speakerEmpty"
        color="#ed6a0c"
        background="#fffbe8"
        wrapable
        :scrollable="false"
        class="speaker-warn"
      >
        <div class="warn-line">⚠ 声纹库为空：会前注册参会人声纹可大幅提升分人准确度，否则几乎全员显示「未识别」</div>
        <van-button size="small" type="warning" plain class="warn-btn" @click="gotoSpeakers">
          去注册声纹
        </van-button>
      </van-notice-bar>

      <van-field
        v-model="cfg.title"
        label="会议标题"
        placeholder="未命名会议"
        maxlength="50"
      />

      <div class="cfg-group-title">版本选择</div>
      <van-radio-group v-model="cfg.versionMode" class="cfg-radios">
        <van-cell v-for="o in versionOptions" :key="o.value" :title="o.label" :label="o.hint" clickable @click="cfg.versionMode = o.value">
          <template #right-icon>
            <van-radio :name="o.value" />
          </template>
        </van-cell>
      </van-radio-group>

      <div class="cfg-group-title">音源模式</div>
      <van-radio-group v-model="cfg.sourceMode" class="cfg-radios">
        <van-cell v-for="o in sourceOptions" :key="o.value" :title="o.label" :label="o.hint" clickable @click="cfg.sourceMode = o.value">
          <template #right-icon>
            <van-radio :name="o.value" />
          </template>
        </van-cell>
      </van-radio-group>

      <van-field
        v-model="cfg.hotwords"
        label="热词追加"
        placeholder="可选，逗号分隔，如：张三,Apollo项目"
      >
        <template #label><span class="hot-label">热词追加</span></template>
      </van-field>
    </div>
  </van-dialog>
</template>

<style scoped>
.recorder { margin-bottom: 8px; }
.actions { padding: 8px 16px 16px; }
.recorder .actions { padding-top: 16px; }
.rec-row { display: flex; align-items: center; gap: 12px; justify-content: flex-end; }
.rec-tag { flex: 1; }
.online-hint { font-size: 12px; color: #999; }
.mic-cell { background: #fff8e6; }
.mic-on { font-weight: 600; color: #3aa675; }
.mic-off { font-weight: 600; color: #999; }

/* 开会配置卡片 */
.cfg { padding: 4px 0 12px; }
.cfg-group-title { padding: 12px 16px 4px; font-size: 13px; color: #969799; }
.cfg-radios :deep(.van-cell) { padding-top: 8px; padding-bottom: 8px; }
.speaker-warn { margin: 8px 16px; border-radius: 6px; }
.warn-line { font-size: 12px; line-height: 1.6; }
.warn-btn { margin-top: 6px; }
.hot-label { font-size: 14px; }

@media (min-width: 768px) { .rec-tag { flex: 0; min-width: 80px; } }
</style>
