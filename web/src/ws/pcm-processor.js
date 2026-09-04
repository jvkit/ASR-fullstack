/**
 * AudioWorkletProcessor：重采样到 16kHz → Int16 PCM → 按 0.1s 块上抛。
 * 浏览器实际采样率通常是 48k，线性插值重采样足够语音场景。
 */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.targetRate = 16000
    this.accum = new Float32Array(0) // 重采样后的待发送缓冲
    this.frameSize = 1600 // 0.1s @16k int16 → 3200 bytes
  }

  resample(input) {
    const ratio = sampleRate / this.targetRate
    const outLen = Math.floor(input.length / ratio)
    const out = new Float32Array(outLen)
    for (let i = 0; i < outLen; i++) {
      const pos = i * ratio
      const i0 = Math.floor(pos)
      const i1 = Math.min(i0 + 1, input.length - 1)
      const frac = pos - i0
      out[i] = input[i0] * (1 - frac) + input[i1] * frac
    }
    return out
  }

  floatToInt16(f32) {
    const i16 = new Int16Array(f32.length)
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]))
      i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
    }
    return i16
  }

  process(inputs) {
    const input = inputs[0]
    if (input && input[0] && input[0].length > 0) {
      const down = this.resample(input[0])
      // 拼接累积缓冲
      const merged = new Float32Array(this.accum.length + down.length)
      merged.set(this.accum)
      merged.set(down, this.accum.length)
      this.accum = merged

      // 凑满 0.1s 就发一块
      while (this.accum.length >= this.frameSize) {
        const chunk = this.accum.slice(0, this.frameSize)
        this.accum = this.accum.slice(this.frameSize)
        const i16 = this.floatToInt16(chunk)
        this.port.postMessage(i16.buffer, [i16.buffer])
      }
    }
    return true
  }
}

registerProcessor('pcm-processor', PCMProcessor)
