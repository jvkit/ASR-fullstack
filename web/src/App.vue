<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import LiveTranscript from './components/LiveTranscript.vue'
import Recorder from './components/Recorder.vue'
import SummaryPanel from './components/SummaryPanel.vue'
import { MicRecorder } from './ws/audio'
import { MeetingWs, type ServerMessage } from './ws/client'

interface Utterance {
  seq: number
  speaker: string
  text: string
}

interface SummaryItem {
  seq: number
  title: string
  points: string[]
  actionItems: { who: string; what: string; due?: string; status?: string }[]
  final: boolean
}

const recording = ref(false)
const meetingId = ref('')
const onlineText = ref('') // 流式临时字幕
const utterances = ref<Utterance[]>([])
const summaries = ref<SummaryItem[]>([])
const errorMsg = ref('')

const mic = reactive(new MicRecorder())
const ws = reactive(new MeetingWs())

// 说话人配色（spk0..spk7 循环）
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
      onlineText.value = msg.text
      break
    case 'transcript':
      onlineText.value = ''
      utterances.value.push({ seq: msg.seq, speaker: msg.speaker || '发言人', text: msg.text })
      colorOf(msg.speaker || '发言人')
      break
    case 'summary':
      summaries.value.push({
        seq: msg.seq,
        title: msg.title,
        points: msg.points,
        actionItems: msg.action_items as SummaryItem['actionItems'],
        final: msg.final,
      })
      break
    case 'ended':
      recording.value = false
      break
    case 'error':
      errorMsg.value = msg.message
      break
  }
}

async function onStart(payload: { title: string; recorder: string }) {
  errorMsg.value = ''
  utterances.value = []
  summaries.value = []
  onlineText.value = ''
  speakerColorMap.clear()

  await ws.connect()
  ws.onMessage(handleMessage)
  // 先开麦（拿到 onPcm 回调），再 start —— 回调里检查 ws.ready 防竞态
  await mic.start((bytes) => {
    if (ws.ready) ws.sendPcm(bytes)
  })
  ws.start(payload.title, payload.recorder)
  recording.value = true
}

async function onStop() {
  ws.stop()
  await mic.stop()
  // 等服务端 ended 消息把 recording 置 false
}
</script>

<template>
  <el-container style="height: 100vh">
    <el-header style="border-bottom: 1px solid #eee; display: flex; align-items: center">
      <h2 style="margin: 0; flex: 1">会议实时纪要</h2>
      <el-alert
        v-if="errorMsg"
        :title="errorMsg"
        type="error"
        :closable="false"
        style="max-width: 400px"
      />
    </el-header>

    <el-main>
      <Recorder :recording="recording" @start="onStart" @stop="onStop" />

      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :span="14">
          <LiveTranscript
            :utterances="utterances"
            :online-text="onlineText"
            :speakers="knownSpeakers"
            :color-of="colorOf"
          />
        </el-col>
        <el-col :span="10">
          <SummaryPanel :summaries="summaries" :meeting-id="meetingId" />
        </el-col>
      </el-row>
    </el-main>
  </el-container>
</template>
