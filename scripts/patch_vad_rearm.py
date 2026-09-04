"""补丁：修复 2pass 流式会话只出第一段 offline 的 bug。

根因：vad 检测到 speech_end（或 is_speaking=false）触发 offline 后，
没有重置 vad 状态机，`speech_start` 永远保持 False，后续所有音频
不进入 frames_asr，下一句永远不会被转写。

修复：在 offline 分段触发点（"if audio_in:" 判断之前）重置 vad 状态：
  - status_dict_vad cache 清空
  - vad_pre_idx 归零
  - 用最近 20 帧（1.2s）重新 priming，让模型看到"说话尾→静默"过渡，
    从而能正确检测到下一句的 speech start
"""
import subprocess

SRC = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py"

# 在触发 offline 判断前插入 vad 重置
old = """                if websocket.mode in ("2pass", "offline"):
                    audio_in = b"".join(frames_asr)
                    if not audio_in and speech_end_i != -1:
                        audio_in = b"".join(pending_offline_audio)
                    reason = "vad_end" if speech_end_i != -1 else "not_speaking"
"""
new = """                # 修复：offline 分段触发后重置 vad 状态机，否则后续 speech_start 不再置位，
                # 整场会话只有第一段会被转写。保留最近 20 帧做 priming，让 vad
                # 从"说话尾→静默"过渡开始检测下一句。
                if websocket.mode in ("2pass", "offline") and (speech_end_i != -1 or not websocket.is_speaking):
                    websocket.status_dict_vad["cache"] = {}
                    websocket.vad_pre_idx = 0
                    frames = frames[-20:]

                if websocket.mode in ("2pass", "offline"):
                    audio_in = b"".join(frames_asr)
                    if not audio_in and speech_end_i != -1:
                        audio_in = b"".join(pending_offline_audio)
                    reason = "vad_end" if speech_end_i != -1 else "not_speaking"
"""


def main() -> None:
    out = subprocess.run(["cat", SRC], capture_output=True, text=True)
    src = out.stdout
    if "修复：offline 分段触发后重置 vad 状态机" in src:
        print("already patched")
        return
    if old not in src:
        print("ERROR: anchor not found")
        raise SystemExit(1)
    src = src.replace(old, new, 1)
    subprocess.run(
        ["bash", "-c", f"cat > {SRC}"],
        input=src,
        text=True,
        check=True,
    )
    r = subprocess.run(
        ["/home/bo1/jvkit/asr/venv/bin/python", "-m", "py_compile", SRC],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("COMPILE FAILED:", r.stderr)
        raise SystemExit(1)
    print("patched OK")


if __name__ == "__main__":
    main()
