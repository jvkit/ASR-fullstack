<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

interface Attr { key: string; value: string }
interface Utterance { seq: number; speaker: string; text: string; start_ms: number; end_ms: number }
interface SummaryRow {
  seq: number
  scope: string
  title: string
  content: string
  action_items: { task?: string; assignee?: string; due?: string; quote?: string; who?: string; what?: string }[]
  attributes: Attr[]
}
interface ActionItem { task: string; assignee: string; due: string; quote: string }

const props = defineProps<{ meetingId: string }>()
const emit = defineEmits<{ back: []; replay: [meetingId: string, title: string] }>()

const title = ref('')
const meetingId = ref('')
const attrs = ref<Attr[]>([])
const status = ref('')
const versionMode = ref<'live' | 'final' | 'both'>('both') // 旧行缺省 both（后端列默认同为 both）
const startedAt = ref('')
const rawAudioUrl = ref('')
const audioMode = ref<'denoised' | 'raw'>('denoised')  // 回放默认降噪版；原始版为存档生肉
const audioUrl = computed(() =>
  audioMode.value === 'raw' ? rawAudioUrl.value : `/api/meetings/${props.meetingId}/audio_denoised`
)
const utterances = ref<Utterance[]>([])   // 实时版（会中批次，草稿态）
const reSegs = ref<Utterance[]>([])       // 会末版（全场重跑，权威态）
const reannotated = ref(false)
const reLoading = ref(false)
const liveSummaries = ref<SummaryRow[]>([])
const finalSummaries = ref<SummaryRow[]>([])
const actionItems = ref<ActionItem[]>([]) // 权威待办清单（会末版聚合；live-only 时为实时版聚合）
const loading = ref(true)

// 双版本 tab（2026-09-28）：live/final 只显示对应一个，both 两个都显示
const activeTab = ref<'live' | 'final'>('live')
const showLive = computed(() => versionMode.value !== 'final')
const showFinal = computed(() => versionMode.value !== 'live')

// 会末版重跑中：status=ended 且终版未落库。WS 推送详情页不一定收得到，轮询兜底（简单优先）
const finalRunning = computed(() => showFinal.value && status.value === 'ended' && !reannotated.value)
let pollTimer: ReturnType<typeof setInterval> | null = null

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/meetings/${props.meetingId}`)
      const d = await r.json()
      if (d.reannotated) {
        stopPoll()
        await load()
      }
    } catch { /* 下次再探 */ }
  }, 3000)
}
function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}
watch(finalRunning, (v) => (v ? startPoll() : stopPoll()))

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

// 待办兼容两种形状：新结构化 {task,assignee,due,quote}（2026-09-28）/ 旧 {who,what,due,status}
function normActions(list: unknown): ActionItem[] {
  if (!Array.isArray(list)) return []
  return list
    .filter((a): a is Record<string, unknown> => !!a && typeof a === 'object')
    .map((a) => ({
      task: String(a.task || a.what || '').trim(),
      assignee: String(a.assignee || a.who || '').trim(),
      due: String(a.due || '').trim(),
      quote: String(a.quote || '').trim(),
    }))
    .filter((a) => a.task || a.quote)
}

async function load() {
  loading.value = true
  try {
    const [detail, liveSum, finalSum] = await Promise.all([
      fetch(`/api/meetings/${props.meetingId}`).then((r) => r.json()),
      fetch(`/api/meetings/${props.meetingId}/summaries?scope=live`).then((r) => r.json()),
      fetch(`/api/meetings/${props.meetingId}/summaries?scope=final`).then((r) => r.json()),
    ])
    title.value = detail.title
    meetingId.value = detail.id || props.meetingId
    attrs.value = detail.attributes || []
    status.value = detail.status || ''
    versionMode.value = ['live', 'final', 'both'].includes(detail.version_mode)
      ? detail.version_mode
      : 'both'
    activeTab.value = versionMode.value === 'final' ? 'final' : 'live'
    startedAt.value = detail.started_at || ''
    rawAudioUrl.value = detail.audio_url || ''
    utterances.value = detail.utterances || []
    reSegs.value = detail.utterances_re || []
    reannotated.value = !!detail.reannotated
    liveSummaries.value = (liveSum as SummaryRow[]) || []
    finalSummaries.value = (finalSum as SummaryRow[]) || []
    actionItems.value = normActions(detail.action_items)
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

function segText(s: Utterance): string {
  return s.text || '（无转写）'
}

onMounted(load)
onBeforeUnmount(() => {
  teardownAudioPin()
  stopPoll()
})

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
      <van-cell title="状态" :value="status === 'live' ? '进行中' : '已结束'" />
      <van-cell title="版本模式" :value="versionMode === 'both' ? '实时 + 会末' : versionMode === 'live' ? '只实时版' : '只会末版'" />
      <van-cell v-for="a in attrs" :key="a.key" :title="a.key" :value="a.value" />
    </van-cell-group>

    <van-cell-group inset class="block" title="录音回放">
      <!-- 双版本切换：降噪版=与识别/声纹同一份 GTCRN 处理（默认）；原始版=存档生肉。
           播放器在 tab 外，两个版本 tab 各自可用 -->
      <div v-if="audioUrl" class="audio-mode">
        <span :class="{ on: audioMode === 'denoised' }" @click="audioMode = 'denoised'">降噪版</span>
        <span :class="{ on: audioMode === 'raw' }" @click="audioMode = 'raw'">原始版</span>
      </div>
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

    <van-tabs v-model:active="activeTab" class="version-tabs">
      <!-- ============ 实时版本（会中批次，草稿态） ============ -->
      <van-tab v-if="showLive" title="实时版本" name="live">
        <div class="cols">
          <div class="col col-main">
            <van-cell-group inset class="block" title="转写记录（实时版）">
              <div class="re-seg-head">
                <van-tag type="warning" size="medium" plain>会中批次版</van-tag>
                <span class="re-seg-tip">会中逐批出稿，文字经波次纠错但未经会末全量重跑</span>
              </div>
              <div v-if="utterances.length" class="re-list">
                <div v-for="s in utterances" :key="s.seq" class="re-item">
                  <van-tag
                    plain
                    class="spk-tag"
                    :style="{ borderColor: colorOf(s.speaker), color: colorOf(s.speaker) }"
                  >
                    {{ s.speaker || '未识别' }}
                  </van-tag>
                  <span class="utt-text">
                    <span class="re-time">{{ fmtSec(s.start_ms) }}-{{ fmtSec(s.end_ms) }} </span>{{ segText(s) }}
                  </span>
                </div>
              </div>
              <van-empty v-else description="本场会议没有实时版转写" image-size="64" />
            </van-cell-group>
          </div>
          <div class="col col-side">
            <van-cell-group inset class="block" title="总结（实时版）">
              <div class="sums">
                <div v-for="s in liveSummaries" :key="`live-${s.seq}`" class="sum-item">
                  <div class="sum-head">
                    <van-tag :type="s.seq === 0 ? 'primary' : 'success'">{{ s.seq === 0 ? '实时版终版' : `第 ${s.seq} 段` }}</van-tag>
                    <span class="sum-title">{{ s.title || `段落 ${s.seq}` }}</span>
                  </div>
                  <div class="sum-content" v-html="s.content"></div>
                  <div v-if="s.attributes.length" class="tags">
                    <van-tag v-for="a in s.attributes" :key="a.key" plain type="primary" class="tag">
                      {{ a.key }}：{{ a.value }}
                    </van-tag>
                  </div>
                </div>
                <van-empty v-if="!liveSummaries.length" description="暂无实时版总结" image-size="64" />
              </div>
            </van-cell-group>
          </div>
        </div>
      </van-tab>

      <!-- ============ 会末版本（全场重跑，权威态） ============ -->
      <van-tab v-if="showFinal" title="会末版本" name="final">
        <div class="cols">
          <div class="col col-main">
            <van-cell-group inset class="block" title="转写记录（会末版）">
              <div class="re-seg-head">
                <van-tag v-if="reannotated" type="success" size="medium">会末终版</van-tag>
                <van-tag v-else-if="finalRunning" type="warning" size="medium" plain>生成中</van-tag>
                <van-tag v-else type="warning" size="medium" plain>未生成</van-tag>
                <van-button size="small" plain type="primary" :loading="reLoading" @click="rerunReannotate">
                  重新标注
                </van-button>
                <span class="re-seg-tip">{{ reannotated ? '全场重跑，边界与说话人最准' : '终版生成中或失败时可手动重跑' }}</span>
              </div>
              <!-- 会末版重跑进度：WS 推送详情页不一定收得到，轮询 reannotated 翻转自动刷新 -->
              <div v-if="finalRunning" class="final-box">
                <van-loading size="20px" color="#1989fa">会末版生成中（全场重跑 → 波次纪要 → 会末总结），完成后本页自动刷新…</van-loading>
              </div>
              <div v-if="reSegs.length" class="re-list">
                <div v-for="s in reSegs" :key="s.seq" class="re-item">
                  <van-tag
                    plain
                    class="spk-tag"
                    :style="{ borderColor: colorOf(s.speaker), color: colorOf(s.speaker) }"
                  >
                    {{ s.speaker || '未识别' }}
                  </van-tag>
                  <span class="utt-text">
                    <span class="re-time">{{ fmtSec(s.start_ms) }}-{{ fmtSec(s.end_ms) }} </span>{{ segText(s) }}
                  </span>
                </div>
              </div>
              <van-empty v-else-if="!finalRunning" description="本场会议没有会末版转写" image-size="64" />
            </van-cell-group>
          </div>
          <div class="col col-side">
            <van-cell-group inset class="block" title="总结（会末版）">
              <div class="sums">
                <div v-for="s in finalSummaries" :key="`final-${s.seq}`" class="sum-item">
                  <div class="sum-head">
                    <van-tag :type="s.seq === 0 ? 'primary' : 'success'">{{ s.seq === 0 ? '会末总结' : `第 ${s.seq} 段` }}</van-tag>
                    <span class="sum-title">{{ s.title || `段落 ${s.seq}` }}</span>
                  </div>
                  <div class="sum-content" v-html="s.content"></div>
                </div>
                <van-empty v-if="!finalSummaries.length" description="暂无会末版总结" image-size="64" />
              </div>
            </van-cell-group>

            <van-cell-group inset class="block" title="待办清单">
              <div v-if="actionItems.length" class="action-list">
                <div v-for="(a, i) in actionItems" :key="i" class="action-item">
                  <div class="action-task">{{ a.task || '（未命名任务）' }}</div>
                  <div class="action-meta">
                    <van-tag v-if="a.assignee" plain type="primary" size="medium" class="tag">责任人：{{ a.assignee }}</van-tag>
                    <van-tag v-if="a.due" plain type="warning" size="medium" class="tag">截止：{{ a.due }}</van-tag>
                  </div>
                  <div v-if="a.quote" class="action-quote">出处：{{ a.quote }}</div>
                </div>
              </div>
              <van-empty v-else description="暂无待办" image-size="64" />
            </van-cell-group>
          </div>
        </div>
      </van-tab>
    </van-tabs>
  </div>
</template>

<style scoped>
.detail { padding-bottom: 16px; }
.detail-head { display: flex; align-items: center; gap: 8px; padding: 4px 8px 8px; }
.detail-title { flex: 1; font-weight: 600; font-size: 15px; word-break: break-word; }
.block { margin-top: 12px; }
.audio-slot { min-height: 40px; }
.audio-mode { display: flex; gap: 0; padding: 8px 16px 4px; }
.audio-mode span {
  padding: 4px 14px; font-size: 13px; color: #646566; cursor: pointer;
  border: 1px solid #dcdee0; border-right: none;
}
.audio-mode span:first-child { border-radius: 4px 0 0 4px; }
.audio-mode span:last-child { border-right: 1px solid #dcdee0; border-radius: 0 4px 4px 0; }
.audio-mode span.on { background: #1989fa; border-color: #1989fa; color: #fff; }
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
.re-seg-head { padding: 4px 16px; display: flex; align-items: center; gap: 8px; }
.re-seg-tip { font-size: 13px; color: #646566; }
.final-box { padding: 12px 16px; }
.re-list { padding: 8px 16px; }
.re-item { margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px; }
.re-time { color: #969799; font-size: 12px; }
.utt-text { flex: 1; word-break: break-word; }
.spk-tag { flex-shrink: 0; }
.sums { padding: 8px 16px; }
.sum-item { margin-bottom: 16px; }
.sum-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.sum-title { font-weight: 600; font-size: 14px; }
.sum-content { font-size: 14px; }
.tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { margin: 0; }
.action-list { padding: 8px 16px; }
.action-item { margin-bottom: 14px; }
.action-task { font-size: 14px; font-weight: 600; color: #323233; }
.action-meta { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }
.action-quote { margin-top: 4px; font-size: 12px; color: #969799; word-break: break-word; }

/* 双 tab 响应式：手机上下堆叠（默认列向），电脑左右分栏（左原文右纪要） */
.version-tabs { margin-top: 4px; }
.cols { display: flex; flex-direction: column; gap: 0; }
.col { min-width: 0; }
@media (min-width: 768px) {
  .cols { flex-direction: row; align-items: flex-start; gap: 12px; padding: 0 8px; }
  .cols .block { margin-top: 8px; }
  .col-main { flex: 7; }
  .col-side { flex: 5; }
}
</style>
