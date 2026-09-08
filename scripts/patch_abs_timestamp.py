"""patch：给 gpu FunASR ws server 加「绝对时间戳偏移」。

问题：2pass-offline 的 timestamp 是 ASR 对「段内音频」算的相对时间，
从不加该段在整个会话中的绝对起点 → 客户端拿到 start_ms 全是几十~几千毫秒，
滚动打标切错音频（报 effective audio duration is too short 500）、
会末重标注时间重叠永远匹配不上（applied to 0/N）。

方案：每个连接维护 total_audio_ms（累计接收音频毫秒，不重置）。
async_asr 收到段音频时，其绝对起点 = total_audio_ms - len(audio_in)//32
（16kHz*2bytes=32000 B/s = 32 B/ms）。把 timestamp 与 sentence_info 的
start/end 都加上该偏移。

幂等：锚点检测，重复运行安全。
"""
from pathlib import Path

SERVER = Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py")

src = SERVER.read_text(encoding="utf-8")

# 1) 连接初始化：加 total_audio_ms 计数
INIT_ANCHOR = '''    websocket.vad_pre_idx = 0
    speech_start = False'''
INIT_NEW = '''    websocket.vad_pre_idx = 0
    websocket.total_audio_ms = 0  # [patch] 累计接收音频毫秒（会话级，不重置），用于绝对时间戳偏移
    speech_start = False'''
if "websocket.total_audio_ms = 0" not in src:
    assert INIT_ANCHOR in src, "init anchor not found"
    src = src.replace(INIT_ANCHOR, INIT_NEW, 1)
    print("[1/4] total_audio_ms init added")
else:
    print("[1/4] already patched, skip")

# 2) 接收循环：每个 PCM 帧累加 total_audio_ms
ACC_ANCHOR = '''            duration_ms = _pcm_duration_ms(pcm, fs=websocket.audio_fs, ch=1, sampwidth=2)
            websocket.vad_pre_idx += duration_ms'''
ACC_NEW = '''            duration_ms = _pcm_duration_ms(pcm, fs=websocket.audio_fs, ch=1, sampwidth=2)
            websocket.vad_pre_idx += duration_ms
            websocket.total_audio_ms += duration_ms  # [patch]'''
if "websocket.total_audio_ms += duration_ms" not in src:
    assert ACC_ANCHOR in src, "acc anchor not found"
    src = src.replace(ACC_ANCHOR, ACC_NEW, 1)
    print("[2/4] total_audio_ms accumulation added")
else:
    print("[2/4] already patched, skip")

# 3) async_asr 顶部：计算本段绝对起点毫秒
ABS_ANCHOR = '''    text = rec_result.get("text", "")
    timestamp = rec_result.get("timestamp", None)
    sentence_info = rec_result.get("sentence_info", None)'''
ABS_NEW = '''    text = rec_result.get("text", "")
    timestamp = rec_result.get("timestamp", None)
    sentence_info = rec_result.get("sentence_info", None)

    # [patch] 本段音频在整个会话中的绝对起始毫秒（16kHz/16bit/mono = 32 B/ms）
    _seg_start_ms = max(0, getattr(websocket, "total_audio_ms", 0) - len(audio_in) // 32)
    if timestamp is not None:
        timestamp = [[int(s) + _seg_start_ms, int(e) + _seg_start_ms] for s, e in timestamp]
    if sentence_info is not None:
        for _si in sentence_info:
            if "start" in _si:
                _si["start"] = int(_si["start"]) + _seg_start_ms
            if "end" in _si:
                _si["end"] = int(_si["end"]) + _seg_start_ms'''
if "_seg_start_ms" not in src:
    assert ABS_ANCHOR in src, "abs anchor not found"
    src = src.replace(ABS_ANCHOR, ABS_NEW, 1)
    print("[3/4] seg absolute offset added")
else:
    print("[3/4] already patched, skip")

SERVER.write_text(src, encoding="utf-8")
print("[4/4] written", SERVER)
