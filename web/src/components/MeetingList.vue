<script setup lang="ts">
import { onMounted, ref } from 'vue'

interface MeetingRow {
  id: string
  title: string
  status: string
  started_at: string | null
  ended_at: string | null
  attributes: { key: string; value: string }[]
}

const emit = defineEmits<{ open: [id: string] }>()

const meetings = ref<MeetingRow[]>([])
const loading = ref(true)

function fmt(row: MeetingRow): string {
  if (!row.started_at) return '-'
  return new Date(row.started_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function dur(row: MeetingRow): string {
  if (row.started_at && row.ended_at) {
    return `${Math.round((Date.parse(row.ended_at) - Date.parse(row.started_at)) / 60000)} 分钟`
  }
  return '进行中'
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
  <div v-loading="loading">
    <van-cell-group inset title="历史会议（点击查看详情）">
      <van-empty v-if="!meetings.length && !loading" description="暂无会议" />
      <van-cell
        v-for="m in meetings"
        :key="m.id"
        :title="m.title || '未命名会议'"
        is-link
        @click="emit('open', m.id)"
      >
        <template #label>
          {{ fmt(m) }} · {{ dur(m) }}
        </template>
        <template #value>
          <van-tag :type="m.status === 'live' ? 'danger' : 'default'">
            {{ m.status === 'live' ? '进行中' : '已结束' }}
          </van-tag>
        </template>
      </van-cell>
    </van-cell-group>
  </div>
</template>
