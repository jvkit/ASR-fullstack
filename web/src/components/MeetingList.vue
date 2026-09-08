<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { showToast } from 'vant'

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
const retesting = ref<string>('')  // 正在重测的会议 id

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

async function retest(m: MeetingRow) {
  if (retesting.value) return
  retesting.value = m.id
  showToast({ message: '已发起重测（说话人重新区分），约需半分钟', duration: 2500 })
  try {
    const resp = await fetch(`/api/meetings/${m.id}/reannotate?speaker_count=2`)
    if (!resp.ok) throw new Error(await resp.text())
    showToast({ message: '重测完成，点开会议查看结果', duration: 3000 })
  } catch (e) {
    showToast({ message: `重测失败：${e}`, type: 'fail', duration: 4000 })
  } finally {
    retesting.value = ''
  }
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
          <div class="cell-actions">
            <van-button
              size="small"
              plain
              type="primary"
              :loading="retesting === m.id"
              :disabled="!!retesting || m.status === 'live'"
              loading-text="重测中"
              @click.stop="retest(m)"
            >
              重新测试
            </van-button>
            <van-tag :type="m.status === 'live' ? 'danger' : 'default'">
              {{ m.status === 'live' ? '进行中' : '已结束' }}
            </van-tag>
          </div>
        </template>
      </van-cell>
    </van-cell-group>
  </div>
</template>

<style scoped>
.cell-actions { display: flex; align-items: center; gap: 6px; }
</style>
