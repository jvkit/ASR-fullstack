<script setup lang="ts">
import { reactive } from 'vue'
import { QuestionFilled } from '@element-plus/icons-vue'

const props = defineProps<{ recording: boolean; paused: boolean }>()
const emit = defineEmits<{
  start: [payload: { title: string; spkThreshold: number }]
  pause: []
  resume: []
  stop: []
}>()

const form = reactive({ title: '未命名会议', spkThreshold: 0.55 })

function start() {
  if (!form.title.trim()) return
  const thr = Number(form.spkThreshold)
  emit('start', {
    title: form.title.trim(),
    spkThreshold: Number.isFinite(thr) && thr > 0 && thr < 1 ? thr : 0.55,
  })
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
      <el-form-item label="说话人阈值">
        <el-input-number
          v-model="form.spkThreshold"
          :min="0.3"
          :max="0.95"
          :step="0.05"
          :precision="2"
          :disabled="recording"
          style="width: 110px"
        />
      </el-form-item>
      <el-tooltip
        content="越高越严格（不同人更难被合并为同一人），越低越宽松。分不出人→调高；把一个人拆成多个→调低"
        placement="top"
      >
        <el-icon v-if="!recording" style="color: #909399; align-self: center"><QuestionFilled /></el-icon>
      </el-tooltip>
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
