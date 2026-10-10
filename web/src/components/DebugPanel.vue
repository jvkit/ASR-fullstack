<script setup lang="ts">
// 调试管道：横向流水线节点 + 各环节明细折叠卡（数据全来自 /api/meetings/{id}/debug*）
import { computed, onMounted, ref, watch } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import { fmtMs as fmtMsShared } from '../ui/format'
import { showToast } from '../ui/toast'

// scope：页面级版本切换（2026-10-01）——内部三处小开关默认跟随，仍可单独切
const props = defineProps<{ meetingId: string; scope?: 'live' | 'final' }>()

interface EventItem { id: number; stage: string; detail: Record<string, unknown>; created_at: string }
interface DiarSeg { id: number; start_ms: number; end_ms: number; cluster_label: string; matched_speaker: string; score?: number; audio_path?: string; source: string }
interface Utt { id: number; seq: number; speaker: string; raw_text: string; corrected_text: string; text: string; start_ms: number; end_ms: number; audio_path?: string; diar_score?: number }
interface VisualNote { id: number; start_ms: number; end_ms: number; text: string; frame_paths: string[] }
interface SummaryLog { id: number; seq: number; scope: string; model: string; prompt_json: unknown[]; raw_response: string; duration_ms: number; created_at: string }
interface Overview { title: string; status: string; version_mode: string; source_mode: string; started_at: string; ended_at: string; audio_raw_path?: string; audio_denoised_path?: string; recordings_dir: string; counts: Record<string, number>; events: EventItem[] }

const loading = ref(false)
const overview = ref<Overview | null>(null)
const diarSource = ref<'live' | 'final'>('final')
const diarSegs = ref<DiarSeg[]>([])
const uttScope = ref<'live' | 'final'>('final')
const utterances = ref<Utt[]>([])
const visualNotes = ref<VisualNote[]>([])
const logScope = ref<'live' | 'final' | ''>('')
const summaryLogs = ref<SummaryLog[]>([])
const openSections = ref<string[]>(['overview'])
const playingAudio = ref<string | null>(null)

const apiBase = '/api'

// ---------- 流水线节点：从 overview 计数/落盘状态推导 ----------
interface PipeNode { key: string; label: string; icon: string; stat: string; section: string }
const pipeNodes = computed<PipeNode[]>(() => {
  const o = overview.value
  const c = o?.counts || {}
  return [
    { key: 'capture', label: '采集', icon: 'mic', stat: o?.audio_raw_path ? '录音已落盘' : '无录音', section: 'overview' },
    { key: 'denoise', label: '降噪', icon: 'volume', stat: o?.audio_denoised_path ? 'GTCRN 已落盘' : '无降噪版', section: 'overview' },
    { key: 'asr', label: 'ASR 转写', icon: 'doc', stat: `${c.utterances ?? 0} 句`, section: 'utts' },
    { key: 'diar', label: '分人', icon: 'users', stat: `${c.diar_segments ?? 0} 段`, section: 'diar' },
    { key: 'llm', label: '纠错 / 纪要 LLM', icon: 'tune', stat: `${c.summary_llm_logs ?? 0} 次调用`, section: 'logs' },
    { key: 'ocr', label: '视觉 OCR', icon: 'eye', stat: `${c.visual_notes ?? 0} 组`, section: 'visual' },
  ]
})

function toggleSection(name: string) {
  const i = openSections.value.indexOf(name)
  if (i >= 0) openSections.value.splice(i, 1)
  else openSections.value.push(name)
}

function gotoSection(name: string) {
  if (!openSections.value.includes(name)) openSections.value.push(name)
  setTimeout(() => {
    document.querySelector(`[data-section="${name}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 50)
}

function fmtTime(ms: number): string {
  return fmtMsShared(ms)
}

function audioUrl(rel?: string): string | undefined {
  if (!rel) return undefined
  return `${apiBase}/meetings/${props.meetingId}/debug/audio/${encodeURIComponent(rel)}`
}

function visualUrl(rel: string): string {
  return `${apiBase}/meetings/${props.meetingId}/debug/visual/${encodeURIComponent(rel)}`
}

function openVisual(rel: string) {
  window.open(visualUrl(rel), '_blank')
}

async function loadOverview() {
  const r = await fetch(`${apiBase}/meetings/${props.meetingId}/debug`)
  overview.value = await r.json()
}

async function loadDiar() {
  const r = await fetch(`${apiBase}/meetings/${props.meetingId}/debug/diar_segments?source=${diarSource.value}`)
  diarSegs.value = await r.json()
}

async function loadUtts() {
  const r = await fetch(`${apiBase}/meetings/${props.meetingId}/debug/utterances?scope=${uttScope.value}`)
  utterances.value = await r.json()
}

async function loadVisual() {
  const r = await fetch(`${apiBase}/meetings/${props.meetingId}/debug/visual_notes`)
  visualNotes.value = await r.json()
}

async function loadLogs() {
  const qs = logScope.value ? `?scope=${logScope.value}` : ''
  const r = await fetch(`${apiBase}/meetings/${props.meetingId}/debug/summary_logs${qs}`)
  summaryLogs.value = await r.json()
}

async function loadAll() {
  loading.value = true
  try {
    await Promise.all([loadOverview(), loadDiar(), loadUtts(), loadVisual(), loadLogs()])
  } finally {
    loading.value = false
  }
}

function playSegment(path?: string) {
  if (!path) return
  const url = audioUrl(path)
  if (!url) return
  playingAudio.value = path
  const a = new Audio(url)
  a.play().finally(() => { playingAudio.value = null })
}

function copyPrompt(log: SummaryLog) {
  const text = JSON.stringify(log.prompt_json, null, 2)
  navigator.clipboard.writeText(text).then(() => showToast('已复制 prompt', 'success'))
}

onMounted(loadAll)

// 页面级版本切换 → 内部默认跟随（初次挂载也同步一次）
watch(
  () => props.scope,
  (v) => {
    if (v !== 'live' && v !== 'final') return
    diarSource.value = v
    uttScope.value = v
    logScope.value = v
    loadDiar()
    loadUtts()
    loadLogs()
  },
  { immediate: true },
)
</script>

<template>
  <div class="debug">
    <div class="debug-head">
      <span class="debug-title">调试管道：{{ overview?.title || props.meetingId }}</span>
      <button class="btn btn-sm" :disabled="loading" @click="loadAll">
        <AppIcon name="refresh" :size="13" /> {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <!-- 流水线总览：节点点击跳明细 -->
    <div class="card pipeline">
      <template v-for="(n, i) in pipeNodes" :key="n.key">
        <button class="pipe-node" @click="gotoSection(n.section)">
          <span class="pipe-icon"><AppIcon :name="n.icon" :size="16" /></span>
          <span class="pipe-label">{{ n.label }}</span>
          <span class="pipe-stat num">{{ n.stat }}</span>
        </button>
        <span v-if="i < pipeNodes.length - 1" class="pipe-arrow">→</span>
      </template>
    </div>

    <!-- 总览 / 事件时间线 -->
    <div class="card sec" data-section="overview">
      <button class="sec-head" @click="toggleSection('overview')">
        <AppIcon name="chevronDown" :size="14" class="sec-caret" :class="{ closed: !openSections.includes('overview') }" />
        <span>总览 / 事件时间线</span>
      </button>
      <div v-if="openSections.includes('overview')" class="sec-body">
        <div v-if="overview" class="section">
          <div class="kv">
            <span>状态：{{ overview.status }}</span>
            <span>模式：{{ overview.version_mode }} / {{ overview.source_mode }}</span>
            <span>原始录音：{{ overview.audio_raw_path ? '已落盘' : '无' }}</span>
            <span>降噪录音：{{ overview.audio_denoised_path ? '已落盘' : '无' }}</span>
          </div>
          <div class="counts">
            <span v-for="(v, k) in overview.counts" :key="k" class="chip num">{{ k }}: {{ v }}</span>
          </div>
          <div class="events">
            <div v-for="e in overview.events" :key="e.id" class="event">
              <span class="event-stage">{{ e.stage }}</span>
              <span class="event-time num">{{ new Date(e.created_at).toLocaleTimeString('zh-CN') }}</span>
              <span class="event-detail">{{ JSON.stringify(e.detail) }}</span>
            </div>
            <div v-if="!overview.events.length" class="empty">无事件记录</div>
          </div>
        </div>
        <div v-else class="skeleton" style="height: 80px" />
      </div>
    </div>

    <!-- VAD / 3D 原始切段 -->
    <div class="card sec" data-section="diar">
      <button class="sec-head" @click="toggleSection('diar')">
        <AppIcon name="chevronDown" :size="14" class="sec-caret" :class="{ closed: !openSections.includes('diar') }" />
        <span>VAD / 3D 原始切段</span>
        <span class="chip num">{{ diarSegs.length }}</span>
      </button>
      <div v-if="openSections.includes('diar')" class="sec-body">
        <div class="seg">
          <button class="seg-item" :class="{ on: diarSource === 'final' }" @click="diarSource = 'final'; loadDiar()">会末版</button>
          <button class="seg-item" :class="{ on: diarSource === 'live' }" @click="diarSource = 'live'; loadDiar()">实时版</button>
        </div>
        <div v-if="diarSegs.length" class="list">
          <div v-for="s in diarSegs" :key="s.id" class="row">
            <span class="time num">{{ fmtTime(s.start_ms) }}-{{ fmtTime(s.end_ms) }}</span>
            <span class="chip">{{ s.cluster_label || '-' }}</span>
            <span class="spk">{{ s.matched_speaker }}</span>
            <span class="score num">{{ s.score != null ? s.score.toFixed(3) : '-' }}</span>
            <button v-if="s.audio_path" class="btn btn-ghost btn-sm" @click="playSegment(s.audio_path)">
              <AppIcon name="play" :size="11" /> {{ playingAudio === s.audio_path ? '播放中…' : '试听' }}
            </button>
          </div>
        </div>
        <div v-else class="empty">无切段数据</div>
      </div>
    </div>

    <!-- ASR 句子（原文 vs 纠错后） -->
    <div class="card sec" data-section="utts">
      <button class="sec-head" @click="toggleSection('utts')">
        <AppIcon name="chevronDown" :size="14" class="sec-caret" :class="{ closed: !openSections.includes('utts') }" />
        <span>ASR 句子（原文 vs 纠错后）</span>
        <span class="chip num">{{ utterances.length }}</span>
      </button>
      <div v-if="openSections.includes('utts')" class="sec-body">
        <div class="seg">
          <button class="seg-item" :class="{ on: uttScope === 'final' }" @click="uttScope = 'final'; loadUtts()">会末版</button>
          <button class="seg-item" :class="{ on: uttScope === 'live' }" @click="uttScope = 'live'; loadUtts()">实时版</button>
        </div>
        <div v-if="utterances.length" class="list">
          <div v-for="u in utterances" :key="u.id" class="utt-row">
            <div class="utt-head">
              <span class="time num">{{ fmtTime(u.start_ms) }}-{{ fmtTime(u.end_ms) }}</span>
              <span class="chip">{{ u.speaker || '未识别' }}</span>
              <span class="score num">score: {{ u.diar_score != null ? u.diar_score.toFixed(3) : '-' }}</span>
              <button v-if="u.audio_path" class="btn btn-ghost btn-sm" @click="playSegment(u.audio_path)">
                <AppIcon name="play" :size="11" /> {{ playingAudio === u.audio_path ? '播放中…' : '试听' }}
              </button>
            </div>
            <div class="utt-line raw">原文：{{ u.raw_text }}</div>
            <div v-if="u.corrected_text && u.corrected_text !== u.raw_text" class="utt-line corrected">
              纠错：{{ u.corrected_text }}
            </div>
          </div>
        </div>
        <div v-else class="empty">无句子数据</div>
      </div>
    </div>

    <!-- 视觉 OCR / 帧图 -->
    <div class="card sec" data-section="visual">
      <button class="sec-head" @click="toggleSection('visual')">
        <AppIcon name="chevronDown" :size="14" class="sec-caret" :class="{ closed: !openSections.includes('visual') }" />
        <span>视觉 OCR / 帧图</span>
        <span class="chip num">{{ visualNotes.length }}</span>
      </button>
      <div v-if="openSections.includes('visual')" class="sec-body">
        <div v-if="visualNotes.length" class="visual-list">
          <div v-for="n in visualNotes" :key="n.id" class="note">
            <div class="note-head num">{{ fmtTime(n.start_ms) }}-{{ fmtTime(n.end_ms) }}</div>
            <pre class="note-text">{{ n.text }}</pre>
            <div class="frames">
              <img
                v-for="(p, i) in n.frame_paths"
                :key="i"
                :src="visualUrl(p)"
                class="frame"
                alt="抽帧"
                @click="openVisual(p)"
              />
            </div>
          </div>
        </div>
        <div v-else class="empty">无视觉数据</div>
      </div>
    </div>

    <!-- LLM 波次调用记录 -->
    <div class="card sec" data-section="logs">
      <button class="sec-head" @click="toggleSection('logs')">
        <AppIcon name="chevronDown" :size="14" class="sec-caret" :class="{ closed: !openSections.includes('logs') }" />
        <span>LLM 波次调用记录</span>
        <span class="chip num">{{ summaryLogs.length }}</span>
      </button>
      <div v-if="openSections.includes('logs')" class="sec-body">
        <div class="seg">
          <button class="seg-item" :class="{ on: logScope === '' }" @click="logScope = ''; loadLogs()">全部</button>
          <button class="seg-item" :class="{ on: logScope === 'final' }" @click="logScope = 'final'; loadLogs()">会末版</button>
          <button class="seg-item" :class="{ on: logScope === 'live' }" @click="logScope = 'live'; loadLogs()">实时版</button>
        </div>
        <div v-if="summaryLogs.length" class="log-list">
          <div v-for="log in summaryLogs" :key="log.id" class="log">
            <div class="log-head">
              <span class="chip" :class="log.scope === 'final' ? 'chip-green' : 'chip-accent'">{{ log.scope }}</span>
              <span class="num">seq={{ log.seq }}</span>
              <span>{{ log.model }}</span>
              <span class="num">{{ log.duration_ms }}ms</span>
              <button class="btn btn-ghost btn-sm" @click="copyPrompt(log)">
                <AppIcon name="copy" :size="12" /> 复制 prompt
              </button>
            </div>
            <pre class="log-resp">{{ log.raw_response }}</pre>
          </div>
        </div>
        <div v-else class="empty">无 LLM 日志</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.debug { display: flex; flex-direction: column; gap: 12px; }
.debug-head { display: flex; align-items: center; gap: 12px; }
.debug-title { font-weight: 600; font-size: 15px; flex: 1; }

/* 流水线 */
.pipeline {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 14px 16px;
  overflow-x: auto;
}
.pipe-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 8px 14px;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--radius-ctl);
  font-family: var(--font);
  cursor: pointer;
  transition: all 0.15s ease-out;
  min-width: 86px;
}
.pipe-node:hover { background: var(--accent-soft); border-color: #d8e1ff; }
.pipe-icon {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: #f0f1f4;
  color: var(--text-2);
  display: flex;
  align-items: center;
  justify-content: center;
}
.pipe-node:hover .pipe-icon { background: #fff; color: var(--accent); }
.pipe-label { font-size: 12px; font-weight: 600; color: var(--text-1); white-space: nowrap; }
.pipe-stat { font-size: 11px; color: var(--text-3); white-space: nowrap; }
.pipe-arrow { color: var(--border-strong); flex-shrink: 0; }

/* 折叠卡 */
.sec { overflow: hidden; }
.sec-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 12px 16px;
  font-size: 13.5px;
  font-weight: 600;
  font-family: var(--font);
  color: var(--text-1);
  background: none;
  border: none;
  cursor: pointer;
  text-align: left;
}
.sec-head:hover { background: #fafbfc; }
.sec-caret { color: var(--text-3); transition: transform 0.18s ease-out; }
.sec-caret.closed { transform: rotate(-90deg); }
.sec-body { padding: 4px 16px 16px; border-top: 1px solid var(--border); }
.section { padding-top: 8px; }

.kv { display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--text-2); margin-bottom: 10px; padding-top: 8px; }
.counts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.events {
  max-height: 260px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-ctl);
  padding: 8px 12px;
}
.event { display: flex; gap: 10px; font-size: 12px; padding: 4px 0; border-bottom: 1px solid #f2f3f6; }
.event:last-child { border-bottom: none; }
.event-stage { width: 130px; flex-shrink: 0; color: var(--accent); font-weight: 500; }
.event-time { width: 80px; flex-shrink: 0; color: var(--text-3); }
.event-detail { flex: 1; color: var(--text-2); word-break: break-all; }

.list { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.row, .utt-row, .note, .log {
  border: 1px solid var(--border);
  border-radius: var(--radius-ctl);
  padding: 8px 12px;
}
.row { display: flex; align-items: center; gap: 10px; }
.time { color: var(--text-3); font-size: 12px; min-width: 80px; font-variant-numeric: tabular-nums; }
.spk { flex: 1; font-size: 13px; }
.score { color: var(--text-3); font-size: 12px; min-width: 60px; font-variant-numeric: tabular-nums; }

.utt-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.utt-line { font-size: 13px; color: var(--text-1); word-break: break-word; }
.utt-line.raw { color: var(--text-3); }
.utt-line.corrected { color: var(--green); }

.visual-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.note-head { font-size: 12px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.note-text {
  white-space: pre-wrap;
  font-size: 12px;
  font-family: var(--font);
  color: var(--text-2);
  background: #f7f8fa;
  padding: 8px 10px;
  border-radius: 6px;
  margin: 6px 0;
}
.frames { display: flex; flex-wrap: wrap; gap: 6px; }
.frame {
  width: 140px;
  height: 88px;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: zoom-in;
  transition: transform 0.15s ease-out;
}
.frame:hover { transform: scale(1.03); }

.log-list { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.log-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; font-size: 12.5px; color: var(--text-2); }
.log-head .btn { margin-left: auto; }
.log-resp {
  margin: 0;
  padding: 10px 12px;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--text-2);
  background: #f7f8fa;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 220px;
  overflow-y: auto;
}
</style>
