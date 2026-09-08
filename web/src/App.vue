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
import { MeetingWs, type ServerMessage } from './ws/client'

interface Utterance {
  seq: number
  speaker: string
  text: string
  draft: boolean // 草稿：原始转写（未纠错定稿），收到 corrected update 后变正式
}

interface SummaryItem {
  seq: number
  title: string
  points: string[]
  actionItems: { who: string; what: string; due?: string; status?: string }[]
  attributes: { key: string; value: string }[]
  final: boolean
}

type View = 'live' | 'list' | 'detail' | 'speakers'

const view = ref<View>('live')
const detailMeetingId = ref('')
const meetingCount = ref(0) // 列表页有新会议时点亮 tab 角标
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

const recording = ref(false)
const paused = ref(false)
const meetingId = ref('')
const onlineText = ref('') // 流式临时字幕
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
      break
    case 'transcript_online':
      if (!paused.value) onlineText.value = msg.text
      break
    case 'transcript':
      onlineText.value = ''
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
    case 'summary':
      summaries.value.push({
        seq: msg.seq,
        title: msg.title,
        points: msg.points,
        actionItems: msg.action_items as SummaryItem['actionItems'],
        attributes: msg.attributes as SummaryItem['attributes'],
        final: msg.final,
      })
      break
    case 'attributes':
      liveAttrs.value = msg.attributes || []
      break
    case 'ended':
      recording.value = false
      paused.value = false
      if (replaying.value) {
        replayFinished.value = true
      }
      meetingCount.value++
      break
    case 'error':
      errorMsg.value = msg.message
      showToast({ message: msg.message, type: 'fail', duration: 4000 })
      break
  }
}

async function onStart(payload: {
  title: string
  spkThreshold: number
  spkMatchThreshold: number
  reannotateNameThreshold: number
  speakerCount: number
  maxSpeakers: number
  vadMaxEndSilenceMs: number
  vadMaxSegmentSec: number
  spkMinSegSec: number
}) {
  if (insecure.value) {
    showToast({ message: 'http 访问下浏览器禁止麦克风，请用 https 地址打开', type: 'fail', duration: 4000 })
    return
  }
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  onlineText.value = ''
  liveAttrs.value = []
  speakerColorMap.clear()

  await ws.connect()
  ws.onMessage(handleMessage)
  // 先开麦（拿到 onPcm 回调），再 start —— 回调里检查 ws.ready 防竞态
  try {
    await mic.start((bytes) => {
      if (ws.ready && !paused.value) ws.sendPcm(bytes)
    })
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
    spkThreshold: payload.spkThreshold,
    spkMatchThreshold: payload.spkMatchThreshold,
    reannotateNameThreshold: payload.reannotateNameThreshold,
    speakerCount: payload.speakerCount || undefined,
    maxSpeakers: payload.maxSpeakers || undefined,
    vadMaxEndSilenceMs: payload.vadMaxEndSilenceMs,
    vadMaxSegmentSec: payload.vadMaxSegmentSec,
    spkMinSegSec: payload.spkMinSegSec,
  })
  recording.value = true
}

async function onPause() {
  paused.value = true
  await mic.pause()
  ws.pause()
}

async function onResume() {
  paused.value = false
  await mic.resume()
  ws.resume()
}

async function onStop() {
  ws.stop()
  await mic.stop()
  // 立即切回「开始记录」，不等服务端 ended（服务端还要 flush + 最终总结，可能十几秒）
  recording.value = false
  paused.value = false
}

// 重放测试：用历史录音当音频源，走完整实时管线（不开麦克风）
async function onReplayStart(sourceId: string, title: string) {
  if (insecure.value) {
    showToast({ message: '重放测试需在安全上下文下进行', type: 'fail', duration: 4000 })
    return
  }
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  onlineText.value = ''
  liveAttrs.value = []
  speakerColorMap.clear()
  replaying.value = true
  replaySourceId.value = sourceId
  replayFinished.value = false
  replayAudioUrl.value = `/api/meetings/${sourceId}/audio`

  await ws.connect()
  ws.onMessage(handleMessage)
  // 不开麦：直接 start，replay_of 让后端读历史录音喂 ASR
  // 参数唯一来源 /api/config（与 config.py 一致）；speakerCount=2 为重放测试固定
  const cfg = await fetch('/api/config').then((r) => r.json()).catch(() => ({}))
  ws.start(`${title}（重放测试）`, {
    speakerCount: 2, // 重放测试固定两人
    spkThreshold: Number(cfg?.spk_threshold ?? 0.55),
    spkMatchThreshold: Number(cfg?.spk_match_threshold ?? 0.40),
    reannotateNameThreshold: Number(cfg?.reannotate_name_threshold ?? 0.30),
    maxSpeakers: Number(cfg?.max_speakers ?? 0),
    vadMaxEndSilenceMs: Number(cfg?.vad_max_end_silence_ms ?? 350),
    vadMaxSegmentSec: Number(cfg?.vad_max_segment_sec ?? 8),
    spkMinSegSec: Number(cfg?.spk_min_seg_sec ?? 1.0),
    replayOf: sourceId,
  })
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
    <van-nav-bar :title="view === 'detail' ? '会议详情' : '会议实时纪要'" />

    <van-notice-bar
      v-if="insecure"
      mode="closeable"
      color="#fff"
      background="#e64340"
      wrapable
      :scrollable="false"
      text="当前为 http 访问，浏览器禁止使用麦克风。请用 https:// 开头的地址访问（首次有证书警告，点继续访问即可）"
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
        <div class="live-panels">
          <LiveTranscript :utterances="utterances" :online-text="onlineText" :speakers="knownSpeakers" :color-of="colorOf" />
          <SummaryPanel :summaries="summaries" :meeting-id="meetingId" />
        </div>
      </template>

      <!-- 实时记录视图 -->
      <template v-else-if="view === 'live'">
        <Recorder :recording="recording" :paused="paused" :insecure="insecure" @start="onStart" @pause="onPause" @resume="onResume" @stop="onStop" />

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

        <div class="live-panels">
          <LiveTranscript
            :utterances="utterances"
            :online-text="onlineText"
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
      <van-tabbar-item name="live" icon="edit">实时记录</van-tabbar-item>
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

</style>
