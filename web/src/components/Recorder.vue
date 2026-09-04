<script setup lang="ts">
import { reactive } from 'vue'

const props = defineProps<{ recording: boolean; paused: boolean }>()
const emit = defineEmits<{
  start: [payload: { title: string }]
  pause: []
  resume: []
  stop: []
}>()

const form = reactive({ title: '未命名会议' })

function start() {
  if (!form.title.trim()) return
  emit('start', { title: form.title.trim() })
}

function togglePause() {
  props.paused ? emit('resume') : emit('pause')
}
</script>

<template>
  <el-card shadow="never">
    <el-form inline @submit.prevent>
      <el-form-item label="会议标题">
        <el-input v-model="form.title" :disabled="recording" style="width: 220px" />
      </el-form-item>
      <el-form-item>
        <el-button v-if="!recording" type="danger" @click="start">● 开始记录</el-button>
        <template v-else>
          <el-button type="warning" @click="togglePause">{{ paused ? '继续' : '暂停' }}</el-button>
          <el-button type="info" @click="emit('stop')">■ 结束会议</el-button>
        </template>
      </el-form-item>
      <el-tag v-if="recording" :type="paused ? 'info' : 'danger'" effect="dark">
        {{ paused ? 'PAUSED' : 'REC' }}
      </el-tag>
    </el-form>
  </el-card>
</template>
