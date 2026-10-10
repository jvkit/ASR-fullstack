<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import UiToggle from '../ui/UiToggle.vue'
import type { SourceMode, VersionMode } from '../ws/client'

const props = defineProps<{
  recording: boolean
  paused: boolean
  insecure: boolean
  online: boolean      // 线上会议模式（录制中）：显示「我开麦了」门控开关
  micOn: boolean       // 麦克风门控当前状态
  levels: number[]     // 实时声压（rms 0~1，100ms 一帧，App 侧环形缓冲）
  elapsedSec: number   // 录音计时（秒，暂停时冻结）
}>()
const emit = defineEmits<{
  start: [payload: {
    title: string
    online: boolean
    versionMode: VersionMode
    sourceMode: SourceMode
    hotwords: string
    videoDeviceId: string   // 线下摄像头（空=不采集视频）
  }]
  pause: []
  resume: []
  stop: []
  micToggle: [on: boolean]
  gotoSpeakers: []     // 声纹注册提醒 → 跳声纹管理页
}>()

// 非实时批次架构：说话人/VAD/阈值全部收敛在服务端 config，前端无调参项
const cfg = reactive({
  title: '',
  versionMode: 'both' as VersionMode, // 默认都走：实时版草稿 + 会末版权威
  sourceMode: 'mic' as SourceMode,    // 线下=麦克风(+可选摄像头预览)；线上=屏幕+系统声（含我开麦了门控）
  hotwords: '',
  videoDeviceId: '',                  // 线下摄像头设备 id（空=不采集视频）
})
const speakerEmpty = ref(false) // 声纹库为空：醒目提醒（会前注册大幅提升分人准确度）
let speakerChecked = false

const versionOptions: { value: VersionMode; label: string; hint: string }[] = [
  { value: 'live', label: '会中实时', hint: '只要会中批次转写与段纪要，不跑会末重跑' },
  { value: 'final', label: '会末精编', hint: '录音只存档，结束后全量重跑出权威版' },
  { value: 'both', label: '两者都要', hint: '会中实时转写+纪要，会末全量重跑权威版（推荐）' },
]
const sourceOptions: { value: SourceMode; label: string; icon: string; hint: string }[] = [
  { value: 'online', label: '线上会议', icon: 'monitor', hint: '采集电脑系统声音+屏幕内容，适用于腾讯会议等' },
  { value: 'mic', label: '线下会议', icon: 'mic', hint: '麦克风采集现场声音，可选摄像头画面' },
]
const versionHint = computed(() => versionOptions.find((o) => o.value === cfg.versionMode)?.hint || '')

// 声纹注册提醒：进入开会页查一次（后端 /api/speakers 转发 GPU 权威库）
function checkSpeakers() {
  if (speakerChecked) return
  speakerChecked = true
  fetch('/api/speakers')
    .then((r) => r.json())
    .then((d) => {
      const list = d.speakers || []
      speakerEmpty.value = Array.isArray(list) && list.length === 0
    })
    .catch(() => { speakerChecked = false }) // 查失败（GPU 不可用）下次再查，不阻塞开会
}

// ---------- 线下摄像头：设备枚举 / 刷新 / 选择 ----------
const videoDevices = ref<{ deviceId: string; label: string }[]>([])
const showCameraPicker = ref(false)
const cameraLoading = ref(false)

async function enumerateCameras(): Promise<{ deviceId: string; label: string }[]> {
  const list = await navigator.mediaDevices.enumerateDevices()
  return list
    .filter((d) => d.kind === 'videoinput')
    .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `摄像头 ${i + 1}` }))
}

// 未授权时 label 全为空 → 先请求一次摄像头权限（用户点刷新=手势内，合规）再枚举
async function refreshCameras() {
  cameraLoading.value = true
  try {
    let devs = await enumerateCameras()
    if (devs.length && devs.every((d) => /^摄像头 \d+$/.test(d.label))) {
      const s = await navigator.mediaDevices.getUserMedia({ video: true })
      s.getTracks().forEach((t) => t.stop())
      devs = await enumerateCameras()
    }
    videoDevices.value = devs
    // 已选设备被拔掉 → 清空选择
    if (cfg.videoDeviceId && !devs.some((d) => d.deviceId === cfg.videoDeviceId)) {
      cfg.videoDeviceId = ''
    }
  } catch {
    // 权限被拒也保留列表（可能拿到带空 label 的项），不阻塞开会
  } finally {
    cameraLoading.value = false
  }
}

const cameraName = computed(() => {
  if (!cfg.videoDeviceId) return '不采集视频'
  return videoDevices.value.find((d) => d.deviceId === cfg.videoDeviceId)?.label || '未知设备'
})

function pickCamera(deviceId: string) {
  cfg.videoDeviceId = deviceId
  showCameraPicker.value = false
}

function onSourceChange(mode: SourceMode) {
  cfg.sourceMode = mode
  if (mode === 'mic' && !videoDevices.value.length) refreshCameras()
}

// 摄像头下拉：点击外部收起
const cameraPickerRef = ref<HTMLElement | null>(null)
function onDocClick(e: MouseEvent) {
  if (cameraPickerRef.value && !cameraPickerRef.value.contains(e.target as Node)) {
    showCameraPicker.value = false
  }
}

function toggleCameraPicker() {
  showCameraPicker.value = !showCameraPicker.value
  if (showCameraPicker.value) {
    if (!videoDevices.value.length) refreshCameras()
    setTimeout(() => document.addEventListener('click', onDocClick), 0)
  } else {
    document.removeEventListener('click', onDocClick)
  }
}

function confirmConfig() {
  const title = cfg.title.trim() || '未命名会议'
  showCameraPicker.value = false
  emit('start', {
    title,
    online: cfg.sourceMode === 'online',
    versionMode: cfg.versionMode,
    sourceMode: cfg.sourceMode,
    hotwords: cfg.hotwords.trim(),
    videoDeviceId: cfg.sourceMode === 'mic' ? cfg.videoDeviceId : '',
  })
}

function gotoSpeakers() {
  emit('gotoSpeakers')
}

function togglePause() {
  props.paused ? emit('resume') : emit('pause')
}

// 录音计时 mm:ss（超 1 小时出 h:mm:ss）
const elapsedText = computed(() => {
  const s = props.elapsedSec
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(sec).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
})

// 声压竖条高度（px）：rms 很小（常态 0.01~0.2），放大映射
function barHeight(rms: number): number {
  return 3 + Math.min(25, Math.round(rms * 130))
}

// 线上会议模式中显示麦克风门控（方案 A：闭麦不释放设备，系统检测不可行，只能手动）
const showMicGate = computed(() => props.recording && props.online)

onMounted(() => {
  if (!props.recording) checkSpeakers()
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
})
</script>

<template>
  <!-- ============ 未开会：居中单列配置页 ============ -->
  <div v-if="!recording" class="idle">
    <div class="hero">
      <h1 class="hero-title">开始一场会议</h1>
      <p class="hero-sub">实时转写 · 分人识别 · 波段纪要 · 会末精编，一次会议两份成果</p>
    </div>

    <div class="card cfg-card">
      <div class="cfg-card-head">会议设置</div>
      <div class="cfg-card-body">
        <!-- 声纹库为空提醒 -->
        <div v-if="speakerEmpty" class="speaker-warn">
          <AppIcon name="warning" :size="15" />
          <span class="warn-text">声纹库为空：会前注册参会人声纹可大幅提升分人准确度，否则几乎全员显示「未识别」</span>
          <button class="btn btn-sm" @click="gotoSpeakers">去注册声纹</button>
        </div>

        <label class="field-label" for="cfg-title">会议标题</label>
        <input id="cfg-title" v-model="cfg.title" class="input" placeholder="未命名会议" maxlength="50" />

        <label class="field-label field-gap">来源模式</label>
        <div class="source-cards">
          <button
            v-for="o in sourceOptions"
            :key="o.value"
            type="button"
            class="source-card"
            :class="{ on: cfg.sourceMode === o.value }"
            @click="onSourceChange(o.value)"
          >
            <span class="source-check"><AppIcon name="check" :size="11" /></span>
            <AppIcon :name="o.icon" :size="22" class="source-icon" />
            <span class="source-label">{{ o.label }}</span>
            <span class="source-hint">{{ o.hint }}</span>
          </button>
        </div>

        <label class="field-label field-gap">版本模式</label>
        <div class="seg">
          <button
            v-for="o in versionOptions"
            :key="o.value"
            type="button"
            class="seg-item"
            :class="{ on: cfg.versionMode === o.value }"
            @click="cfg.versionMode = o.value"
          >
            {{ o.label }}
          </button>
        </div>
        <div class="field-hint">{{ versionHint }}</div>

        <!-- 线下会议：摄像头选择（会议中可预览画面，方便确认采集正常） -->
        <template v-if="cfg.sourceMode === 'mic'">
          <label class="field-label field-gap">摄像头（可选，仅画面预览调试，视频暂不参与纪要）</label>
          <div ref="cameraPickerRef" class="dropdown">
            <button type="button" class="dropdown-trigger" @click.stop="toggleCameraPicker">
              <AppIcon name="camera" :size="15" />
              <span class="dropdown-value">{{ cameraName }}</span>
              <AppIcon name="chevronDown" :size="14" class="dropdown-caret" />
            </button>
            <div v-if="showCameraPicker" class="dropdown-menu">
              <button type="button" class="dropdown-item" @click="pickCamera('')">
                <span>不采集视频</span>
                <AppIcon v-if="!cfg.videoDeviceId" name="check" :size="14" />
              </button>
              <button
                v-for="(d, i) in videoDevices"
                :key="d.deviceId"
                type="button"
                class="dropdown-item"
                @click="pickCamera(d.deviceId)"
              >
                <span>{{ d.label || `摄像头 ${i + 1}` }}</span>
                <AppIcon v-if="cfg.videoDeviceId === d.deviceId" name="check" :size="14" />
              </button>
              <div v-if="!videoDevices.length && !cameraLoading" class="dropdown-empty">
                未检测到摄像头，点下方「刷新列表」重试
              </div>
              <button type="button" class="dropdown-item dropdown-refresh" :disabled="cameraLoading" @click.stop="refreshCameras">
                <AppIcon name="refresh" :size="13" />
                {{ cameraLoading ? '刷新中…' : '刷新列表' }}
              </button>
            </div>
          </div>
        </template>

        <label class="field-label field-gap" for="cfg-hot">热词追加</label>
        <input id="cfg-hot" v-model="cfg.hotwords" class="input" placeholder="可选，逗号分隔，如：张三,Apollo项目" />
        <div class="field-hint">提升专业名词、人名的识别准确率</div>

        <button class="btn btn-primary btn-lg start-btn" :disabled="insecure" @click="confirmConfig">
          <span class="rec-dot-inline" /> 开始记录
        </button>
      </div>
    </div>
  </div>

  <!-- ============ 录音中：状态条 ============ -->
  <div v-else class="rec-wrap">
    <div class="card rec-bar">
      <div class="rec-left">
        <span class="pulse-dot" />
        <span class="rec-label">{{ paused ? '已暂停' : '录音中' }}</span>
        <span class="rec-time num">{{ elapsedText }}</span>
      </div>

      <!-- 实时声压波形 -->
      <div class="wave" :class="{ paused }" aria-hidden="true">
        <span
          v-for="(v, i) in levels"
          :key="i"
          class="wave-bar"
          :style="{ height: barHeight(v) + 'px' }"
        />
      </div>

      <!-- 线上会议麦克风门控（方案 A）：腾讯会议闭麦不释放麦克风设备，系统层检测不到，
           只能手动。闭麦时关掉 → 不录办公室闲聊；开麦说话时打开 → 你的声音才上行 -->
      <div v-if="showMicGate" class="mic-gate">
        <span class="mic-gate-label" :class="micOn ? 'on' : 'off'">我开麦了</span>
        <UiToggle :model-value="micOn" @update:model-value="(v: boolean) => emit('micToggle', v)" />
      </div>

      <div class="rec-actions">
        <button class="icon-btn" :title="paused ? '继续' : '暂停'" @click="togglePause">
          <AppIcon :name="paused ? 'play' : 'pause'" :size="15" />
        </button>
        <button class="btn btn-danger-outline" @click="emit('stop')">
          <AppIcon name="stop" :size="13" /> 结束
        </button>
      </div>
    </div>

    <!-- 麦克风门控状态横幅：漏开 = 自己声音全丢，必须显眼 -->
    <div v-if="showMicGate" class="mic-banner" :class="micOn ? 'on' : 'off'">
      <AppIcon :name="micOn ? 'volume' : 'warning'" :size="14" />
      <span v-if="micOn">麦克风上行中：对方能听到你，你的发言会进入转写</span>
      <span v-else>闭麦中：转写将缺失你的发言——在腾讯会议里开麦时，请同步打开上方「我开麦了」开关</span>
    </div>
  </div>
</template>

<style scoped>
/* ---------- 未开会 ---------- */
.idle { display: flex; flex-direction: column; align-items: center; }
.hero { text-align: center; margin: 40px 0 28px; }
.hero-title { font-size: 26px; font-weight: 600; margin: 0 0 8px; letter-spacing: 0.5px; }
.hero-sub { font-size: 14px; color: var(--text-3); margin: 0; }

.cfg-card { width: 100%; }
.cfg-card-head {
  padding: 14px 20px;
  font-size: 14px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
}
.cfg-card-body { padding: 18px 20px 22px; }
.field-gap { margin-top: 18px; }

.speaker-warn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 16px;
  font-size: 12.5px;
  color: var(--amber);
  background: var(--amber-soft);
  border: 1px solid #f3ddb8;
  border-radius: var(--radius-ctl);
}
.warn-text { flex: 1; line-height: 1.5; }

/* 来源模式大卡片 */
.source-cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.source-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 16px;
  text-align: left;
  font-family: var(--font);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  cursor: pointer;
  transition: border-color 0.15s ease-out, background 0.15s ease-out, box-shadow 0.15s ease-out;
}
.source-card:hover { border-color: var(--border-strong); }
.source-card.on {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.source-card.on .source-icon { color: var(--accent); }
.source-icon { color: var(--text-3); transition: color 0.15s ease-out; }
.source-label { font-size: 14.5px; font-weight: 600; color: var(--text-1); }
.source-hint { font-size: 12px; color: var(--text-3); line-height: 1.5; }
.source-check {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--accent);
  color: #fff;
  opacity: 0;
  transform: scale(0.6);
  transition: all 0.15s ease-out;
}
.source-card.on .source-check { opacity: 1; transform: scale(1); }

/* 摄像头下拉 */
.dropdown { position: relative; }
.dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  font-size: 14px;
  font-family: var(--font);
  color: var(--text-1);
  background: var(--card);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-ctl);
  cursor: pointer;
  transition: border-color 0.15s ease-out;
}
.dropdown-trigger:hover { border-color: #c3c8d0; }
.dropdown-value { flex: 1; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dropdown-caret { color: var(--text-3); }
.dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 50;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: var(--radius-ctl);
  box-shadow: var(--shadow-float);
  padding: 4px;
  max-height: 260px;
  overflow-y: auto;
}
.dropdown-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  font-size: 13.5px;
  font-family: var(--font);
  color: var(--text-1);
  background: none;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
}
.dropdown-item:hover { background: #f2f3f6; }
.dropdown-item :deep(svg) { color: var(--accent); }
.dropdown-refresh {
  justify-content: flex-start;
  color: var(--text-2);
  border-top: 1px solid var(--border);
  border-radius: 0 0 6px 6px;
  margin-top: 4px;
  padding-top: 10px;
}
.dropdown-refresh :deep(svg) { color: var(--text-3); }
.dropdown-empty { padding: 10px; font-size: 12.5px; color: var(--text-3); }

.start-btn { width: 100%; margin-top: 24px; }
.rec-dot-inline {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #fff;
}

/* ---------- 录音中状态条 ---------- */
.rec-wrap { display: flex; flex-direction: column; gap: 10px; }
.rec-bar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
}
.rec-left { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.rec-label { font-size: 14px; font-weight: 600; }
.rec-time { font-size: 14px; color: var(--text-2); font-variant-numeric: tabular-nums; }

/* 声压波形：一排细圆角竖条 */
.wave {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 3px;
  height: 32px;
  min-width: 0;
  overflow: hidden;
}
.wave-bar {
  width: 3px;
  border-radius: 2px;
  background: var(--accent);
  opacity: 0.85;
  transition: height 0.1s linear;
  flex-shrink: 0;
}
.wave.paused .wave-bar { background: #c3c8d0; }

.mic-gate {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 14px;
  border-left: 1px solid var(--border);
  flex-shrink: 0;
}
.mic-gate-label { font-size: 13px; font-weight: 500; }
.mic-gate-label.on { color: var(--green); }
.mic-gate-label.off { color: var(--text-3); }

.rec-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }

.mic-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  font-size: 12.5px;
  border-radius: var(--radius-ctl);
}
.mic-banner.off {
  color: var(--amber);
  background: var(--amber-soft);
  border: 1px solid #f3ddb8;
}
.mic-banner.on {
  color: var(--green);
  background: var(--green-soft);
  border: 1px solid #cdeee0;
  padding-top: 5px;
  padding-bottom: 5px;
}

@media (max-width: 720px) {
  .source-cards { grid-template-columns: 1fr; }
  .wave { display: none; }
}
</style>
