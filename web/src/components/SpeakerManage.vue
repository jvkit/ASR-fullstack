<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import UiModal from '../ui/UiModal.vue'
import { fmtDur } from '../ui/format'
import { showToast } from '../ui/toast'
import { MicRecorder } from '../ws/audio'

const speakers = ref<string[]>([])
const loading = ref(false)
const errorMsg = ref('')
const okMsg = ref('')
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

// 注册样本数：列表接口只给名字，逐人查 segments 补计数（人数少，一次全拉）
const segCounts = reactive<Record<string, number>>({})

// 声纹卡配色（与开会页同一调色板，按列表序循环）
const palette = ['#2f5cff', '#0f9d6c', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#db2777', '#65a30d']
function colorOf(name: string): string {
  const i = speakers.value.indexOf(name)
  return palette[(i < 0 ? 0 : i) % palette.length]
}

// ---------- 录入质量反馈：音量 ----------
const level = ref(0) // rms 0~1
const silentSec = ref(0)
let levelWatch: ReturnType<typeof setInterval> | null = null

function dbPct(rms: number): number {
  const db = 20 * Math.log10(rms + 1e-6)
  return Math.min(100, Math.max(0, ((db + 60) / 60) * 100))
}

function watchLevel() {
  levelWatch = setInterval(() => {
    if (level.value < 0.003) silentSec.value += 0.1
    else silentSec.value = 0
  }, 100)
}

function stopWatchLevel() {
  if (levelWatch) clearInterval(levelWatch)
  levelWatch = null
  level.value = 0
  silentSec.value = 0
}

// ---------- 注册 ----------
const showEnroll = ref(false) // 注册弹窗
const form = reactive({ name: '' })
const mic = reactive(new MicRecorder())
const recording = ref(false)
const recorded = ref(0)
const uploading = ref(false)
const report = ref<any>(null) // 注册报告（kept/rejected 明细）
let chunks: ArrayBuffer[] = []
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const r = await fetch('/api/speakers')
    const d = await r.json()
    speakers.value = d.speakers || []
    refreshSegCounts()
    checkAllQuality()  // 加载即批量自查质量，弱的标红置顶可见
  } catch (e) {
    errorMsg.value = `加载失败：${e}`
  } finally {
    loading.value = false
  }
}

async function refreshSegCounts() {
  await Promise.all(
    speakers.value.map(async (name) => {
      try {
        const r = await fetch(`/api/speakers/${encodeURIComponent(name)}/segments`)
        const d = await r.json()
        segCounts[name] = (d.segments || []).length
      } catch { /* 计数失败不阻塞展示 */ }
    }),
  )
}

onMounted(() => {
  load()
  // http 访问非 localhost 时 navigator.mediaDevices 为 undefined，麦克风必然失败，提前提示
  if (typeof navigator !== 'undefined' && navigator.mediaDevices === undefined) {
    insecure.value = true
  }
})

function openEnroll() {
  form.name = ''
  report.value = null
  errorMsg.value = ''
  okMsg.value = ''
  showEnroll.value = true
}

async function closeEnroll() {
  if (recording.value) await cancelRecord()
  showEnroll.value = false
}

async function startRecord() {
  if (insecure.value) {
    errorMsg.value = '当前为 http 访问，浏览器禁止使用麦克风。请改用 https 地址打开本页。'
    return
  }
  errorMsg.value = ''
  okMsg.value = ''
  report.value = null
  chunks = []
  recorded.value = 0
  try {
    await mic.start(
      (bytes) => chunks.push(bytes),
      (rms) => (level.value = rms),
    )
  } catch (e) {
    recording.value = false
    const name = (e as DOMException)?.name
    errorMsg.value =
      name === 'NotAllowedError'
        ? '麦克风权限被拒绝：请点击浏览器地址栏左侧的锁形图标，允许麦克风后重试。'
        : `无法打开麦克风：${e}`
    return
  }
  recording.value = true
  watchLevel()
  timer = setInterval(() => {
    recorded.value = chunks.length * 0.1
  }, 200)
}

async function stopMic() {
  recording.value = false
  if (timer) clearInterval(timer)
  stopWatchLevel()
  await mic.stop()
}

async function cancelRecord() {
  await stopMic()
  chunks = []
  recorded.value = 0
}

async function stopAndEnroll() {
  await stopMic()
  const name = form.name.trim()
  if (!name) {
    errorMsg.value = '先填姓名'
    return
  }
  if (chunks.length < 30) {
    errorMsg.value = `音频太短（${(chunks.length * 0.1).toFixed(1)}s），请自然说 20~60 秒`
    return
  }
  uploading.value = true
  try {
    const pcm = new Blob(chunks as BlobPart[], { type: 'application/octet-stream' })
    const r = await fetch(`/api/speakers/enroll?name=${encodeURIComponent(name)}`, {
      method: 'POST',
      body: pcm,
    })
    const d = await r.json()
    report.value = d
    if (!r.ok || !d.ok) {
      errorMsg.value = d.detail || d.error || '注册失败'
      showToast('注册未通过，请按提示重录', 'error')
      return
    }
    speakers.value = d.speakers || (await (await fetch('/api/speakers')).json()).speakers || []
    refreshSegCounts()
    okMsg.value = d.msg
      ? `「${name}」${d.msg}`
      : `已注册「${name}」：有效语音 ${d.effective_sec}s / ${d.segments} 段`
    showEnroll.value = false
    form.name = ''
  } catch (e) {
    errorMsg.value = `注册失败：${e}`
  } finally {
    uploading.value = false
    chunks = []
    recorded.value = 0
  }
}

// ---------- 录入检测（说几秒 -> 我是谁；兼作注册质量自查） ----------
const idMic = reactive(new MicRecorder())
const idRecording = ref(false)
const idSec = ref(0)
const idLevel = ref(0)
const idSilent = ref(0)
const idBusy = ref(false)
const idResult = ref<any>(null)
let idChunks: ArrayBuffer[] = []
let idTimer: ReturnType<typeof setInterval> | null = null

async function idStart() {
  errorMsg.value = ''
  idResult.value = null
  idChunks = []
  idSec.value = 0
  try {
    await idMic.start(
      (bytes) => idChunks.push(bytes),
      (rms) => (idLevel.value = rms),
    )
  } catch (e) {
    idRecording.value = false
    errorMsg.value = `无法打开麦克风：${e}`
    return
  }
  idRecording.value = true
  levelWatch = setInterval(() => {
    if (idLevel.value < 0.003) idSilent.value += 0.1
    else idSilent.value = 0
  }, 100)
  idTimer = setInterval(() => {
    idSec.value = idChunks.length * 0.1
  }, 200)
}

async function idStop() {
  idRecording.value = false
  if (idTimer) clearInterval(idTimer)
  if (levelWatch) clearInterval(levelWatch)
  levelWatch = null
  idLevel.value = 0
  idSilent.value = 0
  await idMic.stop()
  if (idChunks.length < 25) {
    showToast('太短，请说 3~10 秒', 'info')
    idChunks = []
    return
  }
  idBusy.value = true
  try {
    const pcm = new Blob(idChunks as BlobPart[], { type: 'application/octet-stream' })
    const r = await fetch('/api/speakers/identify', { method: 'POST', body: pcm })
    const d = await r.json()
    if (!r.ok || !d.ok) {
      idResult.value = { fail: d.detail || d.error || '检测失败' }
      return
    }
    idResult.value = d
  } catch (e) {
    idResult.value = { fail: `检测失败：${e}` }
  } finally {
    idBusy.value = false
    idChunks = []
  }
}

async function appendStart(name: string) {
  // 补录：填好名字直接开始录音（后端同名自动走联合重选）
  form.name = name
  showEnroll.value = true
  await startRecord()
}

// ---------- 注册段试听（质检：点开听注册的声音是否本人，防"名字对人声"错位入库） ----------
const audition = reactive<Record<string, { open: boolean; loading: boolean; segs: any[] }>>({})
const playingKey = ref('') // "name#i"
let audioEl: HTMLAudioElement | null = null

async function toggleAudition(name: string) {
  // 注意：必须先写入再从 reactive 容器读回——读回的才是响应式代理；
  // 直接用赋值表达式的返回值（原始对象）改属性不会触发界面更新（首次试听"空的"的 bug 根因）
  if (!audition[name]) audition[name] = { open: false, loading: false, segs: [] }
  const a = audition[name]
  a.open = !a.open
  if (!a.open) stopPlay()
  if (a.open && !a.segs.length && !a.loading) {
    a.loading = true
    try {
      const r = await fetch(`/api/speakers/${encodeURIComponent(name)}/segments`)
      const d = await r.json()
      a.segs = d.segments || []
      segCounts[name] = a.segs.length
    } catch (e) {
      errorMsg.value = `加载试听段失败：${e}`
    } finally {
      a.loading = false
    }
  }
}

function stopPlay() {
  if (audioEl) {
    audioEl.pause()
    audioEl = null
  }
  playingKey.value = ''
}

function togglePlay(name: string, i: number) {
  const key = `${name}#${i}`
  if (playingKey.value === key) {
    stopPlay()
    return
  }
  stopPlay()
  audioEl = new Audio(`/api/speakers/${encodeURIComponent(name)}/segments/${i}/audio`)
  audioEl.onended = () => {
    if (playingKey.value === key) playingKey.value = ''
  }
  audioEl.onerror = () => {
    if (playingKey.value === key) playingKey.value = ''
    errorMsg.value = '试听音频加载失败'
  }
  // play() 返回 Promise：浏览器拒绝（如解码失败、被中断）时静默失败会显得"点了没反应"，
  // 捕获并报出来
  audioEl.play().catch((e) => {
    if (playingKey.value === key) playingKey.value = ''
    errorMsg.value = `试听播放失败：${e?.message || e}`
  })
  playingKey.value = key
}

// ---------- 重命名 ----------
const showRename = ref(false)
const renameForm = reactive({ old: '', new: '' })
const renaming = ref(false)

function openRename(name: string) {
  renameForm.old = name
  renameForm.new = name
  showRename.value = true
}

async function doRename() {
  const newName = renameForm.new.trim()
  if (!newName || newName === renameForm.old) {
    showRename.value = false
    return
  }
  if (speakers.value.includes(newName)) {
    errorMsg.value = `「${newName}」已存在，请换名或先删除`
    return
  }
  renaming.value = true
  try {
    const r = await fetch('/api/speakers/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_name: renameForm.old, new_name: newName }),
    })
    const d = await r.json()
    if (!r.ok || d.ok === false) {
      errorMsg.value = d.detail || d.error || '重命名失败'
      return
    }
    okMsg.value = `已重命名为「${newName}」`
    showRename.value = false
    await load()
  } finally {
    renaming.value = false
  }
}

// ---------- 注册段删除（单段剔除，质心自动重建；不整人删） ----------
const deletingSeg = ref('') // "name#i"

async function deleteSegment(name: string, i: number) {
  const key = `${name}#${i}`
  if (deletingSeg.value === key) return
  deletingSeg.value = key
  try {
    const r = await fetch('/api/speakers/segment_delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, i }),
    })
    const d = await r.json()
    if (!r.ok || d.ok === false) {
      errorMsg.value = d.detail || d.error || '删除失败'
      return
    }
    okMsg.value = `已删除「${name}」的段 ${i}（质心已重建）`
    if (audition[name]) {
      audition[name].segs = (audition[name].segs || []).filter((s: any) => s.i !== i)
      segCounts[name] = audition[name].segs.length
    }
    await checkQuality(name) // 删段后质量分可能变，重测
  } finally {
    deletingSeg.value = ''
  }
}

// ---------- 批量录入检测（每人自动测，标红弱的，大批量组测一眼看出谁不行） ----------
const qualityScores = reactive<Record<string, number | null>>({}) // name -> 自测相似度（本人识别分）
const qualityChecked = reactive<Record<string, boolean>>({})
// 段级质检（2026-10-09）：每段 vs 本人质心的相似度，低分 = 重点检查对象
const segScores = reactive<Record<string, Record<number, number>>>({}) // name -> {i: sim}
const segWarnSim = ref(0.5)

async function checkQuality(name: string): Promise<number | null> {
  // 拉段级分数（与整体分同源于 /segments_sim，一次请求两用）
  try {
    const r = await fetch(`/api/speakers/${encodeURIComponent(name)}/segment_scores`)
    if (r.ok) {
      const d = await r.json()
      segWarnSim.value = d.warn_sim ?? 0.5
      const m: Record<number, number> = {}
      let sum = 0, n = 0
      for (const s of d.scores || []) {
        m[s.i] = s.sim
        sum += s.sim; n++
      }
      segScores[name] = m
      if (n) return sum / n
    }
  } catch { /* 段级失败退回整体检测 */ }
  // 退回旧逻辑：用自己的注册段做 identify，取均值
  try {
    const segsR = await fetch(`/api/speakers/${encodeURIComponent(name)}/segments`)
    const segsD = await segsR.json()
    const segs = segsD.segments || []
    if (!segs.length) return null
    const scores: number[] = []
    for (const seg of segs.slice(0, 5)) {
      const audioR = await fetch(`/api/speakers/${encodeURIComponent(name)}/segments/${seg.i}/audio`)
      if (!audioR.ok) continue
      const pcm = await audioR.blob()
      const r = await fetch('/api/speakers/identify', { method: 'POST', body: pcm })
      const d = await r.json()
      if (!r.ok || !d.ok) continue
      const hit = (d.ranked || d.matches || []).find((x: any) => x.name === name)
      const s = hit ? hit.sim : d.best_sim ?? null
      if (typeof s === 'number') scores.push(s)
    }
    if (!scores.length) return null
    return scores.reduce((a, b) => a + b, 0) / scores.length
  } catch {
    return null
  }
}

async function checkAllQuality() {
  for (const name of speakers.value) {
    qualityScores[name] = await checkQuality(name)
    qualityChecked[name] = true
  }
}

function qualityClass(score: number | null): string {
  if (score == null) return ''
  if (score < 0.6) return 'q-bad'
  if (score < 0.75) return 'q-warn'
  return 'q-good'
}

// ---------- VAD 精修（2026-10-09）：长段切细 → 逐片听/叉 → 合成回库 ----------
interface Piece { id: number; src_seg: number; dur: number; audio_url: string; sim?: number | null }
const showSplit = ref(false)
const splitName = ref('')
const splitLoading = ref(false)
const splitPieces = ref<Piece[]>([])
const splitToken = ref('')
const splitWarnSim = ref(0.5)
const splitExcluded = ref<Set<number>>(new Set())
const splitPlaying = ref<number | null>(null)
const rebuilding = ref(false)
let splitAudio: HTMLAudioElement | null = null

async function openSplit(name: string) {
  splitName.value = name
  splitPieces.value = []
  splitToken.value = ''
  splitExcluded.value = new Set()
  showSplit.value = true
  splitLoading.value = true
  try {
    const r = await fetch(`/api/speakers/${encodeURIComponent(name)}/split`, { method: 'POST' })
    const d = await r.json()
    if (!r.ok) {
      showToast(d.detail || '切分失败', 'error')
      showSplit.value = false
      return
    }
    splitPieces.value = d.pieces || []
    splitToken.value = d.token
    splitWarnSim.value = d.warn_sim ?? 0.5
    // 默认勾选全部；有 sim 且 < warn 的预剔除（红色重点检查，由人决定）
    const ex = new Set<number>()
    for (const p of splitPieces.value) {
      if (typeof p.sim === 'number' && p.sim < splitWarnSim.value) ex.add(p.id)
    }
    splitExcluded.value = ex
  } catch {
    showToast('切分请求失败', 'error')
    showSplit.value = false
  } finally {
    splitLoading.value = false
  }
}

function togglePiece(id: number) {
  splitExcluded.value.has(id) ? splitExcluded.value.delete(id) : splitExcluded.value.add(id)
  splitExcluded.value = new Set(splitExcluded.value)
}

const splitKeptSec = computed(() =>
  splitPieces.value
    .filter((p) => !splitExcluded.value.has(p.id))
    .reduce((s, p) => s + p.dur, 0),
)

function stopSplitAudio() {
  if (splitAudio) { splitAudio.pause(); splitAudio = null }
  splitPlaying.value = null
}
function playPiece(p: Piece) {
  if (splitPlaying.value === p.id) { stopSplitAudio(); return }
  stopSplitAudio()
  splitAudio = new Audio(p.audio_url)
  splitPlaying.value = p.id
  splitAudio.onended = () => (splitPlaying.value = null)
  splitAudio.onerror = () => { splitPlaying.value = null; showToast('播放失败', 'error') }
  splitAudio.play().catch(() => (splitPlaying.value = null))
}

async function doRebuild() {
  const keep = splitPieces.value.filter((p) => !splitExcluded.value.has(p.id)).map((p) => p.id)
  if (!keep.length) { showToast('至少保留一片', 'error'); return }
  rebuilding.value = true
  try {
    const r = await fetch('/api/speakers/rebuild', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: splitName.value, token: splitToken.value, keep }),
    })
    const d = await r.json()
    if (!r.ok) { showToast(d.detail || '合成失败', 'error'); return }
    showToast(`已合成回库：${d.segments} 片 / 有效 ${d.effective_sec}s`, 'success', 4000)
    showSplit.value = false
    stopSplitAudio()
    await load()
  } finally {
    rebuilding.value = false
  }
}

const confirmName = ref('')
const deleting = ref(false)

function askRemove(name: string) {
  confirmName.value = name
}

async function doRemove() {
  const name = confirmName.value
  if (!name) return
  deleting.value = true
  try {
    const r = await fetch(`/api/speakers/${encodeURIComponent(name)}`, { method: 'DELETE' })
    const d = await r.json()
    if (!r.ok) {
      errorMsg.value = d.detail || '删除失败'
      return
    }
    speakers.value = d.speakers || []
    delete segCounts[name]
    okMsg.value = `已删除「${name}」（移除 ${(d.removed || []).length} 个注册段）`
  } finally {
    deleting.value = false
    confirmName.value = ''
  }
}
</script>

<template>
  <div class="speaker-page">
    <div class="page-head">
      <div>
        <h2 class="page-title">声纹库</h2>
        <p class="page-sub">注册参会人声纹后，开会时自动分人；未注册的发言统一显示「未识别」</p>
      </div>
      <button class="btn btn-primary" :disabled="insecure" @click="openEnroll">
        <AppIcon name="plus" :size="14" /> 新增注册
      </button>
    </div>

    <!-- 提示条 -->
    <div v-if="insecure" class="msg-bar error">
      <AppIcon name="warning" :size="14" />
      当前为 http 访问，浏览器禁止使用麦克风，请改用 https 地址打开
    </div>
    <div v-if="errorMsg" class="msg-bar error">
      <AppIcon name="warning" :size="14" />
      <span class="msg-text">{{ errorMsg }}</span>
      <button class="icon-btn msg-x" @click="errorMsg = ''"><AppIcon name="close" :size="12" /></button>
    </div>
    <div v-if="okMsg" class="msg-bar ok">
      <AppIcon name="check" :size="14" />
      <span class="msg-text">{{ okMsg }}</span>
      <button class="icon-btn msg-x" @click="okMsg = ''"><AppIcon name="close" :size="12" /></button>
    </div>

    <!-- 录入检测（我是谁） -->
    <div class="card block">
      <div class="block-title">录入检测（我是谁）</div>
      <p class="block-sub">随便说 3~10 秒，系统告诉你命中声纹库里的谁。注册完立刻测一次 = 最直接的注册质量检查</p>
      <div class="id-actions">
        <button v-if="!idRecording" class="btn btn-primary" :disabled="insecure || idBusy" @click="idStart">
          <AppIcon name="mic" :size="14" /> {{ idBusy ? '识别中…' : '开始检测' }}
        </button>
        <button v-else class="btn btn-danger-outline" @click="idStop">
          <AppIcon name="stop" :size="12" /> 结束并识别{{ idSec ? `（已录 ${idSec.toFixed(1)}s）` : '' }}
        </button>
        <div v-if="idRecording" class="meter-wrap">
          <div class="meter"><div class="meter-fill" :style="{ width: dbPct(idLevel) + '%' }" /></div>
          <span v-if="idSilent > 2" class="meter-warn">没检测到声音，请靠近麦克风说话</span>
        </div>
      </div>
      <div v-if="idResult" class="report">
        <template v-if="idResult.fail">
          <div class="rep-fail">✗ {{ idResult.fail }}</div>
        </template>
        <template v-else>
          <div v-if="idResult.verdict === 'matched'" class="rep-ok">
            ✓ 命中「{{ idResult.best }}」 相似度 {{ idResult.best_sim }}<template v-if="idResult.margin !== null">，领先第二名 {{ idResult.margin }}</template>
          </div>
          <div v-else-if="idResult.verdict === 'weak'" class="rep-warn">
            ⚠ 疑似「{{ idResult.best }}」（{{ idResult.best_sim }}），置信度偏低——如果你是本人，建议重新录入
          </div>
          <div v-else class="rep-fail">
            ✗ 未匹配到任何注册声纹（最高 {{ idResult.best_sim }}）——如果你是已注册人员，说明录入质量不够，建议重录
          </div>
          <div class="ranked">
            <div v-for="x in idResult.ranked.slice(0, 5)" :key="x.name" class="rank-row">
              <span class="rank-name">{{ x.name }}</span>
              <div class="rank-bar"><div class="rank-fill" :style="{ width: Math.max(2, x.sim * 100) + '%' }" /></div>
              <span class="rank-sim num">{{ x.sim }}</span>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 声纹卡片网格 -->
    <div v-if="loading" class="grid">
      <div v-for="i in 3" :key="i" class="card spk-card">
        <div class="skeleton" style="width: 44px; height: 44px; border-radius: 50%" />
        <div class="skeleton" style="width: 90px; height: 14px; margin-top: 10px" />
      </div>
    </div>
    <div v-else-if="!speakers.length" class="card empty">
      <div class="empty-icon"><AppIcon name="users" :size="24" /></div>
      暂无注册声纹，点右上角「新增注册」录入第一个参会人
    </div>
    <TransitionGroup v-else name="list" tag="div" class="grid">
      <div v-for="s in speakers" :key="s" class="card card-hover spk-card">
        <div class="spk-head">
          <span class="spk-avatar" :style="{ background: colorOf(s) + '1f', color: colorOf(s) }">
            {{ s.charAt(0) }}
          </span>
          <div class="spk-info">
            <div class="spk-name">
              {{ s }}
              <span v-if="qualityChecked[s] && qualityScores[s] != null"
                    class="q-badge num" :class="qualityClass(qualityScores[s])"
                    :title="'录入检测：用自己的注册段测当前质心，相似度 ' + qualityScores[s]?.toFixed(3)">
                {{ qualityScores[s]!.toFixed(2) }}
              </span>
            </div>
            <div class="spk-count num">
              {{ segCounts[s] ?? '…' }} 个注册样本
              <template v-if="qualityScores[s] != null && qualityScores[s]! < 0.6">
                <span class="q-warn-text">· 质量偏弱，建议补录</span>
              </template>
            </div>
          </div>
        </div>
        <div class="spk-actions">
          <button class="btn btn-sm" :class="{ 'btn-primary': audition[s]?.open }" @click="toggleAudition(s)">
            <AppIcon name="volume" :size="12" /> {{ audition[s]?.open ? '收起' : '试听' }}
          </button>
          <button class="btn btn-sm" @click="appendStart(s)">补录</button>
          <button class="btn btn-sm" title="VAD 切细 → 逐片听，把混入他人的片叉出去 → 合成回库" @click="openSplit(s)">VAD 精修</button>
          <button class="btn btn-sm" @click="openRename(s)">重命名</button>
          <button class="btn btn-sm btn-danger-outline" @click="askRemove(s)">
            <AppIcon name="trash" :size="12" />
          </button>
        </div>
        <!-- 试听段列表（展开） -->
        <div v-if="audition[s]?.open" class="seg-list">
          <div v-if="audition[s]?.loading" class="seg-tip">加载中…</div>
          <template v-else>
            <div v-if="!audition[s]?.segs?.length" class="seg-tip">没有可试听的注册段</div>
            <div v-for="seg in audition[s]?.segs" :key="seg.i" class="seg-row"
                 :class="{ segsimlow: segScores[s] && segScores[s][seg.i] != null && segScores[s][seg.i] < segWarnSim }">
              <span class="seg-name">段 {{ seg.i }}</span>
              <span class="seg-dur num">{{ fmtDur(seg.dur) }}</span>
              <span
                v-if="segScores[s] && segScores[s][seg.i] != null"
                class="segsim num"
                :class="{ bad: segScores[s][seg.i] < segWarnSim }"
                :title="segScores[s][seg.i] < segWarnSim ? '与本人质心相似度低，疑似混入他人/太噪，建议点听确认' : '与本人质心相似度'"
              >{{ segScores[s][seg.i].toFixed(2) }}</span>
              <button
                class="btn btn-sm"
                :class="{ 'btn-danger-outline': playingKey === s + '#' + seg.i }"
                @click="togglePlay(s, seg.i)"
              >
                <AppIcon :name="playingKey === s + '#' + seg.i ? 'stop' : 'play'" :size="11" />
                {{ playingKey === s + '#' + seg.i ? '停止' : '播放' }}
              </button>
              <button
                class="btn btn-sm btn-ghost seg-del"
                :disabled="deletingSeg === s + '#' + seg.i"
                title="删掉这一段（混入他人声音/太噪时），其余段保留、质心自动重建"
                @click="deleteSegment(s, seg.i)"
              >
                <AppIcon name="trash" :size="11" />
              </button>
            </div>
            <div class="seg-tip">点开听是否本人声音；某段混了别人/太噪就单独删，不用整人重录</div>
          </template>
        </div>
      </div>
    </TransitionGroup>

    <!-- 注册弹窗 -->
    <UiModal :show="showEnroll" title="注册新声纹" @close="closeEnroll">
      <label class="field-label" for="enroll-name">姓名</label>
      <input id="enroll-name" v-model="form.name" class="input" placeholder="如：张三" :disabled="recording" />
      <div v-if="form.name.trim() && speakers.includes(form.name.trim())" class="append-tip">
        「{{ form.name.trim() }}」已在库中：本次录入将与既有声纹<b>联合重选（补录）</b>——新旧片段同台竞争，合格的留下重建质心
      </div>
      <p class="enroll-tip">
        对着开会用的同一台设备，自然说 20~60 秒（可以停顿、想词）。系统自动挑出最清晰、最像你的
        3~5 个片段；太短/太轻/疑似他人或环境声的片段会被丢弃。没录够可以同名再录，合格片段自动累积
      </p>

      <div class="enroll-actions">
        <button
          v-if="!recording"
          class="btn btn-primary btn-lg enroll-btn"
          :disabled="insecure || !form.name.trim() || uploading"
          @click="startRecord"
        >
          <span class="rec-dot-inline" /> {{ uploading ? '注册中…' : '开始录音' }}
        </button>
        <template v-else>
          <div class="meter-wrap">
            <div class="meter"><div class="meter-fill" :style="{ width: dbPct(level) + '%' }" /></div>
            <span v-if="silentSec > 2" class="meter-warn">没检测到声音，请靠近麦克风说话</span>
          </div>
          <div class="btn-row">
            <button class="btn" @click="cancelRecord">取消</button>
            <button class="btn btn-primary" @click="stopAndEnroll">
              <AppIcon name="stop" :size="12" /> 完成并注册{{ recorded ? `（已录 ${recorded.toFixed(1)}s）` : '' }}
            </button>
          </div>
        </template>
      </div>

      <div v-if="report && report.rejected && report.rejected.length" class="report">
        <div class="rep-title">丢弃 {{ report.rejected.length }} 段（录入清洗）</div>
        <div v-for="(r, i) in report.rejected" :key="i" class="rep-row">· {{ fmtDur(r.dur) }}：{{ r.reason }}</div>
      </div>
    </UiModal>

    <!-- 删除确认弹窗 -->
    <UiModal :show="!!confirmName" title="删除声纹" :width="400" @close="confirmName = ''">
      <p class="confirm-text">
        确定删除「<b>{{ confirmName }}</b>」？删除后立即不再被识别。
      </p>
      <template #footer>
        <button class="btn" @click="confirmName = ''">取消</button>
        <button class="btn btn-danger-outline" :disabled="deleting" @click="doRemove">
          {{ deleting ? '删除中…' : '确认删除' }}
        </button>
      </template>
    </UiModal>

    <!-- 重命名弹窗 -->
    <UiModal :show="showRename" title="重命名声纹" :width="400" @close="showRename = false">
      <p class="confirm-text">把「<b>{{ renameForm.old }}</b>」改名为：</p>
      <input v-model="renameForm.new" class="input" placeholder="新姓名" maxlength="32" @keyup.enter="doRename" />
      <template #footer>
        <button class="btn" @click="showRename = false">取消</button>
        <button class="btn btn-primary" :disabled="renaming || !renameForm.new.trim() || renameForm.new.trim() === renameForm.old" @click="doRename">
          {{ renaming ? '重命名中…' : '确认重命名' }}
        </button>
      </template>
    </UiModal>

    <!-- VAD 精修弹窗：长段切细 → 逐片听/叉 → 合成回库 -->
    <UiModal :show="showSplit" :title="`VAD 精修 · ${splitName}`" :width="640" @close="showSplit = false; stopSplitAudio()">
      <div v-if="splitLoading" class="split-tip">VAD 切分中（长段会切几秒~几十秒）…</div>
      <template v-else>
        <div class="split-tip">
          已切成 {{ splitPieces.length }} 片。逐片试听，把混入他人/太噪的<b>取消勾选</b>（红色 = 相似度低，已预剔除）；
          满意后点「合成回库」，勾选的片整批替换原注册段。
        </div>
        <div class="split-list">
          <div v-for="p in splitPieces" :key="p.id" class="split-row"
               :class="{ off: splitExcluded.has(p.id), low: typeof p.sim === 'number' && p.sim < splitWarnSim }">
            <input type="checkbox" :checked="!splitExcluded.has(p.id)" @change="togglePiece(p.id)" />
            <button class="icon-btn split-play" @click="playPiece(p)">
              <AppIcon :name="splitPlaying === p.id ? 'stop' : 'play'" :size="12" />
            </button>
            <span class="split-dur num">{{ p.dur.toFixed(1) }}s</span>
            <span class="split-src num">原段{{ p.src_seg }}</span>
            <span v-if="typeof p.sim === 'number'" class="segsim num"
                  :class="{ bad: p.sim < splitWarnSim }"
                  :title="'与整体质心相似度'">{{ p.sim.toFixed(2) }}</span>
          </div>
        </div>
      </template>
      <template #footer>
        <span class="split-kept num">保留 {{ splitKeptSec.toFixed(1) }}s</span>
        <button class="btn btn-primary" :disabled="splitLoading || rebuilding || !splitPieces.length" @click="doRebuild">
          {{ rebuilding ? '合成中…' : '合成回库' }}
        </button>
      </template>
    </UiModal>
  </div>
</template>

<style scoped>
.speaker-page { display: flex; flex-direction: column; gap: 14px; }
.page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-title { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
.page-sub { font-size: 13px; color: var(--text-3); margin: 0; }

/* 提示条 */
.msg-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px;
  font-size: 13px;
  border-radius: var(--radius-ctl);
}
.msg-bar.error { color: var(--rec); background: var(--rec-soft); border: 1px solid #f3c8c9; }
.msg-bar.ok { color: var(--green); background: var(--green-soft); border: 1px solid #cdeee0; }
.msg-text { flex: 1; }
.msg-x { width: 22px; height: 22px; border: none; background: transparent; color: inherit; }

/* 录入检测 */
.block { padding: 16px 20px; }
.block-title { font-size: 14px; font-weight: 600; }
.block-sub { font-size: 12.5px; color: var(--text-3); margin: 4px 0 14px; }
.id-actions { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }

.meter-wrap { flex: 1; min-width: 200px; }
.meter { height: 8px; background: #eef0f3; border-radius: 4px; overflow: hidden; }
.meter-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--green), var(--amber));
  transition: width 0.1s linear;
}
.meter-warn { display: block; margin-top: 5px; font-size: 12px; color: var(--rec); }

.report { margin-top: 14px; font-size: 13px; }
.rep-ok { color: var(--green); font-weight: 600; }
.rep-warn { color: var(--amber); font-weight: 600; }
.rep-fail { color: var(--rec); font-weight: 600; }
.rep-title { color: var(--text-3); margin-bottom: 4px; }
.rep-row { color: var(--text-2); line-height: 1.8; }
.ranked { margin-top: 10px; max-width: 420px; }
.rank-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.rank-name { width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-bar { flex: 1; height: 6px; background: #eef0f3; border-radius: 3px; overflow: hidden; }
.rank-fill { height: 100%; background: var(--accent); border-radius: 3px; }
.rank-sim { width: 48px; text-align: right; color: var(--text-2); font-variant-numeric: tabular-nums; }

/* 声纹卡片网格 */
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
}
@media (max-width: 1100px) { .grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }

.spk-card { padding: 16px 18px; }
.spk-head { display: flex; align-items: center; gap: 12px; }
.spk-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 600;
  flex-shrink: 0;
}
.spk-name { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.q-badge { font-size: 11px; padding: 1px 7px; border-radius: 6px; font-weight: 600; }
.q-good { background: #e6f6ef; color: #0f9d6c; }
.q-warn { background: #fdf3e3; color: #d97706; }
.q-bad { background: #fdecec; color: #e5484d; }
.q-warn-text { color: #d97706; font-size: 12px; }
.seg-del { margin-left: auto; }
.spk-count { font-size: 12px; color: var(--text-3); margin-top: 2px; }
.spk-actions { display: flex; gap: 8px; margin-top: 12px; }
.seg-list { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.seg-row { display: flex; align-items: center; gap: 10px; margin: 6px 0; }
.seg-name { width: 44px; font-size: 13px; }
.seg-dur { flex: 1; color: var(--text-3); font-size: 12px; font-variant-numeric: tabular-nums; }
.segsim { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 5px; background: #e6f6ef; color: #0f9d6c; }
.segsim.bad { background: #fdecec; color: #e5484d; }
.seg-row.segsimlow .seg-name { color: var(--rec); }
.split-tip { font-size: 12.5px; color: var(--text-3); line-height: 1.6; margin-bottom: 10px; }
.split-list { max-height: 46vh; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.split-row { display: flex; align-items: center; gap: 10px; padding: 5px 8px; border-radius: 7px; font-size: 13px; }
.split-row:hover { background: #f7f8fa; }
.split-row.off { opacity: 0.45; }
.split-row.off .split-dur { text-decoration: line-through; }
.split-play { width: 26px; height: 26px; border-radius: 50%; color: var(--accent); flex-shrink: 0; }
.split-dur { width: 48px; color: var(--text-2); }
.split-src { font-size: 11px; color: var(--text-3); flex: 1; }
.split-kept { margin-right: auto; font-size: 12px; color: var(--text-3); align-self: center; }
.seg-tip { font-size: 12px; color: var(--text-3); margin-top: 6px; line-height: 1.5; }

/* 注册弹窗 */
.append-tip { font-size: 12.5px; color: var(--accent); margin-top: 8px; line-height: 1.6; }
.enroll-tip { font-size: 12.5px; color: var(--text-3); line-height: 1.7; margin: 12px 0 0; }
.enroll-actions { margin-top: 16px; }
.enroll-btn { width: 100%; }
.rec-dot-inline { width: 8px; height: 8px; border-radius: 50%; background: #fff; }
.btn-row { display: flex; gap: 10px; justify-content: flex-end; margin-top: 12px; }
.confirm-text { font-size: 14px; margin: 4px 0; }
</style>
