<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { MicRecorder } from '../ws/audio'

const speakers = ref<string[]>([])
const loading = ref(false)
const errorMsg = ref('')
const okMsg = ref('')

const form = reactive({ name: '' })
const mic = reactive(new MicRecorder())
const recording = ref(false)
const recorded = ref(0) // 已录秒数
let chunks: ArrayBuffer[] = []
let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const r = await fetch('/api/speakers')
    const d = await r.json()
    speakers.value = d.speakers || []
  } catch (e) {
    errorMsg.value = `加载失败：${e}`
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function startRecord() {
  errorMsg.value = ''
  okMsg.value = ''
  chunks = []
  recorded.value = 0
  await mic.start((bytes) => chunks.push(bytes))
  recording.value = true
  timer = setInterval(() => {
    recorded.value = chunks.length * 0.1
  }, 200)
}

async function stopAndEnroll() {
  recording.value = false
  if (timer) clearInterval(timer)
  await mic.stop()
  const name = form.name.trim()
  if (!name) {
    errorMsg.value = '先填姓名'
    return
  }
  if (chunks.length < 30) {
    errorMsg.value = `音频太短（${(chunks.length * 0.1).toFixed(1)}s），至少录 3 秒，建议 5 秒以上`
    return
  }
  loading.value = true
  try {
    const pcm = new Blob(chunks as BlobPart[], { type: 'application/octet-stream' })
    const r = await fetch(`/api/speakers/enroll?name=${encodeURIComponent(name)}`, {
      method: 'POST',
      body: pcm,
    })
    const d = await r.json()
    if (!r.ok) {
      errorMsg.value = d.detail || '注册失败'
      return
    }
    speakers.value = d.speakers || []
    okMsg.value = `已注册「${name}」，共 ${speakers.value.length} 人`
    form.name = ''
  } catch (e) {
    errorMsg.value = `注册失败：${e}`
  } finally {
    loading.value = false
    chunks = []
    recorded.value = 0
  }
}

async function remove(name: string) {
  loading.value = true
  try {
    const r = await fetch(`/api/speakers/${encodeURIComponent(name)}`, { method: 'DELETE' })
    const d = await r.json()
    if (!r.ok) {
      errorMsg.value = d.detail || '删除失败'
      return
    }
    speakers.value = d.speakers || []
    okMsg.value = `已删除「${name}」`
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <div style="display: flex; align-items: center; gap: 12px">
        <span style="flex: 1; font-weight: 600">声纹管理</span>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </template>

    <el-alert
      v-if="errorMsg"
      :title="errorMsg"
      type="error"
      :closable="false"
      style="margin-bottom: 12px"
      @close="errorMsg = ''"
    />
    <el-alert
      v-if="okMsg"
      :title="okMsg"
      type="success"
      :closable="true"
      style="margin-bottom: 12px"
      @close="okMsg = ''"
    />

    <h4 style="margin: 0 0 8px">注册新声纹</h4>
    <el-form inline @submit.prevent>
      <el-form-item label="姓名">
        <el-input v-model="form.name" placeholder="如：张三" style="width: 160px" :disabled="recording" />
      </el-form-item>
      <el-form-item>
        <el-button v-if="!recording" type="danger" @click="startRecord">● 开始录音</el-button>
        <el-button v-else type="success" @click="stopAndEnroll">■ 结束并注册{{ recorded ? `（${recorded.toFixed(1)}s）` : '' }}</el-button>
      </el-form-item>
    </el-form>
    <el-text type="info" size="small">
      对着这台电脑的麦克风念 5~10 秒日常话语（开会时的设备），注册后会议中自动识别为该姓名。
    </el-text>

    <h4 style="margin: 20px 0 8px">已注册（{{ speakers.length }}）</h4>
    <el-empty v-if="!speakers.length" description="暂无注册声纹" />
    <div v-for="s in speakers" :key="s" style="margin-bottom: 6px; display: flex; align-items: center; gap: 8px">
      <el-tag effect="plain" style="min-width: 100px">{{ s }}</el-tag>
      <el-popconfirm title="确定删除该声纹？" @confirm="remove(s)">
        <template #reference>
          <el-button size="small" type="danger" plain>删除</el-button>
        </template>
      </el-popconfirm>
    </div>
  </el-card>
</template>
