# -*- coding: utf-8 -*-
"""麦克风占用监控：轮询 Windows 注册表 ConsentStore，记录各应用麦克风使用状态变化。

目的：验证「检测腾讯会议是否占用麦克风（开麦/闭麦）」这条路是否打通。
用户操作流程：打开腾讯会议 → 开会 → 开麦说话 → 闭麦；脚本全程记录状态翻转时刻。

用法：python mic_monitor.py [输出日志路径，默认 stdout+文件]
每 1 秒轮询一次；状态变化（开麦/闭麦/首次见到某应用）打印一行：
    2026-09-22 10:30:15 | WeMeetApp.exe  : 开始使用麦克风 (IN-USE)
    2026-09-22 10:32:40 | WeMeetApp.exe  : 停止使用 (上次使用 10:30:15 ~ 10:32:38)
"""
import subprocess
import sys
import time
from datetime import datetime

# 轮询间隔（秒）：注册表值有写入延迟，间隔太小无意义
POLL_SEC = 1.0

PS_SCRIPT = r"""
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$bases = @(
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged',
  'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone'
)
foreach ($b in $bases) {
  if (-not (Test-Path $b)) { continue }
  Get-ChildItem $b | ForEach-Object {
    $k = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
    if ($null -eq $k.LastUsedTimeStart) { return }
    $name = ($_.PSChildName -replace '#','\') -replace '_[0-9a-z]{12}([.]|($_))',''
    Write-Output ("{0}|{1}|{2}" -f $_.PSChildName, $k.LastUsedTimeStart, $k.LastUsedTimeStop)
  }
}
"""


def poll() -> dict[str, tuple[int, int]]:
    """读注册表 → {应用名: (start_filetime, stop_filetime)}。stop=0 表示正在使用。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", PS_SCRIPT],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as e:
        print(f"[warn] powershell 调用失败: {e}", flush=True)
        return {}
    out: dict[str, tuple[int, int]] = {}
    for line in r.stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        key, start, stop = parts
        try:
            out[key] = (int(start), int(stop))
        except ValueError:
            continue
    return out


def fmt_ts(ft: int) -> str:
    """filetime → 本地时间字符串；0 → IN-USE"""
    if ft == 0:
        return "IN-USE"
    return datetime.fromtimestamp((ft - 116444736000000000) / 10_000_000).strftime("%H:%M:%S")


def main() -> None:
    logfile = sys.argv[1] if len(sys.argv) > 1 else None
    f = open(logfile, "a", encoding="utf-8") if logfile else None

    def emit(msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} | {msg}"
        print(line, flush=True)
        if f:
            f.write(line + "\n")
            f.flush()

    emit("监控启动：每 1s 轮询 ConsentStore（开麦应用 stop=0；闭麦后写入停止时间）")
    emit("请开始操作：打开腾讯会议 → 开麦 → 说话 → 闭麦（全程保持本窗口开着）")
    prev: dict[str, tuple[int, int]] = {}
    while True:
        cur = poll()
        if not cur and prev:
            emit("[warn] 轮询返回空（powershell 卡了？）")
        for key, (start, stop) in cur.items():
            old = prev.get(key)
            if old is None:
                emit(f"{key}: 首次见到 | start={fmt_ts(start)} stop={fmt_ts(stop)}")
            elif old != (start, stop):
                was_using = old[1] == 0
                now_using = stop == 0
                if now_using and not was_using:
                    emit(f"{key}: >>> 开始使用麦克风 (IN-USE) <<<")
                elif not now_using and was_using:
                    emit(f"{key}: <<< 停止使用麦克风 >>> (该次使用 {fmt_ts(start)} ~ {fmt_ts(stop)})")
                else:
                    emit(f"{key}: 状态变化 start={fmt_ts(start)} stop={fmt_ts(stop)}")
        for key in prev:
            if key not in cur:
                emit(f"{key}: 从注册表消失")
        prev = cur
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
