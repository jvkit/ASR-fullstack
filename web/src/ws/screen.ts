/** 屏幕帧采集：getDisplayMedia video 轨 → canvas 缩略 → JPEG → POST /frames
 *
 * 线上会议视觉通道前端侧（docs/achit/视频侧接口与代码地图-20260922.md §4）。
 * - 帧间隔/宽度/质量从后端 /api/config 读（visual_* 配置项）
 * - video 轨保活规则：不要 stop video 轨（会连累 audio 轨被浏览器结束）；
 *   暂停抽帧用 track.enabled = false
 * - 上行失败静默重试（下一帧继续），丢帧不阻塞采集
 */

export type FrameSentHandler = (accepted: number, buffered: number) => void

interface VisualConfig {
  visual_frame_interval_sec: number
  visual_frame_width: number
  visual_frame_quality: number
}

export class ScreenFrameCapture {
  private video: HTMLVideoElement | null = null
  private canvas: HTMLCanvasElement | null = null
  private ctx: CanvasRenderingContext2D | null = null
  private timer: ReturnType<typeof setInterval> | null = null
  private cfg: VisualConfig = {
    visual_frame_interval_sec: 10,
    visual_frame_width: 960,
    visual_frame_quality: 80,
  }
  private startedAt = 0
  private sending = false
  onSent: FrameSentHandler | null = null

  /** 从已有的 display stream（音频侧 ScreenRecorder 建立的）开始抽帧上行 */
  async start(displayStream: MediaStream, meetingId: string): Promise<void> {
    // 读后端配置（唯一数据源，不硬编码）
    try {
      const res = await fetch('/api/config')
      const c = await res.json()
      this.cfg = {
        visual_frame_interval_sec: c.visual_frame_interval_sec ?? 10,
        visual_frame_width: c.visual_frame_width ?? 960,
        visual_frame_quality: c.visual_frame_quality ?? 80,
      }
    } catch {
      // 配置读不到用默认值，不阻塞
    }
    const track = displayStream.getVideoTracks()[0]
    if (!track) return

    this.video = document.createElement('video')
    this.video.srcObject = new MediaStream([track])  // 独立 stream 引用，音频侧 stop 不连累
    this.video.muted = true
    await this.video.play()

    this.canvas = document.createElement('canvas')
    const vw = this.video.videoWidth || 1920
    const vh = this.video.videoHeight || 1080
    this.canvas.width = this.cfg.visual_frame_width
    this.canvas.height = Math.round(this.cfg.visual_frame_width * (vh / vw))
    this.ctx = this.canvas.getContext('2d')!
    this.startedAt = Date.now()

    const intervalMs = Math.max(1000, this.cfg.visual_frame_interval_sec * 1000)
    this.timer = setInterval(() => this._capture(meetingId, track), intervalMs)
  }

  private async _capture(meetingId: string, track: MediaStreamTrack): Promise<void> {
    if (!this.ctx || !this.canvas || !this.video || this.sending) return
    if (!track.enabled || this.video.readyState < 2) return
    this.sending = true
    try {
      this.ctx.drawImage(this.video, 0, 0, this.canvas.width, this.canvas.height)
      const dataUrl = this.canvas.toDataURL('image/jpeg', this.cfg.visual_frame_quality / 100)
      const jpegB64 = dataUrl.slice(dataUrl.indexOf(',') + 1)
      const tMs = Date.now() - this.startedAt
      const res = await fetch(`/api/meetings/${meetingId}/frames`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ frames: [{ t_ms: tMs, jpeg_b64: jpegB64 }] }),
      })
      if (res.ok) {
        const r = await res.json()
        this.onSent?.(r.accepted ?? 0, r.buffered ?? 0)
      }
      // 非 ok（413 超限等）：丢本帧，下一帧继续，不重试不阻塞
    } catch {
      // 网络抖动：丢本帧，不阻塞采集
    } finally {
      this.sending = false
    }
  }

  /** 暂停抽帧（不动 display stream，音频不受影响） */
  pause(): void {
    if (this.timer) {
      clearInterval(this.timer)
      this.timer = null
    }
  }

  /** 恢复抽帧 */
  resume(meetingId: string, displayStream: MediaStream): void {
    if (this.timer) return
    const track = displayStream.getVideoTracks()[0]
    if (!track) return
    const intervalMs = Math.max(1000, this.cfg.visual_frame_interval_sec * 1000)
    this.timer = setInterval(() => this._capture(meetingId, track), intervalMs)
  }

  stop(): void {
    this.pause()
    this.video?.pause()
    this.video = null
    this.canvas = null
    this.ctx = null
  }
}
