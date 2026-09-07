"""fix：把 _apply_tuning_params 移到模块顶层，删除循环内嵌套定义与冗余调用。

现状（上次 patch 产物）：
- helper 定义被插在消息循环体内（每次迭代重复定义、缩进乱）
- audio_fs 块内有一处冗余调用

修复：
1. 移除循环体内的嵌套 def
2. 在模块顶层（_safe_int 之后）插入全局 helper
3. 移除 audio_fs 块内的冗余调用，只保留门控外一处
"""
from pathlib import Path

SERVER = Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py")
src = SERVER.read_text(encoding="utf-8")

# 1) 移除循环体内的嵌套 def（从 '                def _apply_tuning_params' 到 'if "audio_fs"' 前的空行）
NESTED_START = '                def _apply_tuning_params(websocket, messagejson):'
NESTED_END = 'if "audio_fs" in messagejson:'
i = src.find(NESTED_START)
if i != -1:
    j = src.find(NESTED_END, i)
    assert j != -1, "nested end not found"
    src = src[:i] + src[j:]
    print("[1/3] nested def removed")
else:
    print("[1/3] nested def not found (already clean?)")

# 2) 模块顶层插入全局 helper（_safe_int 定义之后）
SAFE_INT_ANCHOR = 'def _safe_int(v, default):'
HELPER = '''def _apply_tuning_params(websocket, messagejson):
    """应用说话人/VAD 动态参数（独立于 audio_fs，避免配置无 audio_fs 时不生效）。"""
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
if "def _apply_tuning_params(websocket, messagejson):\n    \"\"\"应用说话人" not in src:
    # 只在顶层出现（非缩进）且不在循环体时插入。检查是否已存在顶层定义。
    if "def _apply_tuning_params(websocket, messagejson):\n    \"\"\"应用说话人" not in src:
        assert SAFE_INT_ANCHOR in src, "safe_int anchor not found"
        src = src.replace(SAFE_INT_ANCHOR, SAFE_INT_ANCHOR + "\n" + HELPER, 1)
        print("[2/3] global helper inserted")
    else:
        print("[2/3] global helper exists")
else:
    print("[2/3] skip")

# 3) 移除 audio_fs 块内的冗余调用
REDUNDANT = '''                if "audio_fs" in messagejson:
                    websocket.audio_fs = _safe_int(messagejson["audio_fs"], 16000)
                    _apply_tuning_params(websocket, messagejson)'''
REDUNDANT_NEW = '''                if "audio_fs" in messagejson:
                    websocket.audio_fs = _safe_int(messagejson["audio_fs"], 16000)'''
if REDUNDANT in src:
    src = src.replace(REDUNDANT, REDUNDANT_NEW, 1)
    print("[3/3] redundant inline call removed")
else:
    print("[3/3] redundant inline call not found")

SERVER.write_text(src, encoding="utf-8")
print("fix done:", SERVER)
