<script setup lang="ts">
import { onMounted, ref } from 'vue'

interface Attr { key: string; value: string }
interface Utterance { seq: number; speaker: string; text: string }
interface SummaryRow {
  seq: number
  title: string
  content: string
  action_items: { who: string; what: string; due?: string; status?: string }[]
  attributes: Attr[]
}

const props = defineProps<{ meetingId: string }>()
const emit = defineEmits<{ back: [] }>()

const title = ref('')
const attrs = ref<Attr[]>([])
const startedAt = ref('')
const utterances = ref<Utterance[]>([])
const summaries = ref<SummaryRow[]>([])
const loading = ref(true)

const speakerColors = ['#e6646c', '#3aa675', '#3b7fe0', '#a06ee1', '#d98e32', '#2ba0b8', '#c2568c', '#6a8f3c']
const speakerColorMap = new Map<string, string>()
function colorOf(speaker: string): string {
  if (!speakerColorMap.has(speaker)) {
    speakerColorMap.set(speaker, speakerColors[speakerColorMap.size % speakerColors.length])
  }
  return speakerColorMap.get(speaker)!
}

async function load() {
  loading.value = true
  try {
    const [detail, sumResp] = await Promise.all([
      fetch(`/api/meetings/${props.meetingId}`).then((r) => r.json()),
      fetch(`/api/meetings/${props.meetingId}/summaries`).then((r) => r.json()),
    ])
    title.value = detail.title
    attrs.value = detail.attributes || []
    startedAt.value = detail.started_at || ''
    utterances.value = detail.utterances || []
    summaries.value = (sumResp as SummaryRow[]) || []
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function downloadMarkdown() {
  const resp = await fetch(`/api/meetings/${props.meetingId}/report`)
  const data = await resp.json()
  const blob = new Blob([data.markdown || ''], { type: 'text/markdown' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${title.value || props.meetingId}.md`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div style="display: flex; align-items: center; gap: 12px">
        <el-button size="small" @click="emit('back')">← 返回列表</el-button>
        <span style="flex: 1; font-weight: 600">{{ title || '未命名会议' }}</span>
        <el-button size="small" type="primary" :loading="loading" @click="downloadMarkdown">
          下载 Markdown
        </el-button>
      </div>
    </template>

    <div v-loading="loading" style="max-height: calc(100vh - 180px); overflow-y: auto; padding-right: 8px">
      <!-- 会议信息：固定字段 + AI 提取的动态属性 -->
      <el-descriptions :column="2" border size="small" style="margin-bottom: 16px">
        <el-descriptions-item label="开始时间">{{ startedAt ? new Date(startedAt).toLocaleString('zh-CN') : '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag size="small" :type="summaries.some((s) => s.seq === 0) ? 'primary' : 'info'">已结束</el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          v-for="a in attrs"
          :key="a.key"
          :label="a.key"
          :span="2"
        >
          {{ a.value }}
        </el-descriptions-item>
      </el-descriptions>

      <h4 style="margin: 16px 0 8px">逐句转写</h4>
      <div v-for="u in utterances" :key="u.seq" style="margin-bottom: 8px">
        <el-tag
          size="small"
          effect="plain"
          :style="{ borderColor: colorOf(u.speaker), color: colorOf(u.speaker) }"
        >
          {{ u.speaker || '发言人' }}
        </el-tag>
        <span style="margin-left: 8px">{{ u.text }}</span>
      </div>
      <el-empty v-if="!utterances.length" description="本场会议没有有效转写" />

      <h4 style="margin: 24px 0 8px">总结</h4>
      <el-timeline v-if="summaries.length">
        <el-timeline-item
          v-for="s in summaries"
          :key="s.seq"
          :timestamp="`第 ${s.seq} 段`"
        >
          <h5 style="margin: 0 0 6px">{{ s.title || `段落 ${s.seq}` }}</h5>
          <div v-html="s.content"></div>
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
          <div v-if="s.action_items.length" style="margin-top: 6px">
            <el-tag
              v-for="(a, i) in s.action_items"
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
      <el-empty v-else description="暂无总结" />
    </div>
  </el-card>
</template>
