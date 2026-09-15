<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { showConfirmDialog, showToast } from 'vant'
import { MicRecorder } from '../ws/audio'

const speakers = ref<string[]>([])
const loading = ref(false)
const errorMsg = ref('')
const okMsg = ref('')
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

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
  } catch (e) {
    errorMsg.value = `加载失败：${e}`
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  // http 访问非 localhost 时 navigator.mediaDevices 为 undefined，麦克风必然失败，提前提示
  if (typeof navigator !== 'undefined' && navigator.mediaDevices === undefined) {
    insecure.value = true
  }
})

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
      showToast('注册未通过，请按提示重录')
      return
    }
    speakers.value = d.speakers || (await (await fetch('/api/speakers')).json()).speakers || []
    okMsg.value = `已注册「${name}」：有效语音 ${d.effective_sec}s / ${d.segments} 段`
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
    showToast('太短，请说 3~10 秒')
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

// ---------- 删除 ----------
async function remove(name: string) {
  try {
    await showConfirmDialog({ title: '删除声纹', message: `确定删除「${name}」？删除后立即不再被识别。` })
  } catch {
    return // 取消
  }
  loading.value = true
  try {
    const r = await fetch(`/api/speakers/${encodeURIComponent(name)}`, { method: 'DELETE' })
    const d = await r.json()
    if (!r.ok) {
      errorMsg.value = d.detail || '删除失败'
      return
    }
    speakers.value = d.speakers || []
    okMsg.value = `已删除「${name}」（移除 ${(d.removed || []).length} 个注册段）`
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="speaker-page">
    <van-notice-bar
      v-if="insecure"
      mode="closeable"
      color="#fff"
      background="#e64340"
      text="当前为 http 访问，浏览器禁止使用麦克风，请改用 https 地址打开"
    />
    <van-notice-bar v-if="errorMsg" mode="closeable" color="#fff" background="#e64340" :text="errorMsg" @close="errorMsg = ''" />
    <van-notice-bar v-if="okMsg" mode="closeable" color="#0a7a4b" background="#e8f7ef" :text="okMsg" @close="okMsg = ''" />

    <van-cell-group inset class="block">
      <template #title>录入检测（我是谁）</template>
      <van-field label="说明">
        <template #input>
          <span class="tip">随便说 3~10 秒，系统告诉你命中声纹库里的谁。注册完立刻测一次 = 最直接的注册质量检查</span>
        </template>
      </van-field>
      <div class="rec-actions">
        <van-button
          v-if="!idRecording"
          type="primary"
          round
          block
          :disabled="insecure"
          :loading="idBusy"
          @click="idStart"
        >
          ◉ 开始检测
        </van-button>
        <van-button v-else type="success" round block @click="idStop">
          ■ 结束并识别{{ idSec ? `（已录 ${idSec.toFixed(1)}s）` : '' }}
        </van-button>
        <div v-if="idRecording" class="meter-wrap">
          <div class="meter"><div class="meter-fill" :style="{ width: dbPct(idLevel) + '%' }" /></div>
          <span v-if="idSilent > 2" class="meter-warn">⚠ 没检测到声音，请靠近麦克风说话</span>
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
              <span class="rank-sim">{{ x.sim }}</span>
            </div>
          </div>
        </template>
      </div>
    </van-cell-group>

    <van-cell-group inset class="block">
      <template #title>注册新声纹</template>
      <van-field
        v-model="form.name"
        label="姓名"
        placeholder="如：张三"
        :disabled="recording"
      />
      <van-field label="说明">
        <template #input>
          <span class="tip">对着开会用的同一台设备，自然说 20~60 秒（可以停顿、想词）。系统自动挑出最清晰、最像你的 3~5 个片段；太短/太轻/疑似他人或环境声的片段会被丢弃。没录够可以同名再录，合格片段自动累积</span>
        </template>
      </van-field>
      <div class="rec-actions">
        <van-button
          v-if="!recording"
          type="danger"
          round
          block
          :disabled="insecure || !form.name.trim()"
          :loading="loading || uploading"
          @click="startRecord"
        >
          ● 开始录音
        </van-button>
        <template v-else>
          <div class="meter-wrap">
            <div class="meter"><div class="meter-fill" :style="{ width: dbPct(level) + '%' }" /></div>
            <span v-if="silentSec > 2" class="meter-warn">⚠ 没检测到声音，请靠近麦克风说话</span>
          </div>
          <div class="btn-row">
            <van-button round @click="cancelRecord">取消</van-button>
            <van-button type="success" round @click="stopAndEnroll">
              ■ 完成并注册{{ recorded ? `（已录 ${recorded.toFixed(1)}s）` : '' }}
            </van-button>
          </div>
        </template>
      </div>
      <div v-if="report && report.rejected && report.rejected.length" class="report">
        <div class="rep-title">丢弃 {{ report.rejected.length }} 段（录入清洗）</div>
        <div v-for="(r, i) in report.rejected" :key="i" class="rep-row">· {{ r.dur }}s：{{ r.reason }}</div>
      </div>
    </van-cell-group>

    <van-cell-group inset class="block" :title="`已注册（${speakers.length}）`">
      <van-empty v-if="!speakers.length" description="暂无注册声纹" />
      <van-cell v-for="s in speakers" :key="s" :title="s">
        <template #right-icon>
          <van-button size="small" type="danger" plain @click="remove(s)">删除</van-button>
        </template>
      </van-cell>
    </van-cell-group>
  </div>
</template>

<style scoped>
.speaker-page { padding-bottom: 24px; }
.block { margin-top: 12px; }
.rec-actions { padding: 8px 16px 16px; }
.tip { font-size: 13px; color: #969799; line-height: 1.6; }
.btn-row { display: flex; gap: 12px; justify-content: flex-end; margin-top: 10px; }
.meter-wrap { margin-top: 10px; }
.meter { height: 10px; background: #ebedf0; border-radius: 5px; overflow: hidden; }
.meter-fill { height: 100%; background: linear-gradient(90deg, #07c160, #f0a020); transition: width 0.1s; }
.meter-warn { display: block; margin-top: 6px; font-size: 12px; color: #ee0a24; }
.report { padding: 8px 16px 14px; font-size: 13px; }
.rep-ok { color: #0a7a4b; font-weight: 600; }
.rep-warn { color: #ed6a0c; font-weight: 600; }
.rep-fail { color: #ee0a24; font-weight: 600; }
.rep-title { color: #969799; margin-bottom: 4px; }
.rep-row { color: #646566; line-height: 1.8; }
.ranked { margin-top: 8px; }
.rank-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.rank-name { width: 90px; color: #323233; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-bar { flex: 1; height: 8px; background: #ebedf0; border-radius: 4px; overflow: hidden; }
.rank-fill { height: 100%; background: #1989fa; }
.rank-sim { width: 44px; text-align: right; color: #646566; font-variant-numeric: tabular-nums; }
</style>
