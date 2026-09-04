<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

interface Utterance {
  seq: number
  speaker: string
  text: string
}

const props = defineProps<{
  utterances: Utterance[]
  onlineText: string
  speakers: string[]
  colorOf: (speaker: string) => string
}>()

const listEl = ref<HTMLElement>()

watch(
  () => props.utterances.length,
  async () => {
    await nextTick()
    listEl.value?.scrollTo({ top: listEl.value.scrollHeight, behavior: 'smooth' })
  },
)
</script>

<template>
  <el-card shadow="never" style="height: 70vh; display: flex; flex-direction: column">
    <template #header>
      <div style="display: flex; align-items: center; gap: 12px">
        <span>实时转写</span>
        <el-tag
          v-for="spk in speakers"
          :key="spk"
          size="small"
          :style="{ backgroundColor: colorOf(spk), color: '#fff', border: 'none' }"
        >
          {{ spk }}
        </el-tag>
      </div>
    </template>

    <div ref="listEl" style="flex: 1; overflow-y: auto">
      <div v-for="u in utterances" :key="u.seq" style="margin-bottom: 10px">
        <el-tag
          size="small"
          effect="plain"
          :style="{ borderColor: colorOf(u.speaker), color: colorOf(u.speaker) }"
        >
          {{ u.speaker }}
        </el-tag>
        <span style="margin-left: 8px">{{ u.text }}</span>
      </div>
      <div v-if="onlineText" style="color: #999; font-style: italic">
        {{ onlineText }}…
      </div>
      <el-empty v-if="!utterances.length && !onlineText" description="开始说话后将实时显示转写" />
    </div>
  </el-card>
</template>
