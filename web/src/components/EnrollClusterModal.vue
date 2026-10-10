<script setup lang="ts">
// 会末陌生人 → 声纹注册面板（2026-10-01 重设计）
// 每个"说话人N"是独立注册单元：各自试听勾选剔除错分段、各自填名字、各自注册；
// 底部「全部注册」把已填名字的簇一次性批量提交。多个簇填同一个名字 = 合并
// 为同一人（GPU 端同名累积注册段）。
import { computed, ref, watch } from 'vue'

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
  label: string
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
const submitting = ref<string | null>(null)     // 正在注册的簇 label；'__batch__'=批量中
const playingSeq = ref<number | null>(null)
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
    showToast('加载未识别说话人失败', 'error')
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
    playingSeq.value = null
    load()
  } else {
    stopAudio()
  }
})

function toggleSeg(seq: number) {
  excluded.value.has(seq) ? excluded.value.delete(seq) : excluded.value.add(seq)
  excluded.value = new Set(excluded.value)
}

function keptMsOf(c: Cluster): number {
  return c.segments
    .filter((s) => !excluded.value.has(s.seq))
    .reduce((sum, s) => sum + Math.max(0, s.end_ms - s.start_ms), 0)
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
  try {
    const r = await fetch(`/api/meetings/${props.meetingId}/enroll_cluster`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        labels: [c.label],
        exclude_seqs: [...excluded.value],
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
</script>

<template>
  <UiModal :show="show" title="注册未识别说话人" :width="640" @close="emit('close')">
    <div v-if="loading" class="ec-loading">加载中…</div>
    <div v-else-if="!clusters.length" class="ec-empty">本场会议没有未识别的说话人</div>
    <template v-else>
      <div class="ec-tip">
        已按"与本人相似度"自动勾选约 {{ autoPickSec }} 秒最像的段（红色 = 疑似混入他人，默认不选）；
        有疑虑再逐段试听挑拣。每个说话人单独填名注册，同一人被拆成多个"说话人"时填<b>同一个名字</b>即合并。
      </div>
      <div
        v-for="c in clusters"
        :key="c.label"
        class="ec-cluster"
        :class="{ done: !!c.registered }"
      >
        <div class="ec-chead">
          <span class="ec-clabel">{{ c.label }}</span>
          <span class="ec-cmeta num">{{ c.seg_count }} 段 · 素材 {{ fmtDur(keptMsOf(c) / 1000) }}</span>
          <template v-if="c.registered">
            <span class="chip chip-green ec-done-chip">
              <AppIcon name="check" :size="11" /> 已注册为 {{ c.registered }}
            </span>
          </template>
          <template v-else>
            <input
              v-model="names[c.label]"
              class="ec-name"
              placeholder="说话人姓名"
              maxlength="32"
              @click.stop
            />
            <button
              class="btn btn-sm btn-primary"
              :disabled="!!submitting || !(names[c.label] || '').trim() || keptMsOf(c) < 3000"
              @click="submitOne(c)"
            >
              {{ submitting === c.label ? '注册中…' : '注册' }}
            </button>
          </template>
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
              v-if="typeof s.cluster_sim === 'number'"
              class="ec-sim num"
              :class="{ bad: s.cluster_sim < warnSim }"
              :title="s.cluster_sim < warnSim ? '与本人相似度低，疑似混入他人声音' : '与本人相似度'"
            >{{ s.cluster_sim.toFixed(2) }}</span>
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
.ec-cluster.done { border-color: #bfe8d2; background: #f6fcf9; }
.ec-chead {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px; background: #f7f8fa;
}
.ec-cluster.done .ec-chead { background: transparent; }
.ec-clabel { font-size: 13.5px; font-weight: 600; flex-shrink: 0; }
.ec-cmeta { font-size: 12px; color: var(--text-3); margin-right: auto; }
.ec-name {
  border: 1px solid var(--border); border-radius: var(--radius-ctl);
  padding: 6px 10px; font-size: 13px; width: 120px; outline: none;
}
.ec-name:focus { border-color: var(--accent); }
.ec-done-chip { display: inline-flex; align-items: center; gap: 4px; }
.ec-segs { padding: 4px 0; max-height: 220px; overflow-y: auto; }
.ec-seg {
  display: flex; align-items: center; gap: 8px;
  padding: 5px 14px; font-size: 13px;
}
.ec-seg.excluded .ec-text, .ec-seg.excluded .ec-time { opacity: 0.4; text-decoration: line-through; }
.ec-play { width: 26px; height: 26px; border-radius: 50%; color: var(--accent); flex-shrink: 0; }
.ec-time { font-size: 11.5px; color: var(--text-3); flex-shrink: 0; }
.ec-text { color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ec-sim { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 5px; background: #e6f6ef; color: #0f9d6c; flex-shrink: 0; }
.ec-sim.bad { background: #fdecec; color: #e5484d; }
.ec-seg.lowsim .ec-text { color: var(--rec); }
.ec-keft { margin-right: auto; font-size: 12px; color: var(--text-3); align-self: center; }
</style>
