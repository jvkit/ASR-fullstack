/** 麦克风采集：getUserMedia + AudioWorklet → 16kHz Int16 PCM 回调 */

export type PCMHandler = (bytes: ArrayBuffer) => void
/** 音量回调：rms 0~1（约 -60dB..0dB），100ms 一次 */
export type LevelHandler = (rms: number) => void

export class MicRecorder {
  private ctx: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  // 线上会议：display stream 的系统声轨混进同一 worklet（GainNode，音量同权重）
  private sysSource: MediaStreamAudioSourceNode | null = null
  private sysGain: GainNode | null = null
  // 线上会议麦克风门控（方案 A，用户拍板 2026-09-22）：0/1 增益，由前端「我开麦了」开关控制。
  // 依据：腾讯会议应用内闭麦不释放设备（ConsentStore 实测不可检测），只能手动；
  // 系统声常开（闭麦时同事声音只走系统声轨，不重复），麦克风默认关（防录进办公室闲聊）
  private micGain: GainNode | null = null
  private micEnabled = false
  private worklet: AudioWorkletNode | null = null
  private stream: MediaStream | null = null
  private onPcm: PCMHandler | null = null
  private analyser: AnalyserNode | null = null
  private levelTimer: ReturnType<typeof setInterval> | null = null

  async start(onPcm: PCMHandler, onLevel?: LevelHandler, displayStream?: MediaStream): Promise<void> {
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
    // 麦克风经门控 Gain 再进 worklet：线上会议模式默认关（micEnabled=false 时增益 0），
    // 普通会议模式恒开（无 displayStream 时不装门控，行为与旧版一致）
    if (displayStream) {
      this.micGain = this.ctx.createGain()
      this.micGain.gain.value = 0
      this.micEnabled = false
      this.source.connect(this.micGain)
      this.micGain.connect(this.worklet)
    } else {
      this.source.connect(this.worklet)
    }
    // 线上会议：系统声（远程会议里同事的声音）与麦克风混音后走同一 16k PCM，
    // 后端协议不变——对管线来说都是"实时音频进"
    if (displayStream) {
      const sysTrack = displayStream.getAudioTracks()[0]
      if (sysTrack) {
        this.sysSource = this.ctx.createMediaStreamSource(new MediaStream([sysTrack]))
        this.sysGain = this.ctx.createGain()
        this.sysSource.connect(this.sysGain)
        this.sysGain.connect(this.worklet)
      }
    }
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
    this.micGain?.disconnect()
    this.micGain = null
    this.micEnabled = false
    // 系统声混音节点：只断本侧连接，不 stop 轨（display stream 由持有方管理）
    this.sysGain?.disconnect()
    this.sysSource?.disconnect()
    this.sysGain = null
    this.sysSource = null
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

  /** 线上会议麦克风门控（方案 A）：true=我的声音上行，false=只上行系统声。
   *
   * 由前端「我开麦了」开关调用；普通会议模式无门控节点，本方法是空操作。
   * 为什么需要手动：腾讯会议闭麦不释放麦克风设备，系统层面无法检测（2026-09-22 实测）。
   */
  setMicEnabled(on: boolean): void {
    this.micEnabled = on
    if (this.micGain) this.micGain.gain.value = on ? 1 : 0
  }

  get micOn(): boolean {
    return this.micEnabled
  }
}
