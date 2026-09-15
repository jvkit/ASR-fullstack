/** 麦克风采集：getUserMedia + AudioWorklet → 16kHz Int16 PCM 回调 */

export type PCMHandler = (bytes: ArrayBuffer) => void
/** 音量回调：rms 0~1（约 -60dB..0dB），100ms 一次 */
export type LevelHandler = (rms: number) => void

export class MicRecorder {
  private ctx: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private worklet: AudioWorkletNode | null = null
  private stream: MediaStream | null = null
  private onPcm: PCMHandler | null = null
  private analyser: AnalyserNode | null = null
  private levelTimer: ReturnType<typeof setInterval> | null = null

  async start(onPcm: PCMHandler, onLevel?: LevelHandler): Promise<void> {
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
    // 音量表（录入质量反馈：让用户看见"麦克风有没有收到音"）
    if (onLevel) {
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 512
      this.source.connect(this.analyser)
      const buf = new Uint8Array(this.analyser.fftSize)
      this.levelTimer = setInterval(() => {
        if (!this.analyser) return
        this.analyser.getByteTimeDomainData(buf)
        let sum = 0
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128
          sum += v * v
        }
        onLevel(Math.sqrt(sum / buf.length))
      }, 100)
    }
  }

  async stop(): Promise<void> {
    if (this.levelTimer) {
      clearInterval(this.levelTimer)
      this.levelTimer = null
    }
    this.analyser?.disconnect()
    this.analyser = null
    this.worklet?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    if (this.ctx) await this.ctx.close()
    this.ctx = null
    this.worklet = null
    this.source = null
    this.stream = null
  }

  /** 暂停采集：AudioContext 挂起，PCM 回调停止；FunASR 连接保持 */
  async pause(): Promise<void> {
    if (this.ctx && this.ctx.state === 'running') await this.ctx.suspend()
  }

  /** 恢复采集 */
  async resume(): Promise<void> {
    if (this.ctx && this.ctx.state === 'suspended') await this.ctx.resume()
  }
}
