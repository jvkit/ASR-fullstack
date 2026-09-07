<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { showConfirmDialog, showToast } from 'vant'
import { MicRecorder } from '../ws/audio'

const speakers = ref<string[]>([])
const loading = ref(false)
const errorMsg = ref('')
const okMsg = ref('')
const insecure = ref(false) // http 非安全上下文：浏览器禁止麦克风

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

onMounted(() => {
  load()
  // http 访问非 localhost 时 navigator.mediaDevices 为 undefined，麦克风必然失败，提前提示
  if (typeof navigator !== 'undefined' && navigator.mediaDevices === undefined) {
    insecure.value = true
  }
})

async function startRecord() {
  if (insecure.value) {
    errorMsg.value = '当前为 http 访问，浏览器禁止使用麦克风。请改用 https 地址打开本页。'
    return
  }
  errorMsg.value = ''
  okMsg.value = ''
  chunks = []
  recorded.value = 0
  try {
    await mic.start((bytes) => chunks.push(bytes))
  } catch (e) {
    recording.value = false
    const name = (e as DOMException)?.name
    errorMsg.value =
      name === 'NotAllowedError'
        ? '麦克风权限被拒绝：请点击浏览器地址栏左侧的锁形图标，允许麦克风后重试。'
        : `无法打开麦克风：${e}`
    return
  }
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
  try {
    await showConfirmDialog({ title: '删除声纹', message: `确定删除「${name}」？` })
  } catch {
    return // 取消
  }
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
  <div class="speaker-page">
    <van-notice-bar
      v-if="insecure"
      mode="closeable"
      color="#fff"
      background="#e64340"
      text="当前为 http 访问，浏览器禁止使用麦克风，请改用 https 地址打开"
    />
    <van-notice-bar
      v-else
      mode="closeable"
      color="#614700"
      background="#fffbe8"
      text="声纹模型已升级（ERes2NetV2）：旧注册数据不兼容，请所有已注册人员重新录一段"
    />
    <van-notice-bar v-if="errorMsg" mode="closeable" color="#fff" background="#e64340" :text="errorMsg" @close="errorMsg = ''" />
    <van-notice-bar v-if="okMsg" mode="closeable" color="#0a7a4b" background="#e8f7ef" :text="okMsg" @close="okMsg = ''" />

    <van-cell-group inset class="block">
      <template #title>注册新声纹</template>
      <van-field
        v-model="form.name"
        label="姓名"
        placeholder="如：张三"
        :disabled="recording"
      />
      <van-field label="说明">
        <template #input>
          <span class="tip">对着当前设备的麦克风念 5~10 秒日常话语（用开会时的同一台设备），注册后会议中自动识别为该姓名</span>
        </template>
      </van-field>
      <div class="rec-actions">
        <van-button
          v-if="!recording"
          type="danger"
          round
          block
          :disabled="insecure || !form.name.trim()"
          :loading="loading"
          @click="startRecord"
        >
          ● 开始录音
        </van-button>
        <van-button
          v-else
          type="success"
          round
          block
          @click="stopAndEnroll"
        >
          ■ 结束并注册{{ recorded ? `（已录 ${recorded.toFixed(1)}s）` : '' }}
        </van-button>
      </div>
    </van-cell-group>

    <van-cell-group inset class="block" :title="`已注册（${speakers.length}）`">
      <van-empty v-if="!speakers.length" description="暂无注册声纹" />
      <van-cell v-for="s in speakers" :key="s" :title="s">
        <template #right-icon>
          <van-button size="small" type="danger" plain @click="remove(s)">删除</van-button>
        </template>
      </van-cell>
    </van-cell-group>
  </div>
</template>

<style scoped>
.speaker-page { padding-bottom: 24px; }
.block { margin-top: 12px; }
.rec-actions { padding: 8px 16px 16px; }
.tip { font-size: 13px; color: #969799; line-height: 1.6; }
</style>
