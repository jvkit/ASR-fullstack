/** 后端 WS 客户端：连接、start/stop、事件分发 */

export type ServerMessage =
  | { type: 'started'; meeting_id: string }
  | { type: 'transcript_online'; text: string }
  | { type: 'transcript'; seq: number; speaker: string; text: string; start_ms: number; end_ms: number }
  | { type: 'summary'; seq: number; final: boolean; title: string; points: string[]; action_items: unknown[]; attributes: unknown[] }
  | { type: 'attributes'; title: string; attributes: { key: string; value: string }[] }
  | { type: 'ended'; meeting_id: string }
  | { type: 'error'; message: string }

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

  start(title: string): void {
    this.ws?.send(JSON.stringify({ type: 'start', title }))
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
