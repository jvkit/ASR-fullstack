"""补丁：VAD 激进切分参数 + 碎段跳过声纹，全部按连接可配。

1. ws 连接初始化新增三个属性（默认值可被客户端配置消息覆盖）：
   - vad_max_end_silence_ms：静音多久判"说完"（默认 350，原模型 800）
   - vad_max_segment_sec：单段最长秒数，超时强制切（默认 8，原模型 60）
   - spk_min_seg_sec：短于此长度的段不做声纹（默认 1.0，碎段 embedding 不稳）
2. async_vad 调用时透传 max_end_silence_time，并同步 vad_opts.max_single_segment_time
3. async_asr 里短段跳过 SV，spk 输出空
"""
import subprocess

SRC = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py"

# 1) 连接初始化默认值（紧跟 spk_threshold 那行）
ANCHOR_INIT = '    websocket.spk_threshold = 0.55  # 默认聚类阈值，可被客户端配置覆盖'
NEW_INIT = '''    websocket.spk_threshold = 0.55  # 默认聚类阈值，可被客户端配置覆盖
    # 激进切分参数（可被客户端配置消息覆盖）：
    # 静音多久判"说完"（ms）——原模型 800，降到 350 后正常换气即可断句
    websocket.vad_max_end_silence_ms = 350
    # 单段最长秒数——原模型 60s，超长段必然混进多个说话人，强制切断
    websocket.vad_max_segment_sec = 8
    # 短于此秒数的段不做声纹（碎段 embedding 不稳，标"发言人"即可）
    websocket.spk_min_seg_sec = 1.0'''

# 2) 客户端配置解析（紧跟 spk_threshold 解析之后）
ANCHOR_CFG = '''                    if "spk_threshold" in messagejson:
                        try:
                            websocket.spk_threshold = float(messagejson["spk_threshold"])
                        except (TypeError, ValueError):
                            pass'''
NEW_CFG = '''                    if "spk_threshold" in messagejson:
                        try:
                            websocket.spk_threshold = float(messagejson["spk_threshold"])
                        except (TypeError, ValueError):
                            pass
                    for _k, _attr, _typ in (
                        ("vad_max_end_silence_ms", "vad_max_end_silence_ms", int),
                        ("vad_max_segment_sec", "vad_max_segment_sec", int),
                        ("spk_min_seg_sec", "spk_min_seg_sec", float),
                    ):
                        if _k in messagejson:
                            try:
                                setattr(websocket, _attr, _typ(messagejson[_k]))
                            except (TypeError, ValueError):
                                pass'''

# 3) async_vad 透传（替换 generate 调用行）
ANCHOR_VAD = '    out = await run_blocking(_generate_sync, model_vad, audio_in, websocket.status_dict_vad, sem=SEM_VAD)'
NEW_VAD = '''    # 激进切分参数：max_end_silence_time 支持 kwargs 覆盖；
    # max_single_segment_time 只能从 vad_opts 改（全局对象，同值并发安全）
    # 注意：AutoModel 包装层没有 vad_opts，必须取内层 model
    model_vad.model.vad_opts.max_single_segment_time = int(getattr(websocket, "vad_max_segment_sec", 8)) * 1000
    _vad_kwargs = dict(websocket.status_dict_vad)
    _vad_kwargs["max_end_silence_time"] = int(getattr(websocket, "vad_max_end_silence_ms", 350))
    out = await run_blocking(_generate_sync, model_vad, audio_in, _vad_kwargs, sem=SEM_VAD)'''

# 4) async_asr 碎段跳过 SV（替换 SV 调用块）
ANCHOR_SV = '''    spk_name = "unknown"
    best_score = 0.0
    try:
        spk_name, best_score = await run_blocking(
            _sv_and_match_sync,
            audio_in,
            int(args.speaker_db_reload_sec),
            sem=SEM_SV,
            ws_conn=websocket,
        )
    except Exception as e:
        print(f"声纹识别失败: {e}")'''
NEW_SV = '''    spk_name = "unknown"
    best_score = 0.0
    _seg_sec = len(audio_in) / 32000.0
    if _seg_sec < float(getattr(websocket, "spk_min_seg_sec", 1.0)):
        # 碎段不做声纹：embedding 不稳，匹配/聚类都不可信，留空由前端显示"发言人"
        pass
    else:
        try:
            spk_name, best_score = await run_blocking(
                _sv_and_match_sync,
                audio_in,
                int(args.speaker_db_reload_sec),
                sem=SEM_SV,
                ws_conn=websocket,
            )
        except Exception as e:
            print(f"声纹识别失败: {e}")'''


def main() -> None:
    src = subprocess.run(["cat", SRC], capture_output=True, text=True).stdout
    if "vad_max_end_silence_ms" in src:
        print("already patched")
        return
    for anchor, new, name in [
        (ANCHOR_INIT, NEW_INIT, "init defaults"),
        (ANCHOR_CFG, NEW_CFG, "config parse"),
        (ANCHOR_VAD, NEW_VAD, "vad kwargs"),
        (ANCHOR_SV, NEW_SV, "skip short seg SV"),
    ]:
        if anchor not in src:
            print(f"ERROR: anchor not found: {name}")
            raise SystemExit(1)
        src = src.replace(anchor, new, 1)
    subprocess.run(["bash", "-c", f"cat > {SRC}"], input=src, text=True, check=True)
    r = subprocess.run(
        ["/home/bo1/jvkit/asr/venv/bin/python", "-m", "py_compile", SRC],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("COMPILE FAILED:", r.stderr)
        raise SystemExit(1)
    print("patched OK")


if __name__ == "__main__":
    main()
