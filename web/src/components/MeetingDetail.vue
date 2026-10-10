<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import DebugPanel from './DebugPanel.vue'
import EnrollClusterModal from './EnrollClusterModal.vue'
import { fmtMs as fmtMsShared } from '../ui/format'
import { showToast } from '../ui/toast'

interface Attr { key: string; value: string }
interface Utterance { seq: number; speaker: string; text: string; start_ms: number; end_ms: number }
interface SummaryRow {
  seq: number
  scope: string
  title: string
  content: string
  start_ms: number | null
  end_ms: number | null
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
const sourceMode = ref('')
const startedAt = ref('')
const endedAt = ref('')
const rawAudioUrl = ref('')
const audioMode = ref<'denoised' | 'raw'>('denoised')  // 回放默认降噪版；原始版为存档生肉
const audioUrl = computed(() =>
  audioMode.value === 'raw' ? rawAudioUrl.value : `/api/meetings/${props.meetingId}/audio_denoised`
)
const utterances = ref<Utterance[]>([])   // 实时版（会中批次，草稿态）
const reSegs = ref<Utterance[]>([])       // 会末版（全场重跑，权威态）
const reannotated = ref(false)
const finalStatus = ref('')   // '' | running | done | failed（会末版全流程状态，2026-10-01）
const reLoading = ref(false)
const liveSummaries = ref<SummaryRow[]>([])
const finalSummaries = ref<SummaryRow[]>([])
const actionItems = ref<ActionItem[]>([]) // 权威待办清单（会末版聚合；live-only 时为实时版聚合）
const loading = ref(true)

// Tab：纪要对照（转写+纪要同屏对齐）/ 调试管道；live/final 用 scope pill
const activeTab = ref<'summary' | 'debug'>('summary')
const showLive = computed(() => versionMode.value !== 'final')
const showFinal = computed(() => versionMode.value !== 'live')
const scope = ref<'live' | 'final'>('live')

const scopeSummaries = computed(() =>
  (scope.value === 'final' ? finalSummaries.value : liveSummaries.value)
    .filter((s) => s.seq >= 1)
    .sort((a, b) => (a.start_ms ?? 0) - (b.start_ms ?? 0)),
)
// 终版 hero：跟随 scope 切换——看会末版就显示会末终版，看实时版就显示实时终版；
// 当前 scope 没有终版时兜底另一版（final 优先，权威版永远看得到）
const heroSummary = computed(() =>
  (scope.value === 'final'
    ? finalSummaries.value.find((s) => s.seq === 0) || liveSummaries.value.find((s) => s.seq === 0)
    : liveSummaries.value.find((s) => s.seq === 0) || finalSummaries.value.find((s) => s.seq === 0)
  ),
)
const scopeUtterances = computed(() => (scope.value === 'final' ? reSegs.value : utterances.value))
// 版本卡片产物计数
const liveSegCount = computed(() => liveSummaries.value.filter((s) => s.seq >= 1).length)
const finalSegCount = computed(() => finalSummaries.value.filter((s) => s.seq >= 1).length)

// 会末版重跑中：final_status=running，或会议刚结束状态还没刷出（短暂窗口）。
// WS 推送详情页不一定收得到，轮询兜底（简单优先）
const finalRunning = computed(
  () =>
    showFinal.value &&
    (finalStatus.value === 'running' ||
      (status.value === 'ended' && !finalStatus.value && !reannotated.value)),
)
const finalFailed = computed(() => showFinal.value && finalStatus.value === 'failed')
let pollTimer: ReturnType<typeof setInterval> | null = null

function startPoll() {
  stopPoll()
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/meetings/${props.meetingId}`)
      const d = await r.json()
      if (d.final_status === 'done' || d.final_status === 'failed') {
        stopPoll()
        await load()
        if (d.final_status === 'failed') showToast('会末版生成失败，可点「重新标注」重试', 'error', 5000)
      }
    } catch { /* 下次再探 */ }
  }, 3000)
}
function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}
watch(finalRunning, (v) => (v ? startPoll() : stopPoll()))

// 说话人配色（与开会页同一调色板）
const speakerColors = ['#2f5cff', '#0f9d6c', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d']
const speakerColorMap = new Map<string, string>()
function colorOf(speaker: string): string {
  if (!speakerColorMap.has(speaker)) {
    speakerColorMap.set(speaker, speakerColors[speakerColorMap.size % speakerColors.length])
  }
  return speakerColorMap.get(speaker)!
}
function initial(name: string): string {
  return (name || '？').trim().charAt(0)
}

// ---------- 定制音频播放器（原生 audio 去壳，自绘进度条） ----------
const audioEl = ref<HTMLAudioElement | null>(null)
const playing = ref(false)
const curMs = ref(0)
const durMs = ref(0)

function togglePlay() {
  const el = audioEl.value
  if (!el) return
  el.paused ? el.play() : el.pause()
}
function onTimeUpdate() {
  curMs.value = (audioEl.value?.currentTime || 0) * 1000
}
function onLoadedMeta() {
  durMs.value = (audioEl.value?.duration || 0) * 1000
}
function onSeek(e: MouseEvent) {
  const el = audioEl.value
  const track = e.currentTarget as HTMLElement
  if (!el || !durMs.value) return
  const rect = track.getBoundingClientRect()
  el.currentTime = ((e.clientX - rect.left) / rect.width) * (durMs.value / 1000)
}
// 纪要段/句子点击 → seek 并播放
function seekTo(ms: number | null | undefined) {
  const el = audioEl.value
  if (!el || ms == null) return
  el.currentTime = ms / 1000
  el.play().catch(() => {})
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

// ---------- 纪要对照：左栏转写（波次虚线分隔）+ 右栏纪要卡纵向对齐 ----------
interface LeftRowWave { type: 'wave'; key: string; wave: SummaryRow }
interface LeftRowUtt { type: 'utt'; key: string; u: Utterance; waveSeq: number | null }
type LeftRow = LeftRowWave | LeftRowUtt

// 左栏行：波次分隔条插在每波第一句之前；句子归属 = 最近一个已开始的波
const leftRows = computed<LeftRow[]>(() => {
  const rows: LeftRow[] = []
  const waves = scopeSummaries.value.filter((w) => w.start_ms != null)
  let wi = 0
  for (const u of scopeUtterances.value) {
    while (wi < waves.length && (waves[wi].start_ms ?? 0) <= u.start_ms) {
      rows.push({ type: 'wave', key: 'w' + waves[wi].seq, wave: waves[wi] })
      wi++
    }
    rows.push({ type: 'utt', key: 'u' + u.seq, u, waveSeq: wi > 0 ? waves[wi - 1].seq : null })
  }
  while (wi < waves.length) {  // 尾部无句子的波也出分隔条（防御）
    rows.push({ type: 'wave', key: 'w' + waves[wi].seq, wave: waves[wi] })
    wi++
  }
  return rows
})

const compareRef = ref<HTMLElement | null>(null)
const waveTops = ref<Record<number, number>>({})  // seq -> 右栏卡片 top
const rightHeight = ref(0)                        // 右栏容器最小高度（绝对定位不撑高）
const flashSeq = ref<number | null>(null)         // 点击纪要卡 → 左栏对应波段高亮
let flashTimer: ReturnType<typeof setTimeout> | null = null

// 对齐：右栏卡片 top = 左栏该波分隔条的纵向位置；卡片比波段高时往下顺移防重叠
async function relayout() {
  await nextTick()
  requestAnimationFrame(() => {
    const root = compareRef.value
    if (!root) return
    const base = root.getBoundingClientRect().top
    let prevBottom = 0
    const tops: Record<number, number> = {}
    for (const s of scopeSummaries.value) {
      const anchor = root.querySelector(`[data-wave-anchor="${s.seq}"]`)
      const card = root.querySelector(`[data-wave-card="${s.seq}"]`) as HTMLElement | null
      const desired = anchor
        ? (anchor as HTMLElement).getBoundingClientRect().top - base
        : prevBottom
      const top = Math.max(desired, prevBottom)
      tops[s.seq] = top
      prevBottom = top + (card?.offsetHeight ?? 140) + 12
    }
    waveTops.value = tops
    rightHeight.value = prevBottom
  })
}
watch([scope, leftRows], relayout)

function onWaveCardClick(s: SummaryRow) {
  seekTo(s.start_ms)
  flashSeq.value = s.seq
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => (flashSeq.value = null), 1800)
  compareRef.value
    ?.querySelector(`[data-wave-anchor="${s.seq}"]`)
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function onResize() {
  relayout()
}

onMounted(() => window.addEventListener('resize', onResize))

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
    sourceMode.value = detail.source_mode || ''
    scope.value = versionMode.value === 'final' ? 'final' : 'live'
    startedAt.value = detail.started_at || ''
    endedAt.value = detail.ended_at || ''
    rawAudioUrl.value = detail.audio_url || ''
    utterances.value = detail.utterances || []
    reSegs.value = detail.utterances_re || []
    reannotated.value = !!detail.reannotated
    finalStatus.value = detail.final_status || ''
    liveSummaries.value = (liveSum as SummaryRow[]) || []
    finalSummaries.value = (finalSum as SummaryRow[]) || []
    actionItems.value = normActions(detail.action_items)
  } finally {
    loading.value = false
  }
  loadUnknownClusters()
}

async function rerunReannotate() {
  reLoading.value = true
  try {
    const r = await fetch(`/api/meetings/${props.meetingId}/reannotate`, { method: 'POST' })
    const d = await r.json()
    if (!r.ok) {
      showToast(d.detail || '触发失败', 'error')
      return
    }
    // 完整重跑会末版（转写+波次纪要+会末总结）在后台跑，本地直接进轮询
    finalStatus.value = 'running'
    startPoll()
    showToast('已触发完整重跑会末版，完成后本页自动刷新', 'success')
  } finally {
    reLoading.value = false
  }
}

// ---------- 会末陌生人 → 声纹注册（2026-10-01） ----------
const showEnroll = ref(false)
const unknownClusters = ref<{ label: string; seg_count: number; total_ms: number; current_speaker?: string }[]>([])

async function loadUnknownClusters() {
  if (!reannotated.value) {
    unknownClusters.value = []
    return
  }
  try {
    const r = await fetch(`/api/meetings/${props.meetingId}/unknown_clusters`)
    const d = await r.json()
    unknownClusters.value = d.clusters || []
  } catch (e) {
    console.warn('loadUnknownClusters 失败:', e)  // 别静默失败，方便定位
  }
}

function isUnknownSpeaker(speaker: string): boolean {
  return scope.value === 'final' && /^说话人\d+$/.test(speaker || '')
}

function onSpeakerClick(speaker: string) {
  if (isUnknownSpeaker(speaker)) showEnroll.value = true
}

function onEnrolled() {
  showToast('已注册，点右上角「重新标注」可让会末版用新名字重跑', 'info', 5000)
  loadUnknownClusters()
}

function fmtMs(ms: number | null | undefined): string {
  return fmtMsShared(ms)
}

function fmtClock(iso: string): string {
  return iso ? new Date(iso).toLocaleString('zh-CN') : '-'
}

function copyMeetingId() {
  navigator.clipboard?.writeText(meetingId.value).then(
    () => showToast('会议 ID 已复制', 'success', 1500),
    () => showToast('复制失败', 'error'),
  )
}

const durationText = computed(() => {
  if (!startedAt.value || !endedAt.value) return ''
  const min = Math.round((Date.parse(endedAt.value) - Date.parse(startedAt.value)) / 60000)
  return `${min} 分钟`
})

const versionText = computed(() =>
  versionMode.value === 'both' ? '实时 + 会末' : versionMode.value === 'live' ? '只实时版' : '只会末版',
)
const sourceText = computed(() => (sourceMode.value === 'online' ? '线上会议' : '线下会议'))

function segText(s: Utterance): string {
  return s.text || '（无转写）'
}

onMounted(load)
onBeforeUnmount(() => {
  stopPoll()
  window.removeEventListener('resize', onResize)
  if (flashTimer) clearTimeout(flashTimer)
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
  <div class="detail">
    <!-- 骨架屏 -->
    <div v-if="loading" class="card" style="padding: 20px">
      <div class="skeleton" style="width: 40%; height: 20px" />
      <div class="skeleton" style="width: 70%; height: 13px; margin-top: 14px" />
      <div class="skeleton" style="width: 100%; height: 120px; margin-top: 20px" />
    </div>

    <template v-else>
      <!-- 顶部：返回 + 标题 + 元信息 + 操作 -->
      <div class="d-head">
        <button class="icon-btn" title="返回列表" @click="emit('back')">
          <AppIcon name="back" :size="16" />
        </button>
        <div class="d-head-main">
          <h2 class="d-title">{{ title || '未命名会议' }}</h2>
          <div class="d-meta num">
            {{ fmtClock(startedAt) }}
            <template v-if="durationText"> · {{ durationText }}</template>
            · {{ versionText }} · {{ sourceText }}
            <span class="m-status" :class="status">
              <span v-if="status === 'live'" class="pulse-dot" />
              {{ status === 'live' ? '进行中' : status === 'interrupted' ? '已中断' : '已结束' }}
            </span>
            <span class="m-id num" title="会议 ID，点击复制" @click="copyMeetingId">{{ meetingId }}</span>
          </div>
        </div>
        <div class="d-actions">
          <button
            class="btn btn-sm"
            :disabled="reLoading"
            title="会末终版生成失败或想重跑时使用"
            @click="rerunReannotate"
          >
            <AppIcon name="refresh" :size="13" /> {{ reLoading ? '触发中…' : '重新标注' }}
          </button>
          <button class="btn btn-sm" @click="downloadMarkdown">
            <AppIcon name="download" :size="13" /> 下载纪要
          </button>
          <button v-if="rawAudioUrl" class="btn btn-sm btn-primary" @click="emit('replay', props.meetingId, title)">
            <AppIcon name="play" :size="12" /> 重放测试
          </button>
        </div>
      </div>

      <!-- 属性标签 -->
      <div v-if="attrs.length" class="d-attrs">
        <span v-for="a in attrs" :key="a.key" class="chip chip-accent">{{ a.key }}：{{ a.value }}</span>
      </div>

      <!-- 播放器条（吸顶） -->
      <div v-if="rawAudioUrl" class="card player-card">
        <button class="icon-btn play-btn" @click="togglePlay">
          <AppIcon :name="playing ? 'pause' : 'play'" :size="16" />
        </button>
        <div class="track" @click="onSeek">
          <div class="track-fill" :style="{ width: durMs ? (curMs / durMs) * 100 + '%' : '0%' }" />
        </div>
        <span class="p-time num">{{ fmtMs(curMs) }} / {{ fmtMs(durMs) }}</span>
        <div class="seg audio-seg">
          <button class="seg-item" :class="{ on: audioMode === 'denoised' }" @click="audioMode = 'denoised'">降噪版</button>
          <button class="seg-item" :class="{ on: audioMode === 'raw' }" @click="audioMode = 'raw'">原始版</button>
        </div>
        <audio
          ref="audioEl"
          :src="audioUrl"
          preload="metadata"
          @timeupdate="onTimeUpdate"
          @loadedmetadata="onLoadedMeta"
          @play="playing = true"
          @pause="playing = false"
          @ended="playing = false"
        />
      </div>

      <!-- 版本大切换（页面级，2026-10-01）：实时版/会末版是两个独立产物，升格为大卡片；
           整页内容（纪要对照 + 调试管道）跟随选中的版本 -->
      <div class="v-switch">
        <button v-if="showLive" class="v-card" :class="{ on: scope === 'live' }" @click="scope = 'live'">
          <div class="v-card-head">
            <AppIcon name="mic" :size="15" />
            <span class="v-card-name">实时版</span>
            <span class="v-card-tag">会中批次 · 实时草稿</span>
          </div>
          <div class="v-card-status">
            <template v-if="status === 'live'">
              <span class="pulse-dot" /> 进行中 · 已出 {{ liveSegCount }} 段纪要
            </template>
            <template v-else>{{ liveSegCount }} 段纪要 · {{ utterances.length }} 句转写</template>
          </div>
        </button>
        <button
          v-if="showFinal"
          class="v-card"
          :class="{ on: scope === 'final', failed: finalFailed }"
          @click="scope = 'final'"
        >
          <div class="v-card-head">
            <AppIcon name="doc" :size="15" />
            <span class="v-card-name">会末版</span>
            <span class="v-card-tag">全场重跑 · 权威版</span>
          </div>
          <div class="v-card-status">
            <span v-if="finalRunning" class="breathing">会末版生成中…</span>
            <span v-else-if="finalStatus === 'done'" class="v-ok">
              <AppIcon name="check" :size="12" /> 已出 · {{ finalSegCount }} 段纪要
            </span>
            <span v-else-if="finalFailed" class="v-fail">
              <AppIcon name="warning" :size="12" /> 生成失败
              <span
                class="v-rerun"
                @click.stop="rerunReannotate"
              >{{ reLoading ? '触发中…' : '重跑' }}</span>
            </span>
            <span v-else>会议结束后生成</span>
          </div>
        </button>
      </div>

      <!-- 内容 Tab（二级）：纪要对照 / 调试管道，均跟随上方选中的版本 -->
      <div class="tabs d-tabs">
        <button class="tab-item" :class="{ on: activeTab === 'summary' }" @click="activeTab = 'summary'">纪要对照</button>
        <button class="tab-item" :class="{ on: activeTab === 'debug' }" @click="activeTab = 'debug'">调试管道</button>
      </div>

      <!-- ============ 纪要对照 tab ============ -->
      <div v-if="activeTab === 'summary'" class="tab-body">
        <!-- 终版 hero -->
        <div v-if="heroSummary" class="card hero-card">
          <div class="hero-badge">
            <AppIcon name="doc" :size="13" />
            {{ heroSummary.scope === 'final' ? '会末最终总结' : '实时版终版' }}
          </div>
          <div class="hero-title">{{ heroSummary.title || '最终总结' }}</div>
          <div class="hero-content" v-html="heroSummary.content"></div>
        </div>

        <!-- 待办汇总 -->
        <div v-if="actionItems.length" class="card todo-card">
          <div class="block-title"><AppIcon name="todo" :size="15" /> 待办汇总</div>
          <div class="todo-list">
            <div v-for="(a, i) in actionItems" :key="i" class="todo-item">
              <div class="todo-task">{{ a.task || '（未命名任务）' }}</div>
              <div class="todo-meta">
                <span v-if="a.assignee" class="chip chip-accent">负责人：{{ a.assignee }}</span>
                <span v-if="a.due" class="chip chip-amber">截止：{{ a.due }}</span>
              </div>
              <div v-if="a.quote" class="todo-quote">出处：{{ a.quote }}</div>
            </div>
          </div>
        </div>

        <!-- 会末版：全部簇 → 注册/重注册入口（2026-10-10 开放版：含已识别簇） -->
        <div v-if="scope === 'final' && unknownClusters.length" class="enroll-banner">
          <AppIcon name="users" :size="15" />
          <span>
            会末版分出 {{ unknownClusters.length }} 个簇（{{
              unknownClusters.map((c) => `${c.label}·${c.current_speaker || '?'}(${Math.round(c.total_ms / 1000)}s)`).join('、')
            }}），可勾选注册/重注册为声纹
          </span>
          <button class="btn btn-sm btn-primary" @click="showEnroll = true">注册/重注册</button>
        </div>

        <!-- 对照视图：左栏转写（波次虚线分隔）+ 右栏纪要卡按时间对齐 -->
        <div v-if="scopeUtterances.length" class="card compare-card">
          <div class="t-head">
            <span class="chip" :class="scope === 'final' ? 'chip-green' : 'chip-amber'">
              {{ scope === 'final' ? (finalStatus === 'done' ? '会末终版 · 全场重跑最准' : '会末版 · 尚未生成') : '会中批次版 · 实时草稿' }}
            </span>
            <span class="t-tip">点击句子/纪要卡可从该处回放；虚线 = 该处产生了一段纪要</span>
          </div>
          <div ref="compareRef" class="compare">
            <!-- 左栏：转写全文 + 波次虚线分隔条 -->
            <div class="cmp-left">
              <template v-for="row in leftRows" :key="row.key">
                <div
                  v-if="row.type === 'wave'"
                  class="wave-divider"
                  :data-wave-anchor="row.wave.seq"
                  :title="`第 ${row.wave.seq} 波纪要覆盖范围`"
                  @click="seekTo(row.wave.start_ms)"
                >
                  <span class="wd-line" />
                  <span class="wd-label">
                    第 {{ row.wave.seq }} 波 · {{ row.wave.title || '（无标题）' }}
                    · <span class="num">{{ fmtMs(row.wave.start_ms) }}–{{ fmtMs(row.wave.end_ms) }}</span>
                  </span>
                  <span class="wd-line" />
                </div>
                <div
                  v-else
                  class="utt"
                  :class="{ flash: row.waveSeq != null && row.waveSeq === flashSeq }"
                  @click="seekTo(row.u.start_ms)"
                >
                  <span
                    class="avatar"
                    :style="{ background: colorOf(row.u.speaker || '未识别') + '1f', color: colorOf(row.u.speaker || '未识别') }"
                  >{{ initial(row.u.speaker || '未识别') }}</span>
                  <div class="utt-main">
                    <div class="utt-meta">
                      <span
                        class="utt-name"
                        :class="{ 'utt-name-link': isUnknownSpeaker(row.u.speaker) }"
                        :style="{ color: colorOf(row.u.speaker || '未识别') }"
                        :title="isUnknownSpeaker(row.u.speaker) ? '点击注册此说话人声纹' : ''"
                        @click.stop="onSpeakerClick(row.u.speaker)"
                      >{{ row.u.speaker || '未识别' }}</span>
                      <span class="utt-time num">{{ fmtMs(row.u.start_ms) }}</span>
                    </div>
                    <div class="utt-text">{{ segText(row.u) }}</div>
                  </div>
                </div>
              </template>
            </div>
            <!-- 右栏：纪要卡绝对定位对齐左栏波次 -->
            <div class="cmp-right" :style="{ minHeight: rightHeight + 'px' }">
              <div
                v-for="s in scopeSummaries"
                :key="`${scope}-${s.seq}`"
                class="wave-card cmp-card"
                :class="{ 'card-hover': s.start_ms != null }"
                :data-wave-card="s.seq"
                :style="{ top: (waveTops[s.seq] ?? 0) + 'px' }"
                @click="onWaveCardClick(s)"
              >
                <div class="wave-head">
                  <span class="wave-seq">第 {{ s.seq }} 段</span>
                  <span v-if="s.start_ms != null" class="wave-time num">{{ fmtMs(s.start_ms) }}–{{ fmtMs(s.end_ms) }}</span>
                  <AppIcon v-if="s.start_ms != null" name="play" :size="11" class="wave-play" />
                </div>
                <div class="wave-title">{{ s.title || `段落 ${s.seq}` }}</div>
                <div class="wave-content" v-html="s.content"></div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="!heroSummary && !scopeUtterances.length" class="card empty">
          <div class="empty-icon"><AppIcon name="doc" :size="24" /></div>
          本场会议暂无纪要
        </div>
      </div>

      <!-- ============ 调试管道 tab ============ -->
      <DebugPanel v-else :meeting-id="props.meetingId" :scope="scope" />

      <!-- 会末陌生人 → 声纹注册面板 -->
      <EnrollClusterModal
        :show="showEnroll"
        :meeting-id="props.meetingId"
        @close="showEnroll = false"
        @enrolled="onEnrolled"
      />
    </template>
  </div>
</template>

<style scoped>
.detail { display: flex; flex-direction: column; gap: 14px; }

/* 顶部 */
.d-head { display: flex; align-items: flex-start; gap: 12px; }
.d-head-main { flex: 1; min-width: 0; }
.d-title { font-size: 20px; font-weight: 600; margin: 2px 0 4px; word-break: break-word; }
.d-meta { font-size: 12.5px; color: var(--text-3); display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.m-status { display: inline-flex; align-items: center; gap: 5px; margin-left: 6px; }
.m-status.live { color: var(--green); }
.m-status.interrupted { color: var(--amber); }
.m-id {
  margin-left: 6px;
  padding: 1px 7px;
  border-radius: 6px;
  background: #f2f3f5;
  color: var(--text-3);
  font-size: 11.5px;
  cursor: copy;
}
.m-id:hover { background: #e8eaee; color: var(--text-2); }
.d-actions { display: flex; gap: 8px; flex-shrink: 0; padding-top: 4px; }
.d-attrs { display: flex; flex-wrap: wrap; gap: 6px; }

/* 播放器条（吸顶） */
.player-card {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
}
.play-btn { width: 36px; height: 36px; border-radius: 50%; color: var(--accent); border-color: #d8e1ff; background: var(--accent-soft); }
.play-btn:hover { background: #dfe7ff; color: var(--accent); }
.track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #eef0f3;
  cursor: pointer;
  position: relative;
}
.track-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 0.1s linear;
}
.p-time { font-size: 12px; color: var(--text-3); font-variant-numeric: tabular-nums; flex-shrink: 0; }
.audio-seg { flex-shrink: 0; }

/* 版本大切换（页面级） */
.v-switch {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}
.v-card {
  text-align: left;
  padding: 14px 18px;
  border: 1.5px solid var(--border);
  border-radius: 14px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.15s ease-out, box-shadow 0.15s ease-out, background 0.15s ease-out;
}
.v-card:hover { border-color: #c7d4f5; }
.v-card.on {
  border-color: var(--accent);
  background: linear-gradient(180deg, #f7f9ff 0%, #fff 70%);
  box-shadow: 0 4px 16px rgba(47, 92, 255, 0.08);
}
.v-card.failed { border-color: #f0b6b9; }
.v-card.failed.on { border-color: var(--rec); background: linear-gradient(180deg, #fdf6f6 0%, #fff 70%); }
.v-card-head { display: flex; align-items: center; gap: 8px; color: var(--text-1); }
.v-card.on .v-card-head { color: var(--accent); }
.v-card-name { font-size: 15px; font-weight: 600; }
.v-card-tag { font-size: 12px; color: var(--text-3); }
.v-card-status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  font-size: 12.5px;
  color: var(--text-3);
}
.v-ok { display: inline-flex; align-items: center; gap: 4px; color: var(--green); font-weight: 500; }
.v-fail { display: inline-flex; align-items: center; gap: 5px; color: var(--rec); font-weight: 500; }
.v-rerun {
  margin-left: 4px;
  padding: 2px 10px;
  border-radius: 6px;
  border: 1px solid #f0b6b9;
  background: #fff;
  font-size: 12px;
  cursor: pointer;
}
.v-rerun:hover { background: var(--rec-soft); }

/* Tab */
.d-tabs { align-items: center; }
.tab-body { display: flex; flex-direction: column; gap: 14px; padding-top: 4px; }

/* 终版 hero */
.hero-card {
  padding: 18px 20px;
  border-color: #d8e1ff;
  background: linear-gradient(180deg, #f7f9ff 0%, #fff 60%);
}
.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 6px;
}
.hero-title { font-size: 16px; font-weight: 600; margin-bottom: 8px; }
.hero-content { font-size: 14px; color: var(--text-2); line-height: 1.75; }
.hero-content :deep(p) { margin: 0 0 6px; }

/* 波段纪要时间线 */
/* 纪要对照视图（2026-09-30：左栏转写+波次虚线，右栏纪要卡绝对定位对齐） */
.compare-card { overflow: hidden; }
.t-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.t-tip { font-size: 12px; color: var(--text-3); }
.compare {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  gap: 4px 18px;
  padding: 8px 0;
}
.cmp-left { min-width: 0; }
.cmp-right { position: relative; }

/* 波次虚线分隔条：虚线 = 该处产生了一段纪要 */
.wave-divider {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 16px 8px 6px;
  cursor: pointer;
  scroll-margin-top: 90px;
}
.wd-line { flex: 1; border-top: 1.5px dashed var(--accent); opacity: 0.45; }
.wd-label {
  font-size: 12px;
  color: var(--accent);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 70%;
}
.wave-divider:hover .wd-label { text-decoration: underline; }

/* 右栏纪要卡（绝对定位，top 由 relayout 按左栏波次位置计算） */
.wave-card {
  position: relative;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
}
.cmp-card {
  position: absolute;
  left: 0;
  right: 0;
  cursor: pointer;
  transition: top 0.25s ease-out, border-color 0.15s ease-out;
}
.cmp-card:hover { border-color: var(--accent); }
.wave-head { display: flex; align-items: center; gap: 8px; }
.wave-seq { font-size: 12px; font-weight: 600; color: var(--accent); }
.wave-time { font-size: 11.5px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.wave-play { color: var(--text-3); margin-left: auto; opacity: 0; transition: opacity 0.15s ease-out; }
.wave-card:hover .wave-play { opacity: 1; }
.wave-title { font-size: 14px; font-weight: 600; margin: 4px 0 6px; }
.wave-content { font-size: 13.5px; color: var(--text-2); line-height: 1.7; }
.wave-content :deep(p) { margin: 0 0 4px; }

/* 待办汇总 */
.todo-card { padding: 14px 18px; }
.block-title { display: flex; align-items: center; gap: 6px; font-size: 14px; font-weight: 600; margin-bottom: 10px; }
.todo-list { display: flex; flex-direction: column; gap: 12px; }
.todo-item { padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.todo-item:last-child { padding-bottom: 0; border-bottom: none; }
.todo-task { font-size: 14px; font-weight: 500; }
.todo-meta { margin-top: 5px; display: flex; flex-wrap: wrap; gap: 5px; }
.todo-quote { margin-top: 5px; font-size: 12px; color: var(--text-3); word-break: break-word; }

/* 转写句子 */
.utt {
  display: flex;
  gap: 10px;
  padding: 7px 16px;
  margin: 0 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease-out;
}
.utt:hover { background: #f7f8fa; }
.utt.flash { background: var(--accent-soft); transition: background 0.3s ease-out; }
.avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12.5px;
  font-weight: 600;
  flex-shrink: 0;
  margin-top: 1px;
}
.utt-main { flex: 1; min-width: 0; }
.utt-meta { display: flex; align-items: baseline; gap: 8px; }
.utt-name { font-size: 13px; font-weight: 600; }
.utt-time { font-size: 11.5px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.utt-text { font-size: 14px; word-break: break-word; line-height: 1.65; }

/* 会末陌生人 → 声纹注册 */
.enroll-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  font-size: 13px;
  color: var(--amber);
  background: #fffbeb;
  border: 1px solid #fde6c8;
  border-radius: var(--radius-ctl);
}
.enroll-banner .btn { margin-left: auto; flex-shrink: 0; }
.utt-name-link { cursor: pointer; text-decoration: underline dotted; text-underline-offset: 3px; }
.utt-name-link:hover { text-decoration-style: solid; }
</style>
