/** 麦克风采集：getUserMedia + AudioWorklet → 16kHz Int16 PCM 回调 */

export type PCMHandler = (bytes: ArrayBuffer) => void

export class MicRecorder {
  private ctx: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private worklet: AudioWorkletNode | null = null
  private stream: MediaStream | null = null
  private onPcm: PCMHandler | null = null

  async start(onPcm: PCMHandler): Promise<void> {
    this.onPcm = onPcm
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true, // 会议外放场景防回声
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    this.ctx = new AudioContext()
    await this.ctx.audioWorklet.addModule(new URL('./pcm-processor.js', import.meta.url))
    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.worklet = new AudioWorkletNode(this.ctx, 'pcm-processor')
    this.worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      this.onPcm?.(e.data)
    }
    this.source.connect(this.worklet)
    this.worklet.connect(this.ctx.destination) // 静音直通，worklet 无输出但需要 connect
  }

  async stop(): Promise<void> {
    this.worklet?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    if (this.ctx) await this.ctx.close()
    this.ctx = null
    this.worklet = null
    this.source = null
    this.stream = null
  }
}
