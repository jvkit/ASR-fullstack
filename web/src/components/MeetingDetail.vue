<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { showToast } from 'vant'

interface Attr { key: string; value: string }
interface Utterance { seq: number; speaker: string; text: string }
interface ReSeg { seq: number; speaker: string; text: string; start_ms: number; end_ms: number }
interface SummaryRow {
  seq: number
  title: string
  content: string
  action_items: { who: string; what: string; due?: string; status?: string }[]
  attributes: Attr[]
}

const props = defineProps<{ meetingId: string }>()
const emit = defineEmits<{ back: []; replay: [meetingId: string, title: string] }>()

const title = ref('')
const meetingId = ref('')
const attrs = ref<Attr[]>([])
const startedAt = ref('')
const audioUrl = ref('')
const utterances = ref<Utterance[]>([])   // 会中批次版（准实时）
const reSegs = ref<ReSeg[]>([])           // 会末终版（全场重跑，更准）
const reannotated = ref(false)
const reLoading = ref(false)
const summaries = ref<SummaryRow[]>([])
const loading = ref(true)

// 单一转写视图：终版生成后展示终版，未生成前展示会中批次版
const displaySegs = ref<ReSeg[]>([])

const speakerColors = ['#e6646c', '#3aa675', '#3b7fe0', '#a06ee1', '#d98e32', '#2ba0b8', '#c2568c', '#6a8f3c']
const speakerColorMap = new Map<string, string>()
function colorOf(speaker: string): string {
  if (!speakerColorMap.has(speaker)) {
    speakerColorMap.set(speaker, speakerColors[speakerColorMap.size % speakerColors.length])
  }
  return speakerColorMap.get(speaker)!
}

// 录音控件吸顶：滚出视口后 fixed 到导航栏下（同一 audio 元素，播放不中断）
const audioSlotRef = ref<HTMLElement | null>(null)
const audioPinned = ref(false)
let audioObserver: IntersectionObserver | null = null

function setupAudioPin() {
  teardownAudioPin()
  if (!audioSlotRef.value) return
  audioObserver = new IntersectionObserver(
    (entries) => {
      // slot 完全滚出视口上方（在顶部之上）-> 吸顶
      audioPinned.value = entries[0]?.boundingClientRect.bottom! < 46
    },
    { threshold: [0] },
  )
  audioObserver.observe(audioSlotRef.value)
}

function teardownAudioPin() {
  audioObserver?.disconnect()
  audioObserver = null
}

async function load() {
  loading.value = true
  try {
    const [detail, sumResp] = await Promise.all([
      fetch(`/api/meetings/${props.meetingId}`).then((r) => r.json()),
      fetch(`/api/meetings/${props.meetingId}/summaries`).then((r) => r.json()),
    ])
    title.value = detail.title
    meetingId.value = detail.id || props.meetingId
    attrs.value = detail.attributes || []
    startedAt.value = detail.started_at || ''
    audioUrl.value = detail.audio_url || ''
    utterances.value = detail.utterances || []
    reSegs.value = detail.utterances_re || []
    reannotated.value = !!detail.reannotated
    // 终版优先；终版还没跑完（会末异步任务）则先展示会中批次版
    displaySegs.value = reSegs.value.length ? reSegs.value : utterances.value
    summaries.value = (sumResp as SummaryRow[]) || []
    // 等 DOM 渲染出 audio slot 后再挂观察器
    setTimeout(setupAudioPin, 0)
  } finally {
    loading.value = false
  }
}

async function rerunReannotate() {
  reLoading.value = true
  try {
    await fetch(`/api/meetings/${props.meetingId}/reannotate`, { method: 'POST' })
    await load()
  } finally {
    reLoading.value = false
  }
}

function fmtSec(ms: number): string {
  return `${Math.floor(ms / 1000)}s`
}

onMounted(load)
onBeforeUnmount(teardownAudioPin)

async function downloadMarkdown() {
  const resp = await fetch(`/api/meetings/${props.meetingId}/report`)
  const data = await resp.json()
  const blob = new Blob([data.markdown || ''], { type: 'text/markdown' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${title.value || props.meetingId}.md`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="detail" v-loading="loading">
    <div class="detail-head">
      <van-button size="small" plain @click="emit('back')">← 返回</van-button>
      <span class="detail-title">{{ title || '未命名会议' }}</span>
      <van-button size="small" type="primary" @click="downloadMarkdown">下载纪要</van-button>
    </div>

    <van-cell-group inset class="block" title="会议信息">
      <van-cell title="会议 ID" :value="meetingId" />
      <van-cell title="开始时间" :value="startedAt ? new Date(startedAt).toLocaleString('zh-CN') : '-'" />
      <van-cell title="状态" value="已结束" />
      <van-cell v-for="a in attrs" :key="a.key" :title="a.key" :value="a.value" />
    </van-cell-group>

    <van-cell-group inset class="block" title="录音回放">
      <!-- 录音控件：原位播放；滚出屏幕后吸顶固定（同一 audio 元素，播放不中断） -->
      <div v-if="audioUrl" ref="audioSlotRef" class="audio-slot">
        <audio
          ref="audioElRef"
          controls
          :src="audioUrl"
          :class="{ 'audio-el': true, 'audio-el--fixed': audioPinned }"
        ></audio>
      </div>
      <van-empty v-else description="本场会议无录音" image-size="64" />
      <div v-if="audioUrl" class="replay-row">
        <van-button size="small" type="primary" plain block icon="play-circle-o" @click="emit('replay', props.meetingId, title)">
          用本场录音重新测试（重放）
        </van-button>
        <div class="replay-tip">模拟真实开会：同步播放录音 + 批次转写/纠错/总结/会末终版</div>
      </div>
    </van-cell-group>

    <van-cell-group inset class="block" title="转写记录">
      <div class="re-seg-head">
        <van-tag v-if="reannotated" type="success" size="medium">会末终版</van-tag>
        <van-tag v-else type="warning" size="medium" plain>会中批次版</van-tag>
        <van-button size="small" plain type="primary" :loading="reLoading" @click="rerunReannotate">
          重新标注
        </van-button>
        <span class="re-seg-tip">{{ reannotated ? '全场重跑，边界与说话人最准' : '终版生成中或失败时可手动重跑' }}</span>
      </div>
      <div v-if="displaySegs.length" class="re-list">
        <div v-for="s in displaySegs" :key="s.seq" class="re-item">
          <van-tag
            plain
            class="spk-tag"
            :style="{ borderColor: colorOf(s.speaker), color: colorOf(s.speaker) }"
          >
            {{ s.speaker || '未识别' }}
          </van-tag>
          <span class="utt-text">
            <span class="re-time">{{ fmtSec(s.start_ms) }}-{{ fmtSec(s.end_ms) }} </span>{{ s.text || '（无转写）' }}
          </span>
        </div>
      </div>
      <van-empty v-else description="本场会议没有有效转写" image-size="64" />
    </van-cell-group>

    <van-cell-group inset class="block" title="总结">
      <div class="sums">
        <div v-for="s in summaries" :key="s.seq" class="sum-item">
          <div class="sum-head">
            <van-tag :type="s.seq === 0 ? 'primary' : 'success'">{{ s.seq === 0 ? '最终总结' : `第 ${s.seq} 段` }}</van-tag>
            <span class="sum-title">{{ s.title || `段落 ${s.seq}` }}</span>
          </div>
          <div class="sum-content" v-html="s.content"></div>
          <div v-if="s.attributes.length" class="tags">
            <van-tag v-for="a in s.attributes" :key="a.key" plain type="primary" class="tag">
              {{ a.key }}：{{ a.value }}
            </van-tag>
          </div>
          <div v-if="s.action_items.length" class="tags">
            <van-tag v-for="(a, i) in s.action_items" :key="i" plain type="warning" class="tag">
              {{ a.who }}：{{ a.what }}{{ a.due ? `（${a.due}）` : '' }}
            </van-tag>
          </div>
        </div>
        <van-empty v-if="!summaries.length" description="暂无总结" image-size="64" />
      </div>
    </van-cell-group>
  </div>
</template>

<style scoped>
.detail { padding-bottom: 16px; }
.detail-head { display: flex; align-items: center; gap: 8px; padding: 4px 8px 8px; }
.detail-title { flex: 1; font-weight: 600; font-size: 15px; word-break: break-word; }
.block { margin-top: 12px; }
.audio-slot { min-height: 40px; }
.audio-el { width: 100%; }
.audio-el--fixed {
  position: fixed;
  top: 46px; /* van-nav-bar 高度 */
  left: 16px;
  right: 16px;
  z-index: 100;
  background: #fff;
  padding: 8px 0;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
.replay-row { padding: 0 16px 12px; }
.replay-tip { font-size: 12px; color: #969799; margin-top: 6px; text-align: center; }
.re-seg-head { padding: 4px 16px; display: flex; align-items: center; }
.re-seg-tip { font-size: 13px; color: #646566; }
.re-list { padding: 8px 16px; }
.re-item { margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px; }
.re-time { color: #969799; font-size: 12px; }
.utts { padding: 8px 16px; }
.utt { margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px; }
.utt-text { flex: 1; word-break: break-word; }
.spk-tag { flex-shrink: 0; }
.sums { padding: 8px 16px; }
.sum-item { margin-bottom: 16px; }
.sum-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.sum-title { font-weight: 600; font-size: 14px; }
.sum-content { font-size: 14px; }
.tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { margin: 0; }
</style>
