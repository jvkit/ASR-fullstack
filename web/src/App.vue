<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { showToast } from 'vant'

import LiveTranscript from './components/LiveTranscript.vue'
import MeetingDetail from './components/MeetingDetail.vue'
import MeetingList from './components/MeetingList.vue'
import Recorder from './components/Recorder.vue'
import SpeakerManage from './components/SpeakerManage.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import { MicRecorder } from './ws/audio'
import { ScreenFrameCapture } from './ws/screen'
import { MeetingWs, type ServerMessage, type SourceMode, type VersionMode } from './ws/client'

interface Utterance {
  seq: number
  speaker: string
  text: string
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

type View = 'live' | 'list' | 'detail' | 'speakers'

const view = ref<View>('live')
const detailMeetingId = ref('')
const meetingCount = ref(0) // 列表页有新会议时点亮 tab 角标
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

const recording = ref(false)
const paused = ref(false)
const meetingId = ref('')
const utterances = ref<Utterance[]>([])
const summaries = ref<SummaryItem[]>([])
const liveAttrs = ref<{ key: string; value: string }[]>([]) // 实时提取的事实性属性
const errorMsg = ref('')

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
}

// 说话人配色（按出现顺序循环）
const speakerColors = ['#e6646c', '#3aa675', '#3b7fe0', '#a06ee1', '#d98e32', '#2ba0b8', '#c2568c', '#6a8f3c']
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
      // 视觉抽帧上行：线上=屏幕帧，线下=摄像头帧（帧带 meeting_id 送 OCR）
      {
        const vs = visualSourceStream()
        if (vs) {
          // 用户在浏览器分享条上手动取消分享：video 轨 ended → 只停抽帧，音频不动
          vs.getVideoTracks()[0]?.addEventListener('ended', () => {
            screenCapture.stop()
          })
          screenCapture.start(vs, msg.meeting_id).catch(() => {})
        }
      }
      break
    case 'transcript':
      utterances.value.push({ seq: msg.seq, speaker: msg.speaker || '发言人', text: msg.text, draft: true })
      colorOf(msg.speaker || '发言人')
      break
    case 'transcript_update': {
      // 滚动打标/纠错回写：按 seq 原地替换说话人与文本
      // corrected=true → 定稿（去草稿样式）；false → 仅换说话人标签（打标），文字仍是草稿
      const idx = utterances.value.findIndex((u) => u.seq === msg.seq)
      if (idx >= 0) {
        const draft = msg.corrected ? false : utterances.value[idx].draft
        utterances.value[idx] = { seq: msg.seq, speaker: msg.speaker || '发言人', text: msg.text, draft }
        colorOf(msg.speaker || '发言人')
      }
      break
    }
    case 'summary': {
      // 按 (scope, seq, final) upsert：段纪要可被下一波订正（服务端会重发同 seq），
      // 无脑 push 会让订正显示成重复条目。会中视图只收 live scope，final 防御性丢弃
      if (msg.scope === 'final') break
      const row = {
        seq: msg.seq,
        scope: msg.scope || 'live',
        title: msg.title,
        points: msg.points,
        actionItems: msg.action_items as SummaryItem['actionItems'],
        attributes: msg.attributes as SummaryItem['attributes'],
        final: msg.final,
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
      showToast({ message: '会末版已生成，可在历史会议查看', type: 'success', duration: 3000 })
      meetingCount.value++
      break
    case 'attributes':
      liveAttrs.value = msg.attributes || []
      break
    case 'ended':
      recording.value = false
      paused.value = false
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
      showToast({ message: '会末终版转写已生成，可在历史会议查看', type: 'success', duration: 3000 })
      break
    case 'error':
      errorMsg.value = msg.message
      showToast({ message: msg.message, type: 'fail', duration: 4000 })
      break
  }
}

async function onStart(payload: StartConfig) {
  if (insecure.value) {
    showToast({ message: 'http 访问下浏览器禁止麦克风，请用 https 地址打开', type: 'fail', duration: 4000 })
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
      showToast({ message: `屏幕分享被取消或失败：${e}`, type: 'fail', duration: 4000 })
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
      showToast({ message: `摄像头打开失败（不影响录音）：${e}`, type: 'warning', duration: 4000 })
      cameraStream = null
    }
  }
  // 先开麦（拿到 onPcm 回调），再 start —— 回调里检查 ws.ready 防竞态；
  // 线上会议时把 display stream 传给 mic：系统声轨混音进同一 PCM 通路
  try {
    await mic.start((bytes) => {
      if (ws.ready && !paused.value) ws.sendPcm(bytes)
    }, undefined, onlineMode.value ? pendingDisplayStream ?? undefined : undefined)
  } catch (e) {
    const name = (e as DOMException)?.name
    showToast({
      message:
        name === 'NotAllowedError'
          ? '麦克风权限被拒绝：请到浏览器设置里允许本页使用麦克风'
          : `无法打开麦克风：${e}`,
      type: 'fail',
      duration: 5000,
    })
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
  // 会议结束才释放整条 display stream（视频轨提前 stop 会连累系统音频轨）
  pendingDisplayStream?.getTracks().forEach((t) => t.stop())
  pendingDisplayStream = null
  cameraStream?.getTracks().forEach((t) => t.stop())
  cameraStream = null
  previewStream.value = null
  stopPreviewDebug()
  // 立即切回「开始记录」，不等服务端 ended（服务端还要 flush + 最终总结，可能十几秒）
  recording.value = false
  paused.value = false
}

// 重放测试：用历史录音当音频源，走完整真实管线（不开麦克风）
async function onReplayStart(sourceId: string, title: string) {
  if (insecure.value) {
    showToast({ message: '重放测试需在安全上下文下进行', type: 'fail', duration: 4000 })
    return
  }
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  liveAttrs.value = []
  speakerColorMap.clear()
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

onMounted(() => {
  if (typeof navigator !== 'undefined' && navigator.mediaDevices === undefined) {
    insecure.value = true
  }
})
</script>

<template>
  <div class="app-shell">
    <!-- 桌面端：顶部导航；移动端：顶部仅标题，导航在底部 tabbar -->
    <van-nav-bar :title="view === 'detail' ? '会议详情' : '会议纪要'" />

    <van-notice-bar
      v-if="insecure"
      mode="closeable"
      color="#fff"
      background="#e64340"
      wrapable
      :scrollable="false"
      text="当前为 http 访问，浏览器禁止使用麦克风。请用 https:// 开头的地址访问（首次有证书警告，点继续访问即可）"
    />

    <!-- 线上会议麦克风门控状态横幅（2026-09-28 P3：漏开 = 自己声音全丢，必须显眼） -->
    <van-notice-bar
      v-if="recording && onlineMode"
      :scrollable="false"
      :wrapable="!mic.micOn"
      color="#fff"
      :background="mic.micOn ? '#07c160' : '#ed6a0c'"
      :left-icon="mic.micOn ? 'volume-o' : 'warning-o'"
      :text="mic.micOn
        ? '麦克风上行中：对方能听到你，你的发言会进入转写'
        : '闭麦中：对方听不到你的声音，转写将缺失你的发言——在下方打开「我开麦了」'"
    />

    <main class="app-main">
      <!-- 重放测试视图（历史录音模拟实时开会） -->
      <template v-if="view === 'live' && replaying">
        <div class="replay-bar">
          <van-tag type="danger" size="large">重放测试</van-tag>
          <span class="replay-title">{{ replaySourceId }}</span>
          <van-button v-if="!replayFinished" size="small" type="warning" plain @click="paused ? onReplayResume() : onReplayPause()">
            {{ paused ? '继续' : '暂停' }}
          </van-button>
          <van-button v-if="!replayFinished" size="small" type="danger" plain @click="onReplayStop">■ 停止</van-button>
          <van-button v-else size="small" plain @click="exitReplay">退出重放</van-button>
        </div>
        <audio
          v-if="replayAudioUrl"
          ref="replayAudioEl"
          :src="replayAudioUrl"
          controls
          autoplay
          class="replay-audio"
          @ended="() => {}"
        />
        <div v-if="liveAttrs.length" class="live-attrs">
          <van-tag v-for="a in liveAttrs" :key="a.key" plain type="primary" class="attr-tag">
            {{ a.key }}：{{ a.value }}
          </van-tag>
        </div>
        <div v-if="finalProgress" class="final-progress">
          <span class="fp-text">会末版生成中：{{ FINAL_STAGE_TEXT[finalProgress.stage] || finalProgress.stage }}</span>
          <van-progress :percentage="finalProgress.pct" :pivot-text="`${finalProgress.pct}%`" stroke-width="6" />
        </div>
        <div class="live-panels">
          <LiveTranscript :utterances="utterances" :speakers="knownSpeakers" :color-of="colorOf" />
          <SummaryPanel :summaries="summaries" :meeting-id="meetingId" />
        </div>
      </template>

      <!-- 会议记录视图（批次转写） -->
      <template v-else-if="view === 'live'">
        <Recorder
          :recording="recording"
          :paused="paused"
          :insecure="insecure"
          :online="onlineMode"
          :mic-on="mic.micOn"
          @start="onStart"
          @pause="onPause"
          @resume="onResume"
          @stop="onStop"
          @mic-toggle="(on: boolean) => mic.setMicEnabled(on)"
          @goto-speakers="view = 'speakers'"
        />

        <!-- 视频画面预览（调试）：线下=摄像头，线上=屏幕内容。muted 防回声 -->
        <div v-if="recording && previewStream" class="video-preview">
          <div class="vp-head">
            <van-tag :type="onlineMode ? 'primary' : 'warning'" size="medium">
              {{ onlineMode ? '屏幕内容' : '摄像头画面' }}
            </van-tag>
            <span class="vp-tip">画面仅本地预览调试，不上行</span>
          </div>
          <video :srcObject.prop="previewStream" class="vp-video" autoplay muted playsinline />
          <div class="vp-debug">{{ previewDebug }}</div>
        </div>

        <div v-if="liveAttrs.length" class="live-attrs">
          <van-tag
            v-for="a in liveAttrs"
            :key="a.key"
            plain
            type="primary"
            class="attr-tag"
          >
            {{ a.key }}：{{ a.value }}
          </van-tag>
        </div>

        <div v-if="finalProgress" class="final-progress">
          <span class="fp-text">会末版生成中：{{ FINAL_STAGE_TEXT[finalProgress.stage] || finalProgress.stage }}</span>
          <van-progress :percentage="finalProgress.pct" :pivot-text="`${finalProgress.pct}%`" stroke-width="6" />
        </div>

        <div class="live-panels">
          <LiveTranscript
            :utterances="utterances"
            :speakers="knownSpeakers"
            :color-of="colorOf"
          />
          <SummaryPanel :summaries="summaries" :meeting-id="meetingId" />
        </div>
      </template>

      <!-- 历史会议列表 -->
      <MeetingList v-else-if="view === 'list'" @open="openDetail" />

      <!-- 声纹管理 -->
      <SpeakerManage v-else-if="view === 'speakers'" />

      <!-- 会议详情 -->
      <MeetingDetail
        v-else-if="view === 'detail'"
        :meeting-id="detailMeetingId"
        @back="view = 'list'"
        @replay="onReplayRequested"
      />
    </main>

    <!-- 移动端底部导航（桌面端隐藏，桌面用顶部 radio-group） -->
    <van-tabbar v-model="view" class="mobile-tabbar">
      <van-tabbar-item name="live" icon="edit">会议记录</van-tabbar-item>
      <van-tabbar-item name="speakers" icon="contact">声纹管理</van-tabbar-item>
      <van-tabbar-item name="list" icon="records" :badge="meetingCount || ''">历史会议</van-tabbar-item>
    </van-tabbar>
  </div>
</template>

<style>
html, body, #app { height: 100%; margin: 0; }
.app-shell { height: 100vh; display: flex; flex-direction: column; }
.app-main { flex: 1; overflow-y: auto; padding: 8px; }

/* 移动端：单列布局 */
.live-panels { display: flex; flex-direction: column; gap: 8px; }
.live-attrs { padding: 4px 2px; display: flex; flex-wrap: wrap; gap: 6px; }
.attr-tag { margin: 0; }

/* 桌面端（≥768px）：恢复左右分栏 + 顶部 radio 导航 + 隐藏 tabbar */
@media (min-width: 768px) {
  .mobile-tabbar { display: none; }
  .app-main { padding: 16px; }
  .live-panels { flex-direction: row; }
  .live-panels > :first-child { flex: 7; }
  .live-panels > :last-child { flex: 5; }
}

/* 重放测试 */
.replay-bar { display: flex; align-items: center; gap: 8px; padding: 4px 2px 8px; }
.replay-title { flex: 1; font-size: 13px; color: #646566; word-break: break-all; }
.replay-audio { width: 100%; margin-bottom: 8px; }

/* 会末版重跑进度（会中视图轻量条） */
.final-progress { padding: 4px 2px 8px; }
.fp-text { display: block; font-size: 12px; color: #646566; margin-bottom: 4px; }

/* 视频画面预览（调试） */
.video-preview { margin: 8px 0; border: 1px solid #ebedf0; border-radius: 8px; overflow: hidden; background: #000; }
.vp-head { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: #fafafa; }
.vp-tip { font-size: 12px; color: #969799; }
.vp-video { display: block; width: 100%; max-height: 260px; object-fit: contain; }
.vp-debug { padding: 4px 8px; font-size: 11px; color: #969799; word-break: break-all; background: #fafafa; font-variant-numeric: tabular-nums; }

</style>
