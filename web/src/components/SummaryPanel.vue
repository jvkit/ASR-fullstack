<script setup lang="ts">
// 会议纪要（右栏）：时间线布局，波次卡片与左侧转写分隔条同源、双向联动
import { nextTick, ref, watch } from 'vue'

import AppIcon from '../ui/AppIcon.vue'
import { fmtMs as fmtMsShared } from '../ui/format'
import { showToast } from '../ui/toast'

interface SummaryItem {
  seq: number
  title: string
  points: string[]
  // 2026-09-28 起波次 LLM 输出结构化待办 {task,assignee,due,quote}；旧数据是 {who,what,due,status}
  actionItems: { who?: string; what?: string; task?: string; assignee?: string; due?: string; status?: string; quote?: string }[]
  attributes: { key: string; value: string }[]
  final: boolean
  startMs?: number
  endMs?: number
}

const props = defineProps<{
  summaries: SummaryItem[]
  meetingId: string
  finalProgress: { stage: string; pct: number } | null
  finalProgressText: string
  activeSeq: number    // 左栏点分隔条 → 定位到该波卡片
  activeNonce: number  // 同 seq 重复点击也能触发（配合 activeSeq 使用）
}>()

const emit = defineEmits<{ locate: [w: { seq: number; startMs?: number; endMs?: number }] }>()

function actionText(a: SummaryItem['actionItems'][number]): string {
  const who = a.who || a.assignee || ''
  const what = a.what || a.task || ''
  const due = a.due ? `（${a.due}）` : ''
  return `${who ? `${who}：` : ''}${what}${due}`
}

function fmtMs(ms?: number): string {
  return fmtMsShared(ms)
}

// 波次卡（seq≥1）按时间序；seq=0 的实时终版单独置顶
function waves(): SummaryItem[] {
  return props.summaries
    .filter((s) => s.seq >= 1)
    .sort((a, b) => (a.startMs ?? 0) - (b.startMs ?? 0))
}
function finalItem(): SummaryItem | undefined {
  return props.summaries.find((s) => s.seq === 0)
}

async function copyReport(meetingId: string) {
  const resp = await fetch(`/api/meetings/${meetingId}/report`)
  const data = await resp.json()
  await navigator.clipboard.writeText(data.markdown || '')
  showToast('已复制到剪贴板', 'success')
}

// 左栏分隔条点击 → 滚动到对应波卡并短暂高亮
const flashSeq = ref(0)
const listEl = ref<HTMLElement | null>(null)
watch(
  () => props.activeNonce,
  async () => {
    if (!props.activeSeq) return
    await nextTick()
    flashSeq.value = props.activeSeq
    listEl.value
      ?.querySelector(`[data-wave="${props.activeSeq}"]`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => (flashSeq.value = 0), 2000)
  },
)
</script>

<template>
  <div class="card panel">
    <div class="panel-head">
      <span class="panel-title">会议纪要</span>
      <button
        v-if="summaries.length && meetingId"
        class="btn btn-ghost btn-sm copy-btn"
        @click="copyReport(meetingId)"
      >
        <AppIcon name="copy" :size="13" /> 复制完整纪要
      </button>
    </div>

    <div ref="listEl" class="list">
      <!-- 会末版进度（final/both 模式结束后重跑中） -->
      <div v-if="finalProgress" class="progress-card">
        <div class="progress-text">
          <span>会末版生成中：{{ finalProgressText }}</span>
          <span class="num">{{ finalProgress.pct }}%</span>
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: finalProgress.pct + '%' }" />
        </div>
      </div>

      <!-- 实时终版（seq=0）置顶 -->
      <div v-if="finalItem()" class="final-card" data-wave="0">
        <div class="final-badge"><AppIcon name="doc" :size="13" /> 实时版终版</div>
        <div class="wave-title">{{ finalItem()!.title || '最终总结' }}</div>
        <ul class="points">
          <li v-for="(p, i) in finalItem()!.points" :key="i">{{ p }}</li>
        </ul>
      </div>

      <!-- 波次时间线 -->
      <div class="timeline">
        <div
          v-for="s in waves()"
          :key="s.seq"
          class="wave-card"
          :class="{ flash: flashSeq === s.seq }"
          :data-wave="s.seq"
          @click="emit('locate', { seq: s.seq, startMs: s.startMs, endMs: s.endMs })"
        >
          <span class="node" />
          <div class="wave-head">
            <span class="wave-seq">第 {{ s.seq }} 波</span>
            <span v-if="s.startMs != null" class="wave-time num">{{ fmtMs(s.startMs) }}–{{ fmtMs(s.endMs) }}</span>
            <AppIcon name="locate" :size="13" class="wave-locate" />
          </div>
          <div class="wave-title">{{ s.title || `段落 ${s.seq}` }}</div>
          <ul class="points">
            <li v-for="(p, i) in s.points" :key="i">{{ p }}</li>
          </ul>
          <div v-if="s.actionItems.length" class="chips">
            <span v-for="(a, i) in s.actionItems" :key="i" class="chip chip-amber">
              <AppIcon name="todo" :size="11" /> {{ actionText(a) }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="!summaries.length && !finalProgress" class="empty">
        <div class="empty-icon"><AppIcon name="doc" :size="24" /></div>
        每波讨论结束后自动生成段纪要
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; overflow: hidden; flex: 1; }
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.panel-title { font-size: 14px; font-weight: 600; }
.copy-btn { color: var(--text-3); }

.list {
  height: calc(100vh - 320px);
  min-height: 320px;
  overflow-y: auto;
  padding: 14px 14px 16px;
}

/* 会末版进度卡 */
.progress-card {
  padding: 12px 14px;
  margin-bottom: 12px;
  background: var(--accent-soft);
  border: 1px solid #d8e1ff;
  border-radius: var(--radius-ctl);
}
.progress-text {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--accent);
  margin-bottom: 8px;
}
.progress-track { height: 5px; border-radius: 3px; background: #dbe3ff; overflow: hidden; }
.progress-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 0.3s ease-out;
}

/* 实时终版卡 */
.final-card {
  padding: 12px 14px;
  margin-bottom: 12px;
  border: 1px solid #d8e1ff;
  background: linear-gradient(180deg, #f7f9ff, #fff);
  border-radius: var(--radius-ctl);
}
.final-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 6px;
}

/* 时间线 */
.timeline { position: relative; padding-left: 18px; }
.timeline::before {
  content: '';
  position: absolute;
  left: 4px;
  top: 8px;
  bottom: 8px;
  width: 1px;
  background: var(--border-strong);
}
.wave-card {
  position: relative;
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-ctl);
  cursor: pointer;
  transition: border-color 0.15s ease-out, box-shadow 0.15s ease-out, transform 0.15s ease-out;
}
.wave-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-float);
  transform: translateY(-1px);
}
.wave-card.flash { animation: flash-bg 2s ease-out forwards; border-color: var(--accent); }
.node {
  position: absolute;
  left: -18px;
  top: 16px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid var(--accent);
}
.wave-head { display: flex; align-items: center; gap: 8px; }
.wave-seq { font-size: 12px; font-weight: 600; color: var(--accent); }
.wave-time { font-size: 11.5px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.wave-locate { margin-left: auto; color: var(--text-3); opacity: 0; transition: opacity 0.15s ease-out; }
.wave-card:hover .wave-locate { opacity: 1; }
.wave-title { font-size: 13.5px; font-weight: 600; margin: 4px 0; }
.points { margin: 0; padding-left: 16px; font-size: 13px; color: var(--text-2); line-height: 1.7; }
.chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 5px; }
</style>
