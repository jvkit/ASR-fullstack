<script setup lang="ts">
// 实时转写：按纪要波次分组（波次分隔条与右侧纪要卡同源），支持联动高亮与自动滚底
import { computed, nextTick, ref, watch } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import { fmtMs as fmtMsShared } from '../ui/format'

interface Utterance {
  seq: number
  speaker: string
  text: string
  start_ms: number
  end_ms: number
  draft: boolean
}
interface WaveInfo {
  seq: number
  title: string
  startMs: number
  endMs: number
}

const props = defineProps<{
  utterances: Utterance[]
  waves: WaveInfo[]           // 已出纪要的波次（seq≥1，带覆盖时间范围）
  speakers: string[]
  colorOf: (speaker: string) => string
  highlight: { startMs: number; endMs: number; nonce: number } | null // 右栏点波次 → 高亮范围
}>()

const emit = defineEmits<{ waveClick: [seq: number] }>()

// ---------- 转写流 + 波次分隔条按时间归并 ----------
type Block =
  | { kind: 'wave'; key: string; wave: WaveInfo }
  | { kind: 'utt'; key: string; u: Utterance }
  | { kind: 'pending'; key: string }

const blocks = computed<Block[]>(() => {
  const waves = [...props.waves].sort((a, b) => a.startMs - b.startMs)
  const out: Block[] = []
  let wi = 0
  let pendingInserted = false
  const lastWaveEnd = waves.length ? Math.max(...waves.map((w) => w.endMs)) : -1

  for (const u of props.utterances) {
    // 到达某波覆盖起点 → 先插入该波的分隔条
    while (wi < waves.length && waves[wi].startMs <= u.start_ms) {
      out.push({ kind: 'wave', key: `wave-${waves[wi].seq}`, wave: waves[wi] })
      wi++
    }
    // 越过所有波覆盖范围的尾巴 → 归入「进行中的波」
    if (!pendingInserted && u.start_ms > lastWaveEnd) {
      out.push({ kind: 'pending', key: 'pending' })
      pendingInserted = true
    }
    out.push({ kind: 'utt', key: `u-${u.seq}`, u })
  }
  while (wi < waves.length) {
    out.push({ kind: 'wave', key: `wave-${waves[wi].seq}`, wave: waves[wi] })
    wi++
  }
  return out
})

function fmtMs(ms: number): string {
  return fmtMsShared(ms)
}

// 名字首字符做头像（「未识别」取「未」）
function initial(name: string): string {
  return (name || '？').trim().charAt(0)
}
// 12% 透明度底色
function softBg(color: string): string {
  return color + '1f'
}

// ---------- 联动高亮：右栏点波次 → 滚动到范围 + 浅 accent 底色 2s 淡出 ----------
const hlRange = ref<{ startMs: number; endMs: number } | null>(null)
const hlFading = ref(false)
let hlTimer: ReturnType<typeof setTimeout> | null = null
const listEl = ref<HTMLElement | null>(null)

watch(
  () => props.highlight?.nonce,
  async () => {
    const h = props.highlight
    if (!h) return
    hlRange.value = { startMs: h.startMs, endMs: h.endMs }
    hlFading.value = false
    if (hlTimer) clearTimeout(hlTimer)
    hlTimer = setTimeout(() => {
      hlFading.value = true // 先常驻 1.2s，再触发 2s 淡出
      setTimeout(() => (hlRange.value = null), 2100)
    }, 1200)
    await nextTick()
    const first = props.utterances.find(
      (u) => u.end_ms > h.startMs && u.start_ms < h.endMs,
    )
    if (first) {
      userPinned.value = false
      listEl.value
        ?.querySelector(`[data-seq="${first.seq}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  },
)

function inHighlight(u: Utterance): boolean {
  const h = hlRange.value
  return !!h && u.end_ms > h.startMs && u.start_ms < h.endMs
}

// ---------- 自动滚到底：用户上滚时暂停，出「回到底部」浮钮 ----------
const userPinned = ref(false)

function onScroll() {
  const el = listEl.value
  if (!el) return
  userPinned.value = el.scrollHeight - el.scrollTop - el.clientHeight > 100
}

async function scrollBottom() {
  await nextTick()
  const el = listEl.value
  if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
}

watch(
  () => props.utterances.length,
  async () => {
    if (!userPinned.value) await scrollBottom()
  },
)
</script>

<template>
  <div class="card panel">
    <div class="panel-head">
      <span class="panel-title">实时转写</span>
      <span class="panel-count num">{{ utterances.length }} 句</span>
      <div class="legend">
        <span v-for="spk in speakers" :key="spk" class="legend-item">
          <span class="legend-dot" :style="{ background: colorOf(spk) }" />{{ spk }}
        </span>
      </div>
    </div>

    <div ref="listEl" class="list" @scroll.passive="onScroll">
      <TransitionGroup name="list">
        <template v-for="b in blocks" :key="b.key">
          <!-- 波次分隔条：与右侧纪要卡同一数据源，点击反向定位 -->
          <button
            v-if="b.kind === 'wave'"
            type="button"
            class="wave-bar-row"
            :title="'定位到右侧纪要第 ' + b.wave.seq + ' 波'"
            @click="emit('waveClick', b.wave.seq)"
          >
            <span class="wave-line" />
            <span class="wave-label">
              第 {{ b.wave.seq }} 波 · <span class="num">{{ fmtMs(b.wave.startMs) }}–{{ fmtMs(b.wave.endMs) }}</span>
              <template v-if="b.wave.title"> ·「{{ b.wave.title }}」</template>
            </span>
            <span class="wave-line" />
          </button>

          <!-- 进行中的波（纪要还没覆盖到的尾巴） -->
          <div v-else-if="b.kind === 'pending'" class="wave-bar-row pending">
            <span class="wave-line" />
            <span class="wave-label breathing">进行中的波 · 纪要生成中…</span>
            <span class="wave-line" />
          </div>

          <!-- 句子 -->
          <div
            v-else
            class="utt"
            :class="{
              draft: b.u.draft,
              hl: inHighlight(b.u),
              'hl-fading': inHighlight(b.u) && hlFading,
            }"
            :data-seq="b.u.seq"
          >
            <span
              class="avatar"
              :style="{ background: softBg(colorOf(b.u.speaker)), color: colorOf(b.u.speaker) }"
            >{{ initial(b.u.speaker) }}</span>
            <div class="utt-main">
              <div class="utt-meta">
                <span class="utt-name" :style="{ color: colorOf(b.u.speaker) }">{{ b.u.speaker }}</span>
                <span class="utt-time num">{{ fmtMs(b.u.start_ms) }}</span>
                <span v-if="b.u.draft" class="utt-draft-tag breathing">转写中…</span>
              </div>
              <div class="utt-text">{{ b.u.text }}</div>
            </div>
          </div>
        </template>
      </TransitionGroup>

      <div v-if="!utterances.length" class="empty">
        <div class="empty-icon"><AppIcon name="mic" :size="24" /></div>
        开始说话后将按批次显示转写（约 30 秒一批）
      </div>
    </div>

    <button v-if="userPinned && utterances.length" class="to-bottom" @click="userPinned = false; scrollBottom()">
      <AppIcon name="chevronDown" :size="13" /> 回到底部
    </button>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; position: relative; overflow: hidden; }
.panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.panel-title { font-size: 14px; font-weight: 600; }
.panel-count { font-size: 12px; color: var(--text-3); }
.legend {
  margin-left: auto;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  overflow: hidden;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--text-2);
  white-space: nowrap;
}
.legend-dot { width: 7px; height: 7px; border-radius: 50%; }

.list { height: calc(100vh - 320px); min-height: 320px; overflow-y: auto; padding: 8px 0; }

/* 波次分隔条 */
.wave-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 16px;
  background: none;
  border: none;
  font-family: var(--font);
  cursor: pointer;
}
.wave-bar-row.pending { cursor: default; }
.wave-line { flex: 1; height: 1px; background: var(--border); }
.wave-label {
  font-size: 11.5px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 75%;
  transition: color 0.15s ease-out;
}
.wave-bar-row:not(.pending):hover .wave-label { color: var(--accent); }

/* 句子行 */
.utt {
  display: flex;
  gap: 10px;
  padding: 7px 16px;
  border-radius: 8px;
  margin: 0 8px;
  transition: background 0.15s ease-out;
}
.utt:hover { background: #f7f8fa; }
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
.utt-draft-tag { margin-left: auto; font-size: 11px; color: var(--text-3); }
.utt-text { font-size: 14px; color: var(--text-1); word-break: break-word; line-height: 1.65; }
.utt.draft .utt-text { opacity: 0.55; transition: opacity 0.3s ease-out; }

/* 联动高亮：底色常驻 2s 后淡出 */
.utt.hl { background: rgba(47, 92, 255, 0.1); }
.utt.hl.hl-fading { animation: flash-bg 2s ease-out forwards; }

/* 回到底部浮钮 */
.to-bottom {
  position: absolute;
  bottom: 16px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 14px;
  font-size: 12.5px;
  font-family: var(--font);
  color: var(--text-2);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 999px;
  box-shadow: var(--shadow-float);
  cursor: pointer;
}
.to-bottom:hover { color: var(--accent); border-color: var(--accent); }
</style>
