"""一次性补丁：给 diarize 服务加 POST /vad 端点。

wav -> fsmn VAD 切句（可调静音阈值），返回 [[start_s, end_s], ...]。
供 AISHELL-4 基准测试用：本地脚本读 flac -> POST /vad 拿句子边界
-> POST /diarize_sent 逐句声纹命名 -> 对 rttm 算 DER。全链路不 scp 大文件。
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

anchor = '''@app.get("/health")
async def health():
    return {"ok": True}'''

addition = '''@app.get("/health")
async def health():
    return {"ok": True}


# ============ /vad：fsmn 切句（基准测试取句子边界用） ============
_VAD = None


def _vad():
    global _VAD
    if _VAD is None:
        from funasr import AutoModel
        _VAD = AutoModel(
            model="/home/bo1/.cache/modelscope/models/damo--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/v2.0.2",
            device="cpu", disable_pbar=True, disable_log=True,
        )
    return _VAD


@app.post("/vad")
async def vad_endpoint(file: UploadFile = File(...), max_end_silence_ms: int = Form(default=250)):
    """wav -> VAD 句子边界 [[start_s, end_s], ...]。

    默认 250ms 静音切句（实测与 FunASR 2pass 边界一致；模型默认 800ms
    会把快交替对话并成整段）。
    """
    import tempfile
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        audio, sr = sf.read(tmp)
        if audio.ndim > 1:
            audio = audio[:, 0]
        r = _vad().generate(input=audio.astype(np.float32).copy(), fs=sr,
                            max_end_silence_time=max_end_silence_ms)
        segs = r[0]["value"] if isinstance(r, list) else r["value"]
        # fsmn vad 输出毫秒
        return {"sents": [[round(a / 1000, 3), round(b / 1000, 3)] for a, b in segs]}
    finally:
        Path(tmp).unlink(missing_ok=True)'''

assert anchor in s, 'health anchor not found'
s = s.replace(anchor, addition)
P.write_text(s)
print('patched OK: /vad added')
