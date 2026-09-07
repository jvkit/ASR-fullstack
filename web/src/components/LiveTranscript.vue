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
  <van-cell-group inset class="panel">
    <template #title>
      <span>实时转写</span>
      <van-tag
        v-for="spk in speakers"
        :key="spk"
        size="medium"
        class="spk-tag"
        :style="{ backgroundColor: colorOf(spk), color: '#fff', border: 'none' }"
      >
        {{ spk }}
      </van-tag>
    </template>

    <div ref="listEl" class="list">
      <div v-for="u in utterances" :key="u.seq" class="utt">
        <van-tag
          size="medium"
          plain
          class="spk-tag"
          :style="{ borderColor: colorOf(u.speaker), color: colorOf(u.speaker) }"
        >
          {{ u.speaker }}
        </van-tag>
        <span class="utt-text">{{ u.text }}</span>
      </div>
      <div v-if="onlineText" class="online">{{ onlineText }}…</div>
      <van-empty v-if="!utterances.length && !onlineText" description="开始说话后将实时显示转写" />
    </div>
  </van-cell-group>
</template>

<style scoped>
.panel { display: flex; flex-direction: column; }
.list { height: 40vh; overflow-y: auto; padding: 8px 16px; }
.utt { margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px; }
.utt-text { flex: 1; word-break: break-word; }
.spk-tag { flex-shrink: 0; }
.online { color: #999; font-style: italic; padding-left: 2px; }
@media (min-width: 768px) { .list { height: 60vh; } }
</style>
