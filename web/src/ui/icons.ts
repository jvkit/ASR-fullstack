/**
 * 内联线性图标库（24x24 viewBox，stroke 风格，stroke-width 1.8，圆角端点）。
 * 不引第三方图标库；值是 <svg> 内部 markup，由 AppIcon 渲染。
 * 实心图形（播放/停止等）用 fill 版本，路径里自带 fill 属性。
 */

export const icons: Record<string, string> = {
  // 开会（麦克风）
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M9 21h6"/>',
  // 历史会议（时钟）
  history: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  // 声纹库（人群）
  users: '<circle cx="9" cy="8.5" r="3.2"/><path d="M3.5 19c.6-3 2.8-4.5 5.5-4.5S13.9 16 14.5 19"/><circle cx="16.5" cy="9.5" r="2.6"/><path d="M15.8 14.7c2.6.1 4.2 1.5 4.7 4.3"/>',
  // 播放（实心三角）
  play: '<path d="M8 5.4v13.2c0 .8.9 1.3 1.6.9l10.2-6.6c.6-.4.6-1.4 0-1.8L9.6 4.5c-.7-.4-1.6.1-1.6.9z" fill="currentColor" stroke="none"/>',
  // 暂停
  pause: '<path d="M9 5v14M15 5v14" stroke-width="2.4"/>',
  // 停止（实心方块）
  stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none"/>',
  // 返回
  back: '<path d="M15 5l-7 7 7 7"/>',
  // 下载
  download: '<path d="M12 4v10"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M4.5 19.5h15"/>',
  // 刷新
  refresh: '<path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 3.5V8h-4.5"/>',
  // 关闭
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  // 展开
  chevronDown: '<path d="M6 9.5l6 6 6-6"/>',
  // 收起（分组折叠）
  chevronRight: '<path d="M9.5 6l6 6-6 6"/>',
  // 对勾
  check: '<path d="M4.5 12.5l5 5L19.5 7"/>',
  // 警告
  warning: '<path d="M12 4L2.8 19.5h18.4L12 4z"/><path d="M12 10v4.5"/><circle cx="12" cy="17.2" r="0.4" fill="currentColor"/>',
  // 复制
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4.5A1.5 1.5 0 0 1 3 13.5v-9A1.5 1.5 0 0 1 4.5 3h9A1.5 1.5 0 0 1 15 4.5V5"/>',
  // 删除
  trash: '<path d="M4.5 6.5h15"/><path d="M9.5 6V4.5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1V6"/><path d="M6.5 6.5l1 13h9l1-13"/><path d="M10 10.5v6M14 10.5v6"/>',
  // 新增
  plus: '<path d="M12 5v14M5 12h14"/>',
  // 屏幕/线上会议
  monitor: '<rect x="3" y="4.5" width="18" height="12.5" rx="2"/><path d="M9 21h6M12 17v4"/>',
  // 文档/纪要
  doc: '<path d="M6 3.5h8L19 8.5V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z"/><path d="M13.5 3.5V9H19"/><path d="M8.5 12.5h7M8.5 16h7"/>',
  // 待办（清单）
  todo: '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M8.5 12.2l2.4 2.4 4.6-5"/>',
  // 调试（齿轮/管道用滑块图标）
  tune: '<path d="M5 8h7M16 8h3M5 16h3M12 16h7"/><circle cx="14" cy="8" r="2.2"/><circle cx="10" cy="16" r="2.2"/>',
  // 眼睛（预览）
  eye: '<path d="M2.5 12S6 5.8 12 5.8 21.5 12 21.5 12 18 18.2 12 18.2 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  // 摄像头
  camera: '<rect x="3" y="7" width="13" height="11" rx="2"/><path d="M16 10.5l5-2.5v9l-5-2.5"/>',
  // 音量/声音
  volume: '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5H4z"/><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a8 8 0 0 1 0 11"/>',
  // 外开链接
  openOut: '<path d="M10 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-4"/><path d="M14 4h6v6"/><path d="M20 4L11 13"/>',
  // 定位/跳转
  locate: '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.5"/><path d="M12 2.5V5M12 19v2.5M2.5 12H5M19 12h2.5"/>',
}
