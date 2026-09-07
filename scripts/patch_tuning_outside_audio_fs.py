"""patch：说话人/VAD 动态参数解析移出 audio_fs 门控（干净重写版）。

bug：spk_threshold / vad_* / max_speakers 的解析嵌套在
`if "audio_fs" in messagejson:` 块内，而我们的客户端配置消息不传 audio_fs，
导致整块解析从未执行。

修复：
1. 顶层新增 _apply_tuning_params（插在 _safe_int 完整函数之后）
2. audio_fs 块内的内联解析替换为调用
3. audio_fs 门控外（end_of_input 前）统一调用一次

幂等：锚点检测。
"""
from pathlib import Path

SERVER = Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py")
src = SERVER.read_text(encoding="utf-8")

if "_apply_tuning_params" in src:
    print("already patched, skip all")
    raise SystemExit(0)

# 1) 顶层 helper：插在 _safe_int 完整函数之后（用其函数体结尾做锚点）
SAFE_INT_END = """def _safe_int(v, default):
    try:
        return int(v)
    except Exception:
        return default
"""
HELPER = SAFE_INT_END + '''

def _apply_tuning_params(websocket, messagejson):
    """应用说话人/VAD 动态参数（独立于 audio_fs，配置无 audio_fs 也生效）。"""
    if "spk_threshold" in messagejson:
        try:
            websocket.spk_threshold = float(messagejson["spk_threshold"])
        except (TypeError, ValueError):
            pass
    for _k, _attr, _typ in (
        ("vad_max_end_silence_ms", "vad_max_end_silence_ms", int),
        ("vad_max_segment_sec", "vad_max_segment_sec", int),
        ("spk_min_seg_sec", "spk_min_seg_sec", float),
        ("max_speakers", "max_speakers", int),
    ):
        if _k in messagejson:
            try:
                setattr(websocket, _attr, _typ(messagejson[_k]))
            except (TypeError, ValueError):
                pass
'''
assert SAFE_INT_END in src, "safe_int anchor not found"
src = src.replace(SAFE_INT_END, HELPER, 1)
print("[1/3] global helper inserted")

# 2) audio_fs 块内的内联解析 → 调用
INLINE = '''                    # 动态聚类阈值（前端可调，做说话人分离效果实验用）
                    if "spk_threshold" in messagejson:
                        try:
                            websocket.spk_threshold = float(messagejson["spk_threshold"])
                        except (TypeError, ValueError):
                            pass
                    for _k, _attr, _typ in (
                        ("vad_max_end_silence_ms", "vad_max_end_silence_ms", int),
                        ("vad_max_segment_sec", "vad_max_segment_sec", int),
                        ("spk_min_seg_sec", "spk_min_seg_sec", float),
                        ("max_speakers", "max_speakers", int),
                    ):
                        if _k in messagejson:
                            try:
                                setattr(websocket, _attr, _typ(messagejson[_k]))
                            except (TypeError, ValueError):
                                pass'''
if INLINE in src:
    src = src.replace(INLINE, "                    _apply_tuning_params(websocket, messagejson)", 1)
    print("[2/3] audio_fs inline replaced with call")
else:
    # 兼容：audio_fs 块内可能只剩 spk_threshold（老版本无 max_speakers tuple）
    OLD_INLINE = '''                    # 动态聚类阈值（前端可调，做说话人分离效果实验用）
                    if "spk_threshold" in messagejson:
                        try:
                            websocket.spk_threshold = float(messagejson["spk_threshold"])
                        except (TypeError, ValueError):
                            pass'''
    if OLD_INLINE in src:
        src = src.replace(OLD_INLINE, "                    _apply_tuning_params(websocket, messagejson)", 1)
        print("[2/3] old audio_fs inline replaced")
    else:
        print("[2/3] inline not found — maybe structure differs, check manually")
        raise SystemExit(1)

# 3) 门控外统一调用（end_of_input 之前）
CALL_ANCHOR = '                if end_of_input:\n                    await finish_input'
assert CALL_ANCHOR in src, "end_of_input anchor not found"
src = src.replace(
    CALL_ANCHOR,
    "                _apply_tuning_params(websocket, messagejson)\n\n" + CALL_ANCHOR,
    1,
)
print("[3/3] call added outside audio_fs gate")

SERVER.write_text(src, encoding="utf-8")
print("patch done:", SERVER)
