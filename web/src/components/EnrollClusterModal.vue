<script setup lang="ts">
// 会末全簇注册/重注册面板（2026-10-10 改造：全簇开放 + 段内波形裁剪）
// 每个簇是独立注册单元：折叠展示，展开后试听/勾选/剪刀裁剪段内范围；
// 填新名=注册新人，填已有名=补录/合并。提交用 cluster_labels + trims。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import UiModal from '../ui/UiModal.vue'
import { fmtMs, fmtDur } from '../ui/format'
import { showToast } from '../ui/toast'

interface Segment {
  seq: number
  start_ms: number
  end_ms: number
  text: string
  score: number | null
  audio_url: string | null
  cluster_sim?: number | null   // 段 vs 簇质心相似度（后端 2026-10-09 起返回）
}
interface Cluster {
  label: string                  // 显示名 "簇1"
  cluster_label: string          // 3D 聚类原始标签 "0"/"1"/"4"（提交用）
  current_speaker: string        // 该簇当前判名（冯忠俊/镇宇/说话人1…）
  seg_count: number
  total_ms: number
  segments: Segment[]
  registered?: string   // 已注册成的名字（本场内防重复提交）
}

const props = defineProps<{ show: boolean; meetingId: string }>()
const emit = defineEmits<{ close: []; enrolled: [] }>()

const loading = ref(false)
const clusters = ref<Cluster[]>([])
const names = ref<Record<string, string>>({})   // label -> 输入的名字
const excluded = ref<Set<number>>(new Set())    // 剔除的错分段（seq 全局唯一）
const trims = ref<Record<number, [number, number]>>({})  // seq -> [start_ms, end_ms] 保留范围
const submitting = ref<string | null>(null)     // 正在注册的簇 label；'__batch__'=批量中
const playingSeq = ref<number | null>(null)
const openLabel = ref<string | null>(null)      // 手风琴：当前展开的簇（null=全折叠）
const editingSeq = ref<number | null>(null)     // 正在裁剪的段
const autoPickSec = ref(40)                     // 后端下发：自动勾选目标秒数
const warnSim = ref(0.5)                        // 后端下发：低分标红阈值
let audio: HTMLAudioElement | null = null

async function load() {
  loading.value = true
  try {
    const r = await fetch(`/api/meetings/${props.meetingId}/unknown_clusters`)
    const d = await r.json()
    clusters.value = d.clusters || []
    autoPickSec.value = d.auto_pick_sec ?? 40
    warnSim.value = d.warn_sim ?? 0.5
    excluded.value = computeDefaultExcludes(clusters.value)
  } catch {
    showToast('加载簇列表失败', 'error')
  } finally {
    loading.value = false
  }
}

// 默认勾选（2026-10-09 用户拍板"懒得听"方案）：按"段 vs 簇质心"相似度降序，
// 凑满 autoPickSec 秒即止；相似度 < warnSim 的段（疑似混入他人）不自动选、标红留人挑。
// 整簇没有相似度数据（GPU 失败）时退回旧行为：全选。
function computeDefaultExcludes(all: Cluster[]): Set<number> {
  const ex = new Set<number>()
  for (const c of all) {
    const segs = c.segments.filter((s) => s.audio_url)
    const withSim = segs.filter((s) => typeof s.cluster_sim === 'number')
    if (withSim.length < 2) continue            // 无分数：全选（旧行为）
    const sorted = [...withSim].sort((a, b) => (b.cluster_sim ?? 0) - (a.cluster_sim ?? 0))
    let pickedMs = 0
    const picked = new Set<number>()
    for (const s of sorted) {
      if ((s.cluster_sim ?? 0) < warnSim.value) continue   // 低分不自动选
      if (pickedMs >= autoPickSec.value * 1000) break
      picked.add(s.seq)
      pickedMs += Math.max(0, s.end_ms - s.start_ms)
    }
    for (const s of segs) {
      if (!picked.has(s.seq)) ex.add(s.seq)
    }
  }
  return ex
}
watch(() => props.show, (v) => {
  if (v) {
    names.value = {}
    excluded.value = new Set()
    trims.value = {}
    playingSeq.value = null
    openLabel.value = null
    closeEditor()
    load()
  } else {
    stopAudio()
    closeEditor()
  }
})

function toggleCluster(c: Cluster) {
  openLabel.value = openLabel.value === c.label ? null : c.label
  if (editingSeq.value !== null) closeEditor()
}

function toggleSeg(seq: number) {
  excluded.value.has(seq) ? excluded.value.delete(seq) : excluded.value.add(seq)
  excluded.value = new Set(excluded.value)
}

// 段有效素材毫秒：被剔除=0；有裁剪=裁剪范围；否则整段
function segKeptMs(s: Segment): number {
  if (excluded.value.has(s.seq)) return 0
  const t = trims.value[s.seq]
  if (t) return Math.max(0, t[1] - t[0])
  return Math.max(0, s.end_ms - s.start_ms)
}

function keptMsOf(c: Cluster): number {
  return c.segments.reduce((sum, s) => sum + segKeptMs(s), 0)
}

// 当前判名是否"已识别"（非 说话人N / 未识别 之类）
const UNKNOWN_RE = /^(说话人|陌生人|待定|未识别)/
function speakerChipClass(name: string): string {
  return UNKNOWN_RE.test(name) ? 'chip-amber' : 'chip-green'
}

// 可批量注册的簇：有名字、未注册过、素材够 3 秒
const batchable = computed(() =>
  clusters.value.filter(
    (c) => !c.registered && (names.value[c.label] || '').trim() && keptMsOf(c) >= 3000,
  ),
)

function stopAudio() {
  if (audio) {
    audio.pause()
    audio = null
  }
  playingSeq.value = null
}
function playSeg(s: Segment) {
  if (!s.audio_url) return
  if (playingSeq.value === s.seq) {
    stopAudio()
    return
  }
  stopAudio()
  audio = new Audio(s.audio_url)
  playingSeq.value = s.seq
  audio.onended = () => (playingSeq.value = null)
  audio.onerror = () => {
    playingSeq.value = null
    showToast('音频播放失败', 'error')
  }
  audio.play().catch(() => (playingSeq.value = null))
}

async function enrollOne(c: Cluster): Promise<boolean> {
  const name = (names.value[c.label] || '').trim()
  if (!name) return false
  // 只带本簇段的裁剪（后端按 seq 应用到段内保留范围）
  const bodyTrims: Record<number, [number, number]> = {}
  for (const s of c.segments) {
    const t = trims.value[s.seq]
    if (t) bodyTrims[s.seq] = t
  }
  try {
    const r = await fetch(`/api/meetings/${props.meetingId}/enroll_cluster`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        cluster_labels: [c.cluster_label],
        exclude_seqs: [...excluded.value],
        trims: bodyTrims,
      }),
    })
    const d = await r.json()
    if (!r.ok) {
      showToast(`${c.label} 注册失败：${d.detail || r.status}`, 'error')
      return false
    }
    if (d.ok === false) {
      showToast(`${c.label} 未通过质量检测：${d.reason || '有效语音不足'}`, 'error', 5000)
      return false
    }
    c.registered = name
    showToast(`「${name}」注册成功（入库 ${d.kept ?? '?'} 段）`, 'success')
    return true
  } catch {
    showToast(`${c.label} 注册请求失败`, 'error')
    return false
  }
}

async function submitOne(c: Cluster) {
  if (submitting.value || c.registered) return
  if (keptMsOf(c) < 3000) {
    showToast(`${c.label} 勾选素材不足 3 秒`, 'error')
    return
  }
  submitting.value = c.label
  try {
    if (await enrollOne(c)) emit('enrolled')
  } finally {
    submitting.value = null
  }
}

async function submitAll() {
  if (submitting.value || !batchable.value.length) return
  submitting.value = '__batch__'
  let ok = 0
  try {
    for (const c of batchable.value) {
      if (await enrollOne(c)) ok++
    }
  } finally {
    submitting.value = null
  }
  if (ok) emit('enrolled')
}

/* ---------- 波形裁剪编辑器 ---------- */
const editor = ref<{
  seq: number
  audioUrl: string
  durationMs: number
  selStart: number   // ms，相对段首
  selEnd: number
  decoding: boolean
  decodeErr: string | null
  peaks: number[] | null   // 100ms bin 的 abs 均值（0..1）
  playing: boolean
} | null>(null)
const waveCanvas = ref<HTMLCanvasElement | null>(null)
const selBar = ref<HTMLDivElement | null>(null)
let editorAudio: HTMLAudioElement | null = null
let pcmData: Float32Array | null = null
let dragSide: 'l' | 'r' | null = null
const MIN_TRIM_MS = 300

async function openEditor(s: Segment) {
  if (!s.audio_url) return
  stopEditorAudio()
  const dur = Math.max(0, s.end_ms - s.start_ms)
  const t = trims.value[s.seq]
  editor.value = {
    seq: s.seq,
    audioUrl: s.audio_url,
    durationMs: dur,
    selStart: t ? t[0] : 0,
    selEnd: t ? t[1] : dur,
    decoding: true,
    decodeErr: null,
    peaks: null,
    playing: false,
  }
  await nextTick()
  drawWave()
  try {
    const buf = await (await fetch(s.audio_url)).arrayBuffer()
    const ctx = new AudioContext()
    const decoded = await ctx.decodeAudioData(buf)
    ctx.close().catch(() => undefined)
    pcmData = decoded.getChannelData(0)
    // 100ms 一个 bin 取 abs 均值
    const binSamples = Math.max(1, Math.floor(decoded.sampleRate * 0.1))
    const bins: number[] = []
    for (let i = 0; i < pcmData.length; i += binSamples) {
      let sum = 0
      const n = Math.min(binSamples, pcmData.length - i)
      for (let j = 0; j < n; j++) sum += Math.abs(pcmData[i + j])
      bins.push(sum / n)
    }
    const peak = Math.max(...bins, 1e-4)
    if (editor.value && editor.value.seq === s.seq) {
      editor.value.peaks = bins.map((v) => v / peak)
      editor.value.decoding = false
      drawWave()
    }
  } catch {
    if (editor.value && editor.value.seq === s.seq) {
      editor.value.decoding = false
      editor.value.decodeErr = '音频解码失败（仍可按时间盲裁）'
    }
  }
}

function closeEditor() {
  stopEditorAudio()
  editor.value = null
  pcmData = null
  dragSide = null
}

function drawWave() {
  const cv = waveCanvas.value
  const e = editor.value
  if (!cv || !e) return
  const dpr = window.devicePixelRatio || 1
  const w = cv.clientWidth
  const h = cv.clientHeight
  if (!w || !h) return
  cv.width = w * dpr
  cv.height = h * dpr
  const g = cv.getContext('2d')
  if (!g) return
  g.scale(dpr, dpr)
  g.clearRect(0, 0, w, h)
  // 选区高亮背景
  const x1 = (e.selStart / e.durationMs) * w
  const x2 = (e.selEnd / e.durationMs) * w
  g.fillStyle = 'rgba(0,0,0,0.05)'
  g.fillRect(0, 0, x1, h)
  g.fillRect(x2, 0, w - x2, h)
  if (e.peaks && e.peaks.length) {
    g.fillStyle = 'var(--accent)'
    g.strokeStyle = getComputedStyle(cv).getPropertyValue('--accent') || '#4f7cff'
    g.fillStyle = g.strokeStyle
    const mid = h / 2
    const binW = w / e.peaks.length
    g.beginPath()
    for (let i = 0; i < e.peaks.length; i++) {
      const bh = Math.max(1, e.peaks[i] * (h - 6))
      const x = i * binW
      g.moveTo(x, mid - bh / 2)
      g.lineTo(x, mid + bh / 2)
    }
    g.lineWidth = Math.max(1, binW * 0.7)
    g.stroke()
  }
}

// 选区条拖动（手柄 mousedown → window mousemove/mouseup）
function startDrag(side: 'l' | 'r', ev: MouseEvent) {
  if (!editor.value) return
  dragSide = side
  ev.preventDefault()
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', endDrag)
}
function onDrag(ev: MouseEvent) {
  const e = editor.value
  const bar = selBar.value
  if (!e || !bar || !dragSide) return
  const rect = bar.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width))
  const ms = ratio * e.durationMs
  if (dragSide === 'l') {
    e.selStart = Math.min(ms, e.selEnd - MIN_TRIM_MS)
    e.selStart = Math.max(0, e.selStart)
  } else {
    e.selEnd = Math.max(ms, e.selStart + MIN_TRIM_MS)
    e.selEnd = Math.min(e.durationMs, e.selEnd)
  }
  drawWave()
}
function endDrag() {
  dragSide = null
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', endDrag)
}
onBeforeUnmount(endDrag)

function resetTrim() {
  const e = editor.value
  if (!e) return
  e.selStart = 0
  e.selEnd = e.durationMs
  drawWave()
}

function stopEditorAudio() {
  if (editorAudio) {
    editorAudio.pause()
    editorAudio = null
  }
  if (editor.value) editor.value.playing = false
}

function playTrim() {
  const e = editor.value
  if (!e) return
  stopAudio()   // 避免与段试听叠放
  if (e.playing) {
    stopEditorAudio()
    return
  }
  const url = `/api/meetings/${props.meetingId}/segments/${e.seq}/trim_audio?start_ms=${Math.round(e.selStart)}&end_ms=${Math.round(e.selEnd)}`
  editorAudio = new Audio(url)
  e.playing = true
  editorAudio.onended = () => stopEditorAudio()
  editorAudio.onerror = () => {
    stopEditorAudio()
    showToast('试听加载失败', 'error')
  }
  editorAudio.play().catch(() => stopEditorAudio())
}

function confirmTrim() {
  const e = editor.value
  if (!e) return
  if (e.selStart <= 0 && e.selEnd >= e.durationMs) {
    delete trims.value[e.seq]   // 全段=不裁
  } else {
    trims.value[e.seq] = [Math.round(e.selStart), Math.round(e.selEnd)]
  }
  trims.value = { ...trims.value }
  closeEditor()
}
</script>

<template>
  <UiModal :show="show" title="注册 / 重注册说话人" :width="640" @close="emit('close')">
    <div v-if="loading" class="ec-loading">加载中…</div>
    <div v-else-if="!clusters.length" class="ec-empty">本场会末版没有可用簇（需先跑会末版）</div>
    <template v-else>
      <div class="ec-tip">
        已按相似度自动勾选每簇约 {{ autoPickSec }} 秒。任意簇可勾选重新注册：填新名=注册新人，
        填已有名=补录/合并；错判不管，人工覆盖。
      </div>
      <div
        v-for="c in clusters"
        :key="c.label"
        class="ec-cluster"
        :class="{ done: !!c.registered, open: openLabel === c.label }"
      >
        <div class="ec-chead" @click="toggleCluster(c)">
          <AppIcon
            class="ec-caret"
            :name="openLabel === c.label ? 'locate' : 'locate'"
            :size="12"
          />
          <span class="ec-clabel">{{ c.label }}</span>
          <span class="ec-cspeaker">
            当前判名：<b class="chip" :class="speakerChipClass(c.current_speaker)">{{ c.current_speaker }}</b>
          </span>
          <span class="ec-cmeta num">{{ c.seg_count }} 段 · 素材 {{ fmtDur(keptMsOf(c) / 1000) }}</span>
          <template v-if="c.registered">
            <span class="chip chip-green ec-done-chip">
              <AppIcon name="check" :size="11" /> 已注册为 {{ c.registered }}
            </span>
          </template>
        </div>
        <div v-show="openLabel === c.label" class="ec-body">
          <div class="ec-namebar">
            <input
              v-model="names[c.label]"
              class="ec-name"
              placeholder="说话人姓名（新名=注册新人，已有名=补录/合并）"
              maxlength="32"
              @click.stop
            />
            <button
              class="btn btn-sm btn-primary"
              :disabled="!!submitting || !!c.registered || !(names[c.label] || '').trim() || keptMsOf(c) < 3000"
              @click.stop="submitOne(c)"
            >
              {{ submitting === c.label ? '注册中…' : c.registered ? '已注册' : '注册' }}
            </button>
          </div>
          <div class="ec-segs">
            <div
              v-for="s in c.segments"
              :key="s.seq"
              class="ec-seg"
              :class="{ excluded: excluded.has(s.seq), lowsim: typeof s.cluster_sim === 'number' && s.cluster_sim < warnSim }"
            >
              <input
                type="checkbox"
                :checked="!excluded.has(s.seq)"
                :disabled="!s.audio_url || !!c.registered"
                @change="toggleSeg(s.seq)"
              />
              <button
                class="icon-btn ec-play"
                :disabled="!s.audio_url"
                :title="s.audio_url ? '试听本段' : '本段无音频'"
                @click="playSeg(s)"
              >
                <AppIcon :name="playingSeq === s.seq ? 'pause' : 'play'" :size="12" />
              </button>
              <span class="ec-time num">{{ fmtMs(s.start_ms) }}–{{ fmtMs(s.end_ms) }}</span>
              <span class="ec-text">{{ s.text || '（无转写）' }}</span>
              <span
                v-if="trims[s.seq]"
                class="ec-trimmed num"
                :title="`仅保留 ${(trims[s.seq]![0] / 1000).toFixed(1)}s – ${(trims[s.seq]![1] / 1000).toFixed(1)}s`"
              >已裁剪 {{ ((trims[s.seq]![1] - trims[s.seq]![0]) / 1000).toFixed(1) }}s</span>
              <span
                v-if="typeof s.cluster_sim === 'number'"
                class="ec-sim num"
                :class="{ bad: s.cluster_sim < warnSim }"
                :title="s.cluster_sim < warnSim ? '与本人相似度低，疑似混入他人声音' : '与本人相似度'"
              >{{ s.cluster_sim.toFixed(2) }}</span>
              <button
                class="icon-btn ec-cut"
                :disabled="!s.audio_url"
                title="裁剪本段（只保留选区）"
                @click="editingSeq === s.seq ? closeEditor() : openEditor(s)"
              >
                <AppIcon name="tune" :size="11" />
              </button>
              <!-- 裁剪编辑器（内嵌层） -->
              <div v-if="editor && editor.seq === s.seq" class="ec-editor" @click.stop>
                <div v-if="editor.decoding" class="ec-ed-status">音频解码中…</div>
                <div v-else>
                  <canvas ref="waveCanvas" class="ec-wave" @click.stop></canvas>
                  <div v-if="editor.decodeErr" class="ec-ed-err">{{ editor.decodeErr }}</div>
                  <div
                    ref="selBar"
                    class="ec-selbar"
                    @click.stop
                  >
                    <div class="ec-selbar-track"></div>
                    <div
                      class="ec-selbar-hl"
                      :style="{ left: (editor.selStart / editor.durationMs * 100) + '%', width: ((editor.selEnd - editor.selStart) / editor.durationMs * 100) + '%' }"
                    ></div>
                    <div
                      class="ec-handle ec-handle-l"
                      :style="{ left: (editor.selStart / editor.durationMs * 100) + '%' }"
                      @mousedown="startDrag('l', $event)"
                    ></div>
                    <div
                      class="ec-handle ec-handle-r"
                      :style="{ left: (editor.selEnd / editor.durationMs * 100) + '%' }"
                      @mousedown="startDrag('r', $event)"
                    ></div>
                  </div>
                  <div class="ec-ed-row">
                    <span class="ec-ed-range num">
                      保留 {{ (editor.selStart / 1000).toFixed(1) }}s – {{ (editor.selEnd / 1000).toFixed(1) }}s（{{ ((editor.selEnd - editor.selStart) / 1000).toFixed(1) }}s）
                    </span>
                    <button class="btn btn-sm" @click.stop="playTrim">
                      <AppIcon :name="editor.playing ? 'stop' : 'play'" :size="11" /> 试听选区
                    </button>
                    <button class="btn btn-sm" @click.stop="resetTrim">重置</button>
                    <button class="btn btn-sm" @click.stop="closeEditor">取消</button>
                    <button class="btn btn-sm btn-primary" @click.stop="confirmTrim">确认裁剪</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <template v-if="clusters.length" #footer>
      <span class="ec-keft num">
        {{ batchable.length ? `待注册 ${batchable.length} 人` : '填写姓名后可批量注册' }}
      </span>
      <button class="btn btn-primary" :disabled="!!submitting || !batchable.length" @click="submitAll">
        {{ submitting === '__batch__' ? '批量注册中…' : '全部注册' }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped>
.ec-loading, .ec-empty { padding: 30px 0; text-align: center; color: var(--text-3); font-size: 13px; }
.ec-tip { font-size: 12.5px; color: var(--text-3); line-height: 1.6; margin-bottom: 12px; }
.ec-cluster { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 10px; overflow: hidden; }
.ec-cluster.open { border-color: var(--accent); }
.ec-cluster.done { border-color: #bfe8d2; background: #f6fcf9; }
.ec-chead {
  display: flex; align-items: center; gap: 8px;
  padding: 10px 14px; background: #f7f8fa; cursor: pointer; user-select: none;
}
.ec-cluster.open .ec-chead { background: transparent; }
.ec-cluster.done .ec-chead { background: transparent; }
.ec-caret { color: var(--text-3); flex-shrink: 0; transition: transform .15s; }
.ec-cluster.open .ec-caret { transform: rotate(90deg); color: var(--accent); }
.ec-clabel { font-size: 13.5px; font-weight: 600; flex-shrink: 0; }
.ec-cspeaker { font-size: 12px; color: var(--text-3); flex-shrink: 0; }
.ec-cspeaker .chip { font-weight: 600; }
.ec-cmeta { font-size: 12px; color: var(--text-3); margin-right: auto; }
.ec-body { padding: 0 14px 8px; }
.ec-namebar { display: flex; gap: 8px; padding: 4px 0 8px; }
.ec-name {
  border: 1px solid var(--border); border-radius: var(--radius-ctl);
  padding: 6px 10px; font-size: 13px; flex: 1; outline: none;
}
.ec-name:focus { border-color: var(--accent); }
.ec-done-chip { display: inline-flex; align-items: center; gap: 4px; }
.ec-segs { padding: 2px 0 4px; max-height: 260px; overflow-y: auto; }
.ec-seg {
  position: relative;
  display: flex; align-items: center; gap: 8px;
  padding: 5px 0; font-size: 13px; flex-wrap: wrap;
}
.ec-seg.excluded .ec-text, .ec-seg.excluded .ec-time { opacity: 0.4; text-decoration: line-through; }
.ec-play { width: 26px; height: 26px; border-radius: 50%; color: var(--accent); flex-shrink: 0; }
.ec-cut { width: 24px; height: 24px; border-radius: var(--radius-ctl); color: var(--text-3); flex-shrink: 0; }
.ec-cut:hover { color: var(--accent); border-color: var(--accent); }
.ec-time { font-size: 11.5px; color: var(--text-3); flex-shrink: 0; }
.ec-text { color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 80px; }
.ec-trimmed { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 5px; background: #eef2ff; color: var(--accent); flex-shrink: 0; }
.ec-sim { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 5px; background: #e6f6ef; color: #0f9d6c; flex-shrink: 0; }
.ec-sim.bad { background: #fdecec; color: #e5484d; }
.ec-seg.lowsim .ec-text { color: var(--rec); }
.ec-keft { margin-right: auto; font-size: 12px; color: var(--text-3); align-self: center; }

/* 裁剪编辑器（段行内嵌层，占满弹窗内容宽） */
.ec-editor {
  flex-basis: 100%;
  border: 1px solid var(--border); border-radius: var(--radius-ctl);
  background: #fbfbfc; padding: 10px 12px; margin: 4px 0 8px;
}
.ec-ed-status { font-size: 12px; color: var(--text-3); padding: 20px 0; text-align: center; }
.ec-ed-err { font-size: 11.5px; color: var(--rec); margin-top: 4px; }
.ec-wave { display: block; width: 100%; height: 100px; border-radius: var(--radius-ctl); background: #fff; border: 1px solid var(--border); }
.ec-selbar { position: relative; height: 30px; margin: 6px 0 2px; }
.ec-selbar-track {
  position: absolute; left: 0; right: 0; top: 50%; height: 6px; transform: translateY(-50%);
  border-radius: 3px; background: var(--border); opacity: .6;
}
.ec-selbar-hl {
  position: absolute; top: 50%; height: 10px; transform: translateY(-50%);
  border-radius: 5px; background: var(--accent); opacity: .25;
}
.ec-handle {
  position: absolute; top: 50%; width: 8px; height: 26px; transform: translate(-50%, -50%);
  border-radius: 3px; background: var(--accent); cursor: ew-resize;
}
.ec-ed-row { display: flex; align-items: center; gap: 8px; margin-top: 6px; flex-wrap: wrap; }
.ec-ed-row .btn { display: inline-flex; align-items: center; gap: 4px; }
.ec-ed-range { font-size: 12px; color: var(--text-2); margin-right: auto; }
</style>
