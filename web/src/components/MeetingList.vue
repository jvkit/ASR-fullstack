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

async function load() {
  loading.value = true
  try {
    const resp = await fetch('/api/meetings')
    meetings.value = (await resp.json()) as MeetingRow[]
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div style="display: flex; align-items: center">
        <span style="flex: 1">历史会议</span>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </template>

    <el-table :data="meetings" v-loading="loading" @row-click="(r) => emit('open', r.id)">
      <el-table-column prop="title" label="标题" min-width="180" />
      <el-table-column label="开始时间" width="160">
        <template #default="{ row }">
          {{ row.started_at ? new Date(row.started_at).toLocaleString('zh-CN') : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="时长" width="110">
        <template #default="{ row }">
          {{ row.started_at && row.ended_at ? Math.round((Date.parse(row.ended_at) - Date.parse(row.started_at)) / 60000) + ' 分钟' : '进行中' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === 'live' ? 'danger' : 'info'">
            {{ row.status === 'live' ? '进行中' : '已结束' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
