<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AppIcon from '../ui/AppIcon.vue'

interface MeetingRow {
  id: string
  title: string
  status: string
  started_at: string | null
  ended_at: string | null
  attributes: { key: string; value: string }[]
  tags: string[]
  reannotated: boolean
  final_status: string
  utterance_count: number
  summary_count: number
}

const emit = defineEmits<{ open: [id: string] }>()

const meetings = ref<MeetingRow[]>([])
const loading = ref(true)

const STATUS_TEXT: Record<string, string> = { live: '进行中', ended: '已结束', interrupted: '已中断' }

function statusText(s: string): string {
  return STATUS_TEXT[s] || s
}

function fmt(row: MeetingRow): string {
  if (!row.started_at) return '-'
  return new Date(row.started_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function dur(row: MeetingRow): string {
  if (row.started_at && row.ended_at) {
    return `${Math.round((Date.parse(row.ended_at) - Date.parse(row.started_at)) / 60000)} 分钟`
  }
  return row.status === 'live' ? '进行中' : '—'
}

/** 产物摘要：0 句 0 段 = 失败/空会议，一眼可辨 */
function productSummary(row: MeetingRow): string {
  return `${row.utterance_count ?? 0} 句转写 · ${row.summary_count ?? 0} 段纪要`
}

onMounted(async () => {
  try {
    const resp = await fetch('/api/meetings')
    meetings.value = (await resp.json()) as MeetingRow[]
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="list-page">
    <div class="page-head">
      <h2 class="page-title">历史会议</h2>
      <p class="page-sub">点击卡片查看纪要、转写与回放；重放测试入口在详情页</p>
    </div>

    <!-- 骨架屏 -->
    <div v-if="loading" class="grid">
      <div v-for="i in 4" :key="i" class="card m-card">
        <div class="skeleton" style="width: 55%; height: 16px" />
        <div class="skeleton" style="width: 80%; height: 12px; margin-top: 12px" />
        <div class="skeleton" style="width: 40%; height: 12px; margin-top: 8px" />
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!meetings.length" class="card empty">
      <div class="empty-icon"><AppIcon name="history" :size="24" /></div>
      暂无会议记录，去「开会」页开始第一场记录
    </div>

    <!-- 卡片网格 -->
    <TransitionGroup v-else name="list" tag="div" class="grid">
      <div
        v-for="m in meetings"
        :key="m.id"
        class="card card-hover m-card"
        @click="emit('open', m.id)"
      >
        <div class="m-head">
          <span class="m-title">{{ m.title || '未命名会议' }}</span>
          <span class="m-status" :class="m.status">
            <span v-if="m.status === 'live'" class="pulse-dot" />
            {{ statusText(m.status) }}
          </span>
        </div>
        <div class="m-meta num">{{ fmt(m) }} · {{ dur(m) }} · {{ productSummary(m) }}</div>
        <div v-if="m.tags?.length" class="m-tags">
          <span v-for="t in m.tags" :key="t" class="chip">{{ t }}</span>
        </div>
        <div class="m-foot">
          <span v-if="m.final_status === 'done'" class="chip chip-green">
            <AppIcon name="check" :size="11" /> 终版已出
          </span>
          <span v-else-if="m.final_status === 'failed'" class="chip chip-red">
            <AppIcon name="warning" :size="11" /> 会末版失败
          </span>
          <span v-else-if="m.final_status === 'running'" class="chip chip-amber">
            会末版生成中…
          </span>
          <button class="btn btn-sm btn-ghost m-open" @click.stop="emit('open', m.id)">
            查看详情
          </button>
        </div>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.page-head { margin-bottom: 20px; }
.page-title { font-size: 20px; font-weight: 600; margin: 0 0 4px; }
.page-sub { font-size: 13px; color: var(--text-3); margin: 0; }

.grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}
@media (min-width: 1500px) { .grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }

.m-card { padding: 16px 18px; cursor: pointer; display: flex; flex-direction: column; gap: 8px; }
.m-head { display: flex; align-items: flex-start; gap: 10px; }
.m-title {
  flex: 1;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.m-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  flex-shrink: 0;
  color: var(--text-3);
}
.m-status.live { color: var(--green); font-weight: 500; }
.m-status.interrupted { color: var(--amber); font-weight: 500; }
.m-meta { font-size: 12.5px; color: var(--text-3); font-variant-numeric: tabular-nums; }
.m-tags { display: flex; flex-wrap: wrap; gap: 5px; }
.m-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 4px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  min-height: 30px;
}
.m-open { color: var(--text-3); }
.m-card:hover .m-open { color: var(--accent); }
</style>
