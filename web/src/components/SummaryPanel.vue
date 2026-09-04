<script setup lang="ts">
interface SummaryItem {
  seq: number
  title: string
  points: string[]
  actionItems: { who: string; what: string; due?: string; status?: string }[]
  attributes: { key: string; value: string }[]
  final: boolean
}

defineProps<{ summaries: SummaryItem[]; meetingId: string }>()

async function copyReport(meetingId: string) {
  const resp = await fetch(`/api/meetings/${meetingId}/report`)
  const data = await resp.json()
  await navigator.clipboard.writeText(data.markdown || '')
}
</script>

<template>
  <el-card shadow="never" style="height: 70vh; display: flex; flex-direction: column">
    <template #header>
      <div style="display: flex; align-items: center">
        <span style="flex: 1">滚动纪要</span>
        <el-button
          v-if="summaries.length && meetingId"
          size="small"
          @click="copyReport(meetingId)"
        >
          复制完整纪要
        </el-button>
      </div>
    </template>

    <div style="flex: 1; overflow-y: auto">
      <el-timeline>
        <el-timeline-item
          v-for="s in summaries"
          :key="s.seq"
          :timestamp="s.final ? '最终总结' : `第 ${s.seq} 段`"
          :type="s.final ? 'primary' : 'success'"
        >
          <h4 style="margin: 0 0 6px">{{ s.title || `段落 ${s.seq}` }}</h4>
          <ul style="margin: 0; padding-left: 18px">
            <li v-for="(p, i) in s.points" :key="i">{{ p }}</li>
          </ul>
          <div v-if="s.attributes.length" style="margin-top: 6px">
            <el-tag
              v-for="a in s.attributes"
              :key="a.key"
              size="small"
              effect="plain"
              style="margin-right: 6px"
            >
              {{ a.key }}：{{ a.value }}
            </el-tag>
          </div>
          <div v-if="s.actionItems.length" style="margin-top: 6px">
            <el-tag
              v-for="(a, i) in s.actionItems"
              :key="i"
              size="small"
              type="warning"
              effect="plain"
              style="margin: 2px 4px 2px 0"
            >
              {{ a.who }}：{{ a.what }}{{ a.due ? `（${a.due}）` : '' }}
            </el-tag>
          </div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-if="!summaries.length" description="每轮讨论结束后自动生成总结" />
    </div>
  </el-card>
</template>
