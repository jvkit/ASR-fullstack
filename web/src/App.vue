<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import LiveTranscript from './components/LiveTranscript.vue'
import MeetingDetail from './components/MeetingDetail.vue'
import MeetingList from './components/MeetingList.vue'
import Recorder from './components/Recorder.vue'
import SpeakerManage from './components/SpeakerManage.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import AppIcon from './ui/AppIcon.vue'
import ToastHost from './ui/ToastHost.vue'
import { showToast } from './ui/toast'
import { MicRecorder } from './ws/audio'
import { ScreenFrameCapture, ScreenVideoRecorder } from './ws/screen'
import { MeetingWs, type ServerMessage, type SourceMode, type VersionMode } from './ws/client'

interface Utterance {
  seq: number
  speaker: string
  text: string
  start_ms: number
  end_ms: number
  draft: boolean // 草稿：原始转写（未纠错定稿），收到 corrected update 后变正式
}

interface SummaryItem {
  seq: number
  scope: string
  title: string
  points: string[]
  // 2026-09-28 起波次 LLM 输出结构化待办 {task,assignee,due,quote}；旧数据是 {who,what,due,status}
  actionItems: { who?: string; what?: string; task?: string; assignee?: string; due?: string; status?: string; quote?: string }[]
  attributes: { key: string; value: string }[]
  final: boolean
  // 纪要↔转写联动：本波覆盖的转写时间范围（seq=0 终版总结无此字段）
  startMs?: number
  endMs?: number
}

interface StartConfig {
  title: string
  online: boolean
  versionMode: VersionMode
  sourceMode: SourceMode
  hotwords: string
  videoDeviceId: string // 线下摄像头（空=不采集视频）
}

// 会末版进度（final_progress 推送；详情页另有轮询兜底，这里覆盖"刚结束还在会议页"的场景）
const finalProgress = ref<{ stage: string; pct: number } | null>(null)
const FINAL_STAGE_TEXT: Record<string, string> = {
  reannotate: '全场重跑（分人+转写）',
  waves: '会末波次纪要',
  summary: '会末最终总结',
  done: '完成',
}
const finalProgressText = computed(() =>
  finalProgress.value ? FINAL_STAGE_TEXT[finalProgress.value.stage] || finalProgress.value.stage : '',
)

type View = 'live' | 'list' | 'detail' | 'speakers'

const view = ref<View>('live')
const detailMeetingId = ref('')
const meetingCount = ref(0) // 列表页有新会议时点亮导航角标
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

const recording = ref(false)
const paused = ref(false)
const meetingId = ref('')
const utterances = ref<Utterance[]>([])
const summaries = ref<SummaryItem[]>([])
const liveAttrs = ref<{ key: string; value: string }[]>([]) // 实时提取的事实性属性
const errorMsg = ref('')

// 录音计时：从 started 消息起算，暂停时冻结
const elapsedSec = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null

function startElapsed() {
  stopElapsed()
  elapsedSec.value = 0
  elapsedTimer = setInterval(() => {
    if (!paused.value) elapsedSec.value++
  }, 1000)
}
function stopElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = null
}

// 实时声压波形：MicRecorder 的 onLevel（rms 0~1，100ms 一帧），环形保留最近 48 帧
const levels = ref<number[]>([])
function onLevel(rms: number) {
  levels.value.push(rms)
  if (levels.value.length > 48) levels.value.shift()
}

// 纪要↔转写联动：右栏点波次 → 左栏滚动+高亮该时间范围；左栏点分隔条 → 右栏定位卡片
const highlightRange = ref<{ startMs: number; endMs: number; nonce: number } | null>(null)
const activeWaveSeq = ref(0)
const activeWaveNonce = ref(0)

// 纪要波次（供转写流插入分隔条）：seq≥1 且带时间范围
const liveWaves = computed(() =>
  summaries.value
    .filter((s) => s.seq >= 1 && s.startMs != null)
    .map((s) => ({ seq: s.seq, title: s.title, startMs: s.startMs!, endMs: s.endMs ?? s.startMs! })),
)

// 重放测试（用历史录音模拟实时开会）：非空=正在重放某场历史会议
const replaying = ref(false)
const replaySourceId = ref('')
const replayAudioUrl = ref('')
const replayAudioEl = ref<HTMLAudioElement | null>(null)
const replayFinished = ref(false)

const mic = reactive(new MicRecorder())
const ws = reactive(new MeetingWs())
// 线上会议视觉通道：额外抓屏（系统声+屏幕同一次 getDisplayMedia），定时抽帧上行 OCR
const onlineMode = ref(false)
const versionMode = ref<VersionMode>('both') // 本场开会配置（双版本架构，默认都走）
const screenCapture = new ScreenFrameCapture()
const videoRecorder = new ScreenVideoRecorder()
let pendingDisplayStream: MediaStream | null = null
// 视频预览（调试：线下=摄像头画面，线上=屏幕画面）；.prop 直接绑 DOM srcObject，渲染即生效
let cameraStream: MediaStream | null = null // 线下摄像头（独立流，需自行停轨）
const previewStream = ref<MediaStream | null>(null)

// 视觉抽帧的视频源：线上=屏幕流，线下=摄像头流（两者都进 OCR，程序不区分来源）
function visualSourceStream(): MediaStream | null {
  return onlineMode.value ? pendingDisplayStream : cameraStream
}

// ---------- 预览调试面板：流→轨道→video 元素→播放状态，每秒采样 ----------
const previewDebug = ref('')
const previewDebugOpen = ref(false) // 收进可折叠「调试详情」
let previewDebugTimer: ReturnType<typeof setInterval> | null = null

function startPreviewDebug() {
  stopPreviewDebug()
  previewDebugTimer = setInterval(() => {
    const s = previewStream.value
    if (!s) {
      previewDebug.value = 'stream=null'
      return
    }
    const vt = s.getVideoTracks()[0]
    const parts = [
      `stream✓ tracks=${s.getTracks().length}`,
      vt
        ? `track=${vt.readyState},en=${vt.enabled},mu=${vt.muted}`
        : 'track=无video轨',
    ]
    const el = document.querySelector<HTMLVideoElement>('.vp-video')
    if (!el) {
      parts.push('el=未渲染')
    } else {
      parts.push(
        `el.rs=${el.readyState} ${el.videoWidth}x${el.videoHeight} paused=${el.paused} t=${el.currentTime.toFixed(1)} muted=${el.muted} err=${el.error?.code ?? '-'} so=${el.srcObject ? '✓' : 'null'}`,
      )
      if (el.paused && el.srcObject) el.play().catch(() => {}) // autoplay 被拒自动救活
    }
    previewDebug.value = parts.join(' | ')
  }, 1000)
}

function stopPreviewDebug() {
  if (previewDebugTimer) clearInterval(previewDebugTimer)
  previewDebugTimer = null
  previewDebug.value = ''
  previewDebugOpen.value = false
}

// 说话人配色（按出现顺序循环；浅底清晰、不荧光）
const speakerColors = ['#2f5cff', '#0f9d6c', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d']
const speakerColorMap = new Map<string, string>()
function colorOf(speaker: string): string {
  if (!speakerColorMap.has(speaker)) {
    speakerColorMap.set(speaker, speakerColors[speakerColorMap.size % speakerColors.length])
  }
  return speakerColorMap.get(speaker)!
}
const knownSpeakers = computed(() => [...speakerColorMap.keys()])

function handleMessage(msg: ServerMessage) {
  switch (msg.type) {
    case 'started':
      meetingId.value = msg.meeting_id
      startElapsed()
      // 视觉抽帧上行：线上=屏幕帧，线下=摄像头帧（帧带 meeting_id 送 OCR）
      {
        const vs = visualSourceStream()
        if (vs) {
          // 用户在浏览器分享条上手动取消分享：video 轨 ended → 只停抽帧，音频不动
          vs.getVideoTracks()[0]?.addEventListener('ended', () => {
            screenCapture.stop()
            void videoRecorder.stop()  // 持久化录像随分享结束收尾（含尾片上传）
          })
          screenCapture.start(vs, msg.meeting_id).catch(() => {})
          videoRecorder.start(vs, msg.meeting_id)  // 整轨录像落盘（2026-10-08）
        }
      }
      break
    case 'transcript':
      utterances.value.push({
        seq: msg.seq,
        speaker: msg.speaker || '发言人',
        text: msg.text,
        start_ms: msg.start_ms,
        end_ms: msg.end_ms,
        draft: true,
      })
      colorOf(msg.speaker || '发言人')
      break
    case 'transcript_update': {
      // 滚动打标/纠错回写：按 seq 原地替换说话人与文本
      // corrected=true → 定稿（去草稿样式）；false → 仅换说话人标签（打标），文字仍是草稿
      const idx = utterances.value.findIndex((u) => u.seq === msg.seq)
      if (idx >= 0) {
        const draft = msg.corrected ? false : utterances.value[idx].draft
        utterances.value[idx] = {
          seq: msg.seq,
          speaker: msg.speaker || '发言人',
          text: msg.text,
          start_ms: msg.start_ms,
          end_ms: msg.end_ms,
          draft,
        }
        colorOf(msg.speaker || '发言人')
      }
      break
    }
    case 'summary': {
      // 按 (scope, seq, final) upsert：段纪要可被下一波订正（服务端会重发同 seq），
      // 无脑 push 会让订正显示成重复条目。会中视图只收 live scope，final 防御性丢弃
      if (msg.scope === 'final') break
      const row: SummaryItem = {
        seq: msg.seq,
        scope: msg.scope || 'live',
        title: msg.title,
        points: msg.points,
        actionItems: msg.action_items as SummaryItem['actionItems'],
        attributes: msg.attributes as SummaryItem['attributes'],
        final: msg.final,
        startMs: msg.start_ms,
        endMs: msg.end_ms,
      }
      const si = summaries.value.findIndex(
        (s) => s.scope === row.scope && s.seq === msg.seq && s.final === msg.final,
      )
      if (si >= 0) summaries.value[si] = row
      else summaries.value.push(row)
      break
    }
    case 'final_progress':
      // 会末版重跑进度（只在 final/both 模式出现）：会中视图展示轻量进度条
      if (versionMode.value !== 'final' && versionMode.value !== 'both') break
      finalProgress.value = { stage: msg.stage, pct: msg.pct }
      break
    case 'final_done':
      finalProgress.value = null
      showToast('会末版已生成，可在历史会议查看', 'success', 3000)
      meetingCount.value++
      break
    case 'attributes':
      liveAttrs.value = msg.attributes || []
      break
    case 'ended':
      recording.value = false
      paused.value = false
      stopElapsed()
      screenCapture.stop()
      pendingDisplayStream?.getTracks().forEach((t) => t.stop())
      pendingDisplayStream = null
      cameraStream?.getTracks().forEach((t) => t.stop())
      cameraStream = null
      previewStream.value = null
      stopPreviewDebug()
      if (replaying.value) {
        replayFinished.value = true
      }
      meetingCount.value++
      break
    case 'reannotated':
      showToast('会末终版转写已生成，可在历史会议查看', 'success', 3000)
      break
    case 'error':
      errorMsg.value = msg.message
      showToast(msg.message, 'error', 4000)
      break
  }
}

async function onStart(payload: StartConfig) {
  if (insecure.value) {
    showToast('http 访问下浏览器禁止麦克风，请用 https 地址打开', 'error', 4000)
    return
  }
  onlineMode.value = payload.online
  versionMode.value = payload.versionMode
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  liveAttrs.value = []
  speakerColorMap.clear()
  finalProgress.value = null
  highlightRange.value = null
  levels.value = []

  await ws.connect()
  ws.onMessage(handleMessage)
  // 线上会议：先抓屏（getDisplayMedia 必须在用户手势内调用），系统声+屏幕同一次采集；
  // audio 轨交给系统声混音，video 轨给 ScreenFrameCapture 定时抽帧
  if (onlineMode.value) {
    try {
      pendingDisplayStream = await navigator.mediaDevices.getDisplayMedia({
        audio: true,
        video: { frameRate: 5 },
      })
      previewStream.value = pendingDisplayStream // 屏幕画面预览（调试）
    } catch (e) {
      showToast(`屏幕分享被取消或失败：${e}`, 'error', 4000)
      ws.close()
      return
    }
  }
  // 线下会议：按配置打开摄像头，仅画面预览（视频不参与纪要，先保证"看得到采到什么"）
  if (!onlineMode.value && payload.videoDeviceId) {
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({
        video: { deviceId: { exact: payload.videoDeviceId } },
        audio: false,
      })
      previewStream.value = cameraStream
    } catch (e) {
      showToast(`摄像头打开失败（不影响录音）：${e}`, 'info', 4000)
      cameraStream = null
    }
  }
  // 先开麦（拿到 onPcm 回调），再 start —— 回调里检查 ws.ready 防竞态；
  // 线上会议时把 display stream 传给 mic：系统声轨混音进同一 PCM 通路
  try {
    await mic.start(
      (bytes) => {
        if (ws.ready && !paused.value) ws.sendPcm(bytes)
      },
      onLevel,
      onlineMode.value ? pendingDisplayStream ?? undefined : undefined,
    )
  } catch (e) {
    const name = (e as DOMException)?.name
    showToast(
      name === 'NotAllowedError'
        ? '麦克风权限被拒绝：请到浏览器设置里允许本页使用麦克风'
        : `无法打开麦克风：${e}`,
      'error',
      5000,
    )
    ws.close()
    return
  }
  ws.start(payload.title, {
    online: onlineMode.value,
    versionMode: versionMode.value,
    sourceMode: payload.sourceMode,
    hotwords: payload.hotwords,
  })
  recording.value = true
  startPreviewDebug()
}

async function onPause() {
  paused.value = true
  await mic.pause()
  ws.pause()
  screenCapture.pause()
}

async function onResume() {
  paused.value = false
  await mic.resume()
  ws.resume()
  const vs = visualSourceStream()
  if (vs && meetingId.value) {
    screenCapture.resume(meetingId.value, vs)
  }
}

async function onStop() {
  ws.stop()
  await mic.stop()
  screenCapture.stop()
  await videoRecorder.stop()  // 等尾片上传完再释放 display stream，防丢最后一分片
  // 会议结束才释放整条 display stream（视频轨提前 stop 会连累系统音频轨）
  pendingDisplayStream?.getTracks().forEach((t) => t.stop())
  pendingDisplayStream = null
  cameraStream?.getTracks().forEach((t) => t.stop())
  cameraStream = null
  previewStream.value = null
  stopPreviewDebug()
  stopElapsed()
  levels.value = []
  // 立即切回「开始记录」，不等服务端 ended（服务端还要 flush + 最终总结，可能十几秒）
  recording.value = false
  paused.value = false
}

// 重放测试：用历史录音当音频源，走完整真实管线（不开麦克风）
async function onReplayStart(sourceId: string, title: string) {
  if (insecure.value) {
    showToast('重放测试需在安全上下文下进行', 'error', 4000)
    return
  }
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  liveAttrs.value = []
  speakerColorMap.clear()
  highlightRange.value = null
  replaying.value = true
  replaySourceId.value = sourceId
  replayFinished.value = false
  replayAudioUrl.value = `/api/meetings/${sourceId}/audio`

  await ws.connect()
  ws.onMessage(handleMessage)
  // 不开麦：直接 start，replay_of 让后端读历史录音按真实节奏喂入
  ws.start(`${title}（重放测试）`, { replayOf: sourceId })
  recording.value = true
}

async function onReplayStop() {
  ws.stop()
  replaying.value = false
  paused.value = false
  stopElapsed()
  replayAudioEl.value?.pause()
}

function onReplayPause() {
  paused.value = true
  replayAudioEl.value?.pause()
  ws.pause()
}

function onReplayResume() {
  paused.value = false
  replayAudioEl.value?.play()
  ws.resume()
}

function exitReplay() {
  // 退出重放视图（ended 后点返回）：清状态回列表
  if (replaying.value) {
    ws.stop()
  }
  replaying.value = false
  paused.value = false
  replayFinished.value = false
  replayAudioEl.value?.pause()
  view.value = 'list'
}

function openDetail(id: string) {
  detailMeetingId.value = id
  view.value = 'detail'
}

async function onReplayRequested(id: string, title: string) {
  view.value = 'live' // 切到 live 视图（replaying=true 时渲染重放视图）
  await onReplayStart(id, title)
}

// 纪要↔转写联动
function onLocateWave(w: { seq: number; startMs?: number; endMs?: number }) {
  if (w.startMs == null) return
  highlightRange.value = { startMs: w.startMs, endMs: w.endMs ?? w.startMs, nonce: Date.now() }
}
function onWaveBarClick(seq: number) {
  activeWaveSeq.value = seq
  activeWaveNonce.value++
}

// ---------- 后端健康探测（侧边栏底部小圆点） ----------
const backendOk = ref<boolean | null>(null)
let healthTimer: ReturnType<typeof setInterval> | null = null

async function pingHealth() {
  try {
    const r = await fetch('/health')
    backendOk.value = r.ok
  } catch {
    backendOk.value = false
  }
}

const navItems = [
  { key: 'live' as View, label: '开会', icon: 'mic' },
  { key: 'list' as View, label: '历史会议', icon: 'history' },
  { key: 'speakers' as View, label: '声纹库', icon: 'users' },
]

onMounted(() => {
  if (typeof navigator !== 'undefined' && navigator.mediaDevices === undefined) {
    insecure.value = true
  }
  pingHealth()
  healthTimer = setInterval(pingHealth, 30000)
})

onBeforeUnmount(() => {
  if (healthTimer) clearInterval(healthTimer)
  stopElapsed()
})
</script>

<template>
  <div class="app-shell">
    <!-- 左侧固定侧边栏 -->
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-logo"><AppIcon name="doc" :size="16" /></span>
        <span class="brand-name">会议纪要</span>
      </div>
      <nav class="nav">
        <button
          v-for="n in navItems"
          :key="n.key"
          class="nav-item"
          :class="{ on: view === n.key || (n.key === 'list' && view === 'detail') }"
          @click="view = n.key"
        >
          <AppIcon :name="n.icon" :size="17" />
          <span>{{ n.label }}</span>
          <span v-if="n.key === 'list' && meetingCount" class="nav-badge num">{{ meetingCount }}</span>
        </button>
      </nav>
      <div class="side-foot">
        <span
          class="health-dot"
          :class="backendOk === null ? 'checking' : backendOk ? 'ok' : 'down'"
        />
        <span class="health-text">
          后端{{ backendOk === null ? '探测中' : backendOk ? '已连接' : '不可用' }}
        </span>
      </div>
    </aside>

    <!-- 主内容区 -->
    <div class="main-col">
      <div v-if="insecure" class="insecure-banner">
        <AppIcon name="warning" :size="15" />
        当前为 http 访问，浏览器禁止使用麦克风。请用 https:// 开头的地址访问（首次有证书警告，点继续访问即可）
      </div>

      <main class="app-main">
        <Transition name="page" mode="out-in">
          <!-- 重放测试视图（历史录音模拟实时开会） -->
          <div v-if="view === 'live' && replaying" key="replay" class="page">
            <div class="replay-banner">
              <span class="chip chip-amber replay-tag">重放测试</span>
              <span class="replay-src num" :title="replaySourceId">{{ replaySourceId }}</span>
              <audio
                v-if="replayAudioUrl"
                ref="replayAudioEl"
                :src="replayAudioUrl"
                controls
                autoplay
                class="replay-audio"
              />
              <div class="replay-actions">
                <template v-if="!replayFinished">
                  <button class="btn btn-sm" @click="paused ? onReplayResume() : onReplayPause()">
                    <AppIcon :name="paused ? 'play' : 'pause'" :size="13" />
                    {{ paused ? '继续' : '暂停' }}
                  </button>
                  <button class="btn btn-sm btn-danger-outline" @click="onReplayStop">
                    <AppIcon name="stop" :size="12" /> 停止
                  </button>
                </template>
                <button v-else class="btn btn-sm" @click="exitReplay">
                  <AppIcon name="back" :size="13" /> 退出重放
                </button>
              </div>
            </div>

            <div class="live-cols">
              <LiveTranscript
                class="col-left"
                :utterances="utterances"
                :waves="liveWaves"
                :speakers="knownSpeakers"
                :color-of="colorOf"
                :highlight="highlightRange"
                @wave-click="onWaveBarClick"
              />
              <div class="col-right">
                <div v-if="liveAttrs.length" class="live-attrs">
                  <span v-for="a in liveAttrs" :key="a.key" class="chip chip-accent">{{ a.key }}：{{ a.value }}</span>
                </div>
                <SummaryPanel
                  :summaries="summaries"
                  :meeting-id="meetingId"
                  :final-progress="finalProgress"
                  :final-progress-text="finalProgressText"
                  :active-seq="activeWaveSeq"
                  :active-nonce="activeWaveNonce"
                  @locate="onLocateWave"
                />
              </div>
            </div>
          </div>

          <!-- 会议记录视图（开会页） -->
          <div v-else-if="view === 'live'" key="live" class="page" :class="{ 'page-idle': !recording }">
            <Recorder
              :recording="recording"
              :paused="paused"
              :insecure="insecure"
              :online="onlineMode"
              :mic-on="mic.micOn"
              :levels="levels"
              :elapsed-sec="elapsedSec"
              @start="onStart"
              @pause="onPause"
              @resume="onResume"
              @stop="onStop"
              @mic-toggle="(on: boolean) => mic.setMicEnabled(on)"
              @goto-speakers="view = 'speakers'"
            />

            <template v-if="recording">
              <!-- 视频画面预览（调试）：线下=摄像头，线上=屏幕内容。muted 防回声 -->
              <div v-if="previewStream" class="card video-preview">
                <div class="vp-head">
                  <AppIcon :name="onlineMode ? 'monitor' : 'camera'" :size="15" />
                  <span class="vp-title">{{ onlineMode ? '屏幕内容' : '摄像头画面' }} 预览</span>
                  <span class="vp-tip">画面仅本地预览调试，不上行</span>
                </div>
                <video :srcObject.prop="previewStream" class="vp-video" autoplay muted playsinline />
                <button class="vp-debug-toggle" @click="previewDebugOpen = !previewDebugOpen">
                  <AppIcon name="chevronDown" :size="13" :class="{ rotated: previewDebugOpen }" />
                  调试详情
                </button>
                <div v-if="previewDebugOpen" class="vp-debug num">{{ previewDebug }}</div>
              </div>

              <div class="live-cols">
                <LiveTranscript
                  class="col-left"
                  :utterances="utterances"
                  :waves="liveWaves"
                  :speakers="knownSpeakers"
                  :color-of="colorOf"
                  :highlight="highlightRange"
                  @wave-click="onWaveBarClick"
                />
                <div class="col-right">
                  <div v-if="liveAttrs.length" class="live-attrs">
                    <span v-for="a in liveAttrs" :key="a.key" class="chip chip-accent">{{ a.key }}：{{ a.value }}</span>
                  </div>
                  <SummaryPanel
                    :summaries="summaries"
                    :meeting-id="meetingId"
                    :final-progress="finalProgress"
                    :final-progress-text="finalProgressText"
                    :active-seq="activeWaveSeq"
                    :active-nonce="activeWaveNonce"
                    @locate="onLocateWave"
                  />
                </div>
              </div>
            </template>
          </div>

          <!-- 历史会议列表 -->
          <MeetingList v-else-if="view === 'list'" key="list" class="page" @open="openDetail" />

          <!-- 声纹管理 -->
          <SpeakerManage v-else-if="view === 'speakers'" key="speakers" class="page" />

          <!-- 会议详情 -->
          <MeetingDetail
            v-else-if="view === 'detail'"
            key="detail"
            class="page"
            :meeting-id="detailMeetingId"
            @back="view = 'list'"
            @replay="onReplayRequested"
          />
        </Transition>
      </main>
    </div>

    <ToastHost />
  </div>
</template>

<style>
.app-shell { height: 100vh; display: flex; overflow: hidden; }

/* ---------- 侧边栏 ---------- */
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: #fff;
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
}
.brand { display: flex; align-items: center; gap: 10px; padding: 4px 10px 18px; }
.brand-logo {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-name { font-size: 15px; font-weight: 600; letter-spacing: 0.5px; }
.nav { display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  font-size: 14px;
  font-family: var(--font);
  color: var(--text-2);
  background: none;
  border: none;
  border-radius: var(--radius-ctl);
  cursor: pointer;
  transition: background 0.15s ease-out, color 0.15s ease-out;
  text-align: left;
}
.nav-item:hover { background: #f2f3f6; color: var(--text-1); }
.nav-item.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
.nav-badge {
  margin-left: auto;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--rec);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}
.side-foot {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px 2px;
  font-size: 12px;
  color: var(--text-3);
}
.health-dot { width: 8px; height: 8px; border-radius: 50%; background: #c3c8d0; }
.health-dot.ok { background: var(--green); box-shadow: 0 0 0 3px rgba(15, 157, 108, 0.15); }
.health-dot.down { background: var(--rec); }
.health-dot.checking { background: #c3c8d0; }

/* ---------- 主内容区 ---------- */
.main-col { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.app-main { flex: 1; overflow-y: auto; }
.page { max-width: 1200px; margin: 0 auto; padding: 28px 32px 48px; }
.page-idle { max-width: 760px; }

.insecure-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 20px;
  font-size: 13px;
  color: #fff;
  background: var(--rec);
}

/* ---------- 开会页双栏（左 7 : 右 5） ---------- */
.live-cols { display: flex; gap: 16px; margin-top: 16px; align-items: stretch; }
.col-left { flex: 7; min-width: 0; }
.col-right { flex: 5; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.live-attrs { display: flex; flex-wrap: wrap; gap: 6px; }

/* ---------- 重放测试横幅 ---------- */
.replay-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: var(--amber-soft);
  border: 1px solid #f3ddb8;
  border-radius: var(--radius-card);
}
.replay-tag { font-weight: 600; }
.replay-src {
  font-size: 12px;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}
.replay-audio { flex: 1; height: 32px; min-width: 160px; }
.replay-actions { display: flex; gap: 8px; }

/* ---------- 视频预览卡 ---------- */
.video-preview { margin-top: 16px; overflow: hidden; }
.vp-head { display: flex; align-items: center; gap: 8px; padding: 10px 14px; color: var(--text-2); }
.vp-title { font-size: 13.5px; font-weight: 600; color: var(--text-1); }
.vp-tip { font-size: 12px; color: var(--text-3); margin-left: auto; }
.vp-video {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  max-height: 340px;
  object-fit: contain;
  background: #0c0d0f;
}
.vp-debug-toggle {
  display: flex;
  align-items: center;
  gap: 5px;
  width: 100%;
  padding: 7px 14px;
  font-size: 12px;
  font-family: var(--font);
  color: var(--text-3);
  background: #fafbfc;
  border: none;
  border-top: 1px solid var(--border);
  cursor: pointer;
}
.vp-debug-toggle:hover { color: var(--text-2); }
.vp-debug-toggle svg { transition: transform 0.18s ease-out; }
.vp-debug-toggle svg.rotated { transform: rotate(180deg); }
.vp-debug {
  padding: 8px 14px;
  font-size: 11.5px;
  color: var(--text-3);
  word-break: break-all;
  background: #fafbfc;
  border-top: 1px solid var(--border);
}

/* 窄屏不破碎即可：双栏堆叠 */
@media (max-width: 960px) {
  .live-cols { flex-direction: column; }
  .page { padding: 20px 16px 40px; }
}
</style>
