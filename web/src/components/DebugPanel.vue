<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

const props = defineProps<{ meetingId: string }>()

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
const activeCollapse = ref<string[]>(['overview'])
const playingAudio = ref<string | null>(null)

const apiBase = '/api'

function fmtTime(ms: number): string {
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

function audioUrl(rel?: string): string | undefined {
  if (!rel) return undefined
  return `${apiBase}/meetings/${props.meetingId}/debug/audio/${encodeURIComponent(rel)}`
}

function visualUrl(rel: string): string {
  return `${apiBase}/meetings/${props.meetingId}/debug/visual/${encodeURIComponent(rel)}`
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
  navigator.clipboard.writeText(text).then(() => alert('已复制 prompt'))
}

onMounted(loadAll)
</script>

<template>
  <div v-loading="loading" class="debug">
    <div class="debug-head">
      <span class="debug-title">调试视图：{{ overview?.title || props.meetingId }}</span>
      <el-button size="small" type="primary" plain @click="loadAll">刷新</el-button>
    </div>

    <el-collapse v-model="activeCollapse">
      <el-collapse-item title="总览 / 事件时间线" name="overview">
        <div v-if="overview" class="section">
          <div class="kv">
            <span>状态：{{ overview.status }}</span>
            <span>模式：{{ overview.version_mode }} / {{ overview.source_mode }}</span>
            <span>原始录音：{{ overview.audio_raw_path ? '已落盘' : '无' }}</span>
            <span>降噪录音：{{ overview.audio_denoised_path ? '已落盘' : '无' }}</span>
          </div>
          <div class="counts">
            <el-tag v-for="(v, k) in overview.counts" :key="k" type="info">{{ k }}: {{ v }}</el-tag>
          </div>
          <div class="events">
            <div v-for="e in overview.events" :key="e.id" class="event">
              <span class="event-stage">{{ e.stage }}</span>
              <span class="event-time">{{ new Date(e.created_at).toLocaleTimeString('zh-CN') }}</span>
              <span class="event-detail">{{ JSON.stringify(e.detail) }}</span>
            </div>
          </div>
        </div>
      </el-collapse-item>

      <el-collapse-item title="VAD / 3D 原始切段" name="diar">
        <div class="section">
          <el-radio-group v-model="diarSource" size="small" @change="loadDiar">
            <el-radio-button label="final">会末版</el-radio-button>
            <el-radio-button label="live">实时版</el-radio-button>
          </el-radio-group>
          <div v-if="diarSegs.length" class="list">
            <div v-for="s in diarSegs" :key="s.id" class="row">
              <span class="time">{{ fmtTime(s.start_ms) }}-{{ fmtTime(s.end_ms) }}</span>
              <el-tag size="small">{{ s.cluster_label || '-' }}</el-tag>
              <span class="spk">{{ s.matched_speaker }}</span>
              <span class="score">{{ s.score != null ? s.score.toFixed(3) : '-' }}</span>
              <el-button v-if="s.audio_path" size="small" link @click="playSegment(s.audio_path)">
                {{ playingAudio === s.audio_path ? '播放中…' : '试听' }}
              </el-button>
            </div>
          </div>
          <el-empty v-else description="无切段数据" />
        </div>
      </el-collapse-item>

      <el-collapse-item title="ASR 句子（原文 vs 纠错后）" name="utts">
        <div class="section">
          <el-radio-group v-model="uttScope" size="small" @change="loadUtts">
            <el-radio-button label="final">会末版</el-radio-button>
            <el-radio-button label="live">实时版</el-radio-button>
          </el-radio-group>
          <div v-if="utterances.length" class="list">
            <div v-for="u in utterances" :key="u.id" class="utt-row">
              <div class="utt-head">
                <span class="time">{{ fmtTime(u.start_ms) }}-{{ fmtTime(u.end_ms) }}</span>
                <el-tag size="small" type="info">{{ u.speaker || '未识别' }}</el-tag>
                <span class="score">score: {{ u.diar_score != null ? u.diar_score.toFixed(3) : '-' }}</span>
                <el-button v-if="u.audio_path" size="small" link @click="playSegment(u.audio_path)">
                  {{ playingAudio === u.audio_path ? '播放中…' : '试听' }}
                </el-button>
              </div>
              <div class="utt-line raw">原文：{{ u.raw_text }}</div>
              <div v-if="u.corrected_text && u.corrected_text !== u.raw_text" class="utt-line corrected">
                纠错：{{ u.corrected_text }}
              </div>
            </div>
          </div>
          <el-empty v-else description="无句子数据" />
        </div>
      </el-collapse-item>

      <el-collapse-item title="视觉 OCR / 帧图" name="visual">
        <div class="section">
          <div v-if="visualNotes.length" class="visual-list">
            <div v-for="n in visualNotes" :key="n.id" class="note">
              <div class="note-head">{{ fmtTime(n.start_ms) }}-{{ fmtTime(n.end_ms) }}</div>
              <pre class="note-text">{{ n.text }}</pre>
              <div class="frames">
                <img v-for="(p, i) in n.frame_paths" :key="i" :src="visualUrl(p)" class="frame" @click="window.open(visualUrl(p))" />
              </div>
            </div>
          </div>
          <el-empty v-else description="无视觉数据" />
        </div>
      </el-collapse-item>

      <el-collapse-item title="LLM 波次调用记录" name="logs">
        <div class="section">
          <el-radio-group v-model="logScope" size="small" @change="loadLogs">
            <el-radio-button label="">全部</el-radio-button>
            <el-radio-button label="final">会末版</el-radio-button>
            <el-radio-button label="live">实时版</el-radio-button>
          </el-radio-group>
          <div v-if="summaryLogs.length" class="log-list">
            <div v-for="log in summaryLogs" :key="log.id" class="log">
              <div class="log-head">
                <el-tag size="small">{{ log.scope }}</el-tag>
                <span>seq={{ log.seq }}</span>
                <span>{{ log.model }}</span>
                <span>{{ log.duration_ms }}ms</span>
                <el-button size="small" link @click="copyPrompt(log)">复制 prompt</el-button>
              </div>
              <el-input type="textarea" :rows="6" readonly :model-value="log.raw_response" />
            </div>
          </div>
          <el-empty v-else description="无 LLM 日志" />
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.debug { padding: 12px; }
.debug-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.debug-title { font-weight: 600; font-size: 16px; flex: 1; }
.section { padding: 8px 0; }
.kv { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; color: #606266; margin-bottom: 8px; }
.counts { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.events { max-height: 260px; overflow-y: auto; border: 1px solid #ebeef5; border-radius: 4px; padding: 8px; }
.event { display: flex; gap: 8px; font-size: 12px; padding: 3px 0; border-bottom: 1px solid #f2f2f2; }
.event-stage { width: 120px; flex-shrink: 0; color: #409eff; font-weight: 500; }
.event-time { width: 80px; flex-shrink: 0; color: #909399; }
.event-detail { flex: 1; color: #606266; word-break: break-all; }
.list { margin-top: 10px; }
.row, .utt-row, .note, .log { border: 1px solid #ebeef5; border-radius: 4px; padding: 8px; margin-bottom: 8px; }
.row { display: flex; align-items: center; gap: 10px; }
.time { color: #909399; font-size: 12px; min-width: 80px; }
.spk { flex: 1; }
.score { color: #909399; font-size: 12px; min-width: 60px; }
.utt-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.utt-line { font-size: 13px; color: #303133; word-break: break-word; }
.utt-line.raw { color: #909399; }
.utt-line.corrected { color: #67c23a; }
.note-text { white-space: pre-wrap; font-size: 12px; color: #606266; background: #f5f7fa; padding: 6px; border-radius: 4px; margin: 6px 0; }
.frames { display: flex; flex-wrap: wrap; gap: 6px; }
.frame { width: 120px; height: 80px; object-fit: cover; border-radius: 4px; cursor: pointer; }
.log-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
</style>
