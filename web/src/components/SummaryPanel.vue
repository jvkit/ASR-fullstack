<script setup lang="ts">
import { showToast } from 'vant'

interface SummaryItem {
  seq: number
  title: string
  points: string[]
  // 2026-09-28 起波次 LLM 输出结构化待办 {task,assignee,due,quote}；旧数据是 {who,what,due,status}
  actionItems: { who?: string; what?: string; task?: string; assignee?: string; due?: string; status?: string; quote?: string }[]
  attributes: { key: string; value: string }[]
  final: boolean
}

function actionText(a: SummaryItem['actionItems'][number]): string {
  const who = a.who || a.assignee || ''
  const what = a.what || a.task || ''
  const due = a.due ? `（${a.due}）` : ''
  return `${who ? `${who}：` : ''}${what}${due}`
}

defineProps<{ summaries: SummaryItem[]; meetingId: string }>()

async function copyReport(meetingId: string) {
  const resp = await fetch(`/api/meetings/${meetingId}/report`)
  const data = await resp.json()
  await navigator.clipboard.writeText(data.markdown || '')
  showToast('已复制到剪贴板')
}
</script>

<template>
  <van-cell-group inset class="panel">
    <template #title>
      <span>滚动纪要</span>
      <van-button
        v-if="summaries.length && meetingId"
        size="small"
        plain
        type="primary"
        class="copy-btn"
        @click="copyReport(meetingId)"
      >
        复制完整纪要
      </van-button>
    </template>

    <div class="list">
      <div v-for="s in summaries" :key="s.seq" class="item">
        <div class="item-head">
          <van-tag :type="s.final ? 'primary' : 'success'">{{ s.final ? '最终总结' : `第 ${s.seq} 段` }}</van-tag>
          <span class="item-title">{{ s.title || `段落 ${s.seq}` }}</span>
        </div>
        <ul class="points">
          <li v-for="(p, i) in s.points" :key="i">{{ p }}</li>
        </ul>
        <div v-if="s.attributes.length" class="tags">
          <van-tag v-for="a in s.attributes" :key="a.key" plain type="primary" class="tag">
            {{ a.key }}：{{ a.value }}
          </van-tag>
        </div>
        <div v-if="s.actionItems.length" class="tags">
          <van-tag v-for="(a, i) in s.actionItems" :key="i" plain type="warning" class="tag">
            {{ actionText(a) }}
          </van-tag>
        </div>
      </div>
      <van-empty v-if="!summaries.length" description="每轮讨论结束后自动生成总结" />
    </div>
  </van-cell-group>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; }
.copy-btn { float: right; }
.list { height: 40vh; overflow-y: auto; padding: 8px 16px; }
.item { margin-bottom: 16px; }
.item-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.item-title { font-weight: 600; font-size: 14px; }
.points { margin: 0; padding-left: 18px; }
.tags { margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px; }
.tag { margin: 0; }
@media (min-width: 768px) { .list { height: 60vh; } }
</style>
