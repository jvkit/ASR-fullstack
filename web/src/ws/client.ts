/** 后端 WS 客户端：连接、start/stop、事件分发 */

export type ServerMessage =
  | { type: 'started'; meeting_id: string }
  | { type: 'transcript'; seq: number; speaker: string; text: string; start_ms: number; end_ms: number }
  | { type: 'transcript_update'; seq: number; speaker: string; text: string; start_ms: number; end_ms: number; corrected?: boolean }
  | { type: 'summary'; seq: number; scope?: string; final: boolean; title: string; points: string[]; action_items: unknown[]; attributes: unknown[] }
  | { type: 'attributes'; title: string; attributes: { key: string; value: string }[] }
  | { type: 'ended'; meeting_id: string }
  | { type: 'reannotated'; meeting_id: string }
  // 会末版进度（2026-09-28 双版本架构）：stage=reannotate|waves|summary|done
  | { type: 'final_progress'; stage: string; pct: number }
  | { type: 'final_done'; meeting_id: string }
  | { type: 'error'; message: string }

export type VersionMode = 'live' | 'final' | 'both'
export type SourceMode = 'mic' | 'online'

export class MeetingWs {
  private ws: WebSocket | null = null

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      this.ws = new WebSocket(`${proto}://${location.host}/ws/meeting`)
      this.ws.binaryType = 'arraybuffer'
      this.ws.onopen = () => resolve()
      this.ws.onerror = (e) => reject(e)
    })
  }

  get ready(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  start(
    title: string,
    opts?: {
      replayOf?: string
      online?: boolean
      versionMode?: VersionMode
      sourceMode?: SourceMode
      /** 会议级追加热词（逗号分隔）。注：后端当前从 config 读热词库，本字段为
       *  前向兼容预留（服务端暂不消费，未知键被静默忽略，不影响开会）。 */
      hotwords?: string
    },
  ): void {
    const payload: Record<string, unknown> = { type: 'start', title }
    if (opts?.replayOf) payload.replay_of = opts.replayOf
    if (opts?.online) payload.online = true
    if (opts?.versionMode) payload.version_mode = opts.versionMode
    if (opts?.sourceMode) payload.source_mode = opts.sourceMode
    if (opts?.hotwords?.trim()) payload.hotwords = opts.hotwords.trim()
    this.ws?.send(JSON.stringify(payload))
  }

  sendPcm(bytes: ArrayBuffer): void {
    this.ws?.send(bytes)
  }

  pause(): void {
    this.ws?.send(JSON.stringify({ type: 'pause' }))
  }

  resume(): void {
    this.ws?.send(JSON.stringify({ type: 'resume' }))
  }

  stop(): void {
    this.ws?.send(JSON.stringify({ type: 'stop' }))
  }

  onMessage(handler: (msg: ServerMessage) => void): void {
    if (!this.ws) return
    this.ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        try {
          handler(JSON.parse(e.data) as ServerMessage)
        } catch {
          /* ignore */
        }
      }
    }
  }

  close(): void {
    this.ws?.close()
    this.ws = null
  }
}
