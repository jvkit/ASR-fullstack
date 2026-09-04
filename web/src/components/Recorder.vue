<script setup lang="ts">
import { reactive, ref } from 'vue'

const props = defineProps<{ recording: boolean }>()
const emit = defineEmits<{
  start: [payload: { title: string; recorder: string }]
  stop: []
}>()

const form = reactive({
  title: '产品周会',
  recorder: '',
})

async function start() {
  if (!form.title.trim()) return
  emit('start', { title: form.title.trim(), recorder: form.recorder.trim() })
}

function stop() {
  emit('stop')
}

// 默认采集人填本机用户名（简单标识，二期接 OA 账号）
form.recorder = localStorage.getItem('recorder') || ''
function persistRecorder() {
  localStorage.setItem('recorder', form.recorder)
}
</script>

<template>
  <el-card shadow="never">
    <el-form inline @submit.prevent>
      <el-form-item label="会议标题">
        <el-input v-model="form.title" :disabled="recording" style="width: 220px" />
      </el-form-item>
      <el-form-item label="采集人">
        <el-input
          v-model="form.recorder"
          :disabled="recording"
          placeholder="你的名字"
          style="width: 140px"
          @change="persistRecorder"
        />
      </el-form-item>
      <el-form-item>
        <el-button v-if="!recording" type="danger" @click="start">● 开始记录</el-button>
        <el-button v-else type="info" @click="stop">■ 结束会议</el-button>
      </el-form-item>
      <el-tag v-if="recording" type="danger" effect="dark">REC</el-tag>
    </el-form>
  </el-card>
</template>
