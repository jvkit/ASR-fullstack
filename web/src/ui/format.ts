/** 全局时间显示规范：
 * - 时间轴位置（时间戳/进度）：mm:ss（超 1 小时 h:mm:ss），与音频控件一致
 * - 时长（录音段/会议长度）：X 分 XX 秒（不足 1 分钟只出秒）
 */

/** 毫秒 → 时间轴位置 mm:ss / h:mm:ss */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '--:--'
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  const ss = String(s % 60).padStart(2, '0')
  if (h > 0) return `${h}:${String(m % 60).padStart(2, '0')}:${ss}`
  return `${String(m).padStart(2, '0')}:${ss}`
}

/** 秒 → 时长 X 分 XX 秒（不足 1 分钟只出秒） */
export function fmtDur(sec: number | null | undefined): string {
  if (sec == null) return '--'
  const s = Math.round(sec)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m} 分 ${s % 60} 秒` : `${s} 秒`
}
