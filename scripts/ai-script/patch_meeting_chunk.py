"""一次性补丁：diarize 服务加 POST /meeting_chunk —— 非实时一体化管线。

wav -> RMS归一化 -> fsmn VAD 切句 -> paraformer 离线逐句转写
   -> ERes2NetV2 diarize_sent(含滑窗子段切分) -> [{start,end,speaker,text}]

server 每 pipeline_tick_sec(默认8s) 把累积新音频 POST 过来，会末全场重跑
也是同一个端点。实时 FunASR 从此不需要。
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

anchor = '''@app.get("/health")'''
assert anchor in s

addition = '''# ============ /meeting_chunk：非实时一体化管线（VAD+ASR+区分人） ============
_ASR = None


def _asr():
    global _ASR
    if _ASR is None:
        from funasr import AutoModel
        _ASR = AutoModel(
            model="/home/bo1/.cache/modelscope/models/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master",
            device="cpu", disable_pbar=True, disable_log=True,
        )
    return _ASR


@app.post("/meeting_chunk")
async def meeting_chunk(file: UploadFile = File(...), name_threshold: float = Form(default=0.30),
                        max_end_silence_ms: int = Form(default=250)):
    """wav -> [{start,end,speaker,text}]，内部完整走好管线（含归一化）。"""
    import tempfile
    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        audio, sr = sf.read(tmp)
        if audio.ndim > 1:
            audio = audio[:, 0]
        audio = audio.astype(np.float32)
        # RMS 归一化（远场轻录音 VAD 漏检修复，已验证）
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms > 1e-6 and abs(rms - 0.1) / 0.1 > 0.3:
            audio = np.clip(audio * (0.1 / rms), -1.0, 1.0)
        # VAD
        r = _vad().generate(input=audio.copy(), fs=sr, max_end_silence_time=max_end_silence_ms)
        vad = r[0]["value"] if isinstance(r, list) else r["value"]
        sents = [[a / 1000, b / 1000] for a, b in vad]
        if not sents:
            return {"segs": []}
        # 逐句 ASR
        asr = _asr()
        texts = []
        for a, b in sents:
            clip = audio[int(a * sr):int(b * sr)]
            if len(clip) < sr * 0.1:
                texts.append("")
                continue
            rr = asr.generate(input=clip, fs=sr)
            texts.append((rr[0]["text"] if isinstance(rr, list) else rr["text"]).strip())
        # 区分人（diarize_sent 逻辑内联：复用其滑窗子段切分）
        r2 = await diarize_sent(
            file=UploadFile(file=io.BytesIO(data), filename="chunk.wav"),
            sents=json.dumps(sents), name_threshold=name_threshold,
            enroll_dir="", sub_min=2.5,
        )
        segs = []
        for seg, txt in zip(r2["segs"], _expand_texts(r2["segs"], sents, texts)):
            segs.append({"start": round(float(seg[0]), 3), "end": round(float(seg[1]), 3),
                         "speaker": seg[3], "text": txt})
        return {"segs": segs}
    finally:
        Path(tmp).unlink(missing_ok=True)


def _expand_texts(segs: list, sents: list, texts: list) -> list:
    """子段文字 = 所属原句的文字（子段切分时按句复制；字级分配由调用方做）。"""
    out = []
    for seg in segs:
        si = int(seg[2]) if len(seg) > 2 else 0
        out.append(texts[si] if 0 <= si < len(texts) else "")
    return out


@app.get("/health")'''

s = s.replace(anchor, addition, 1)
P.write_text(s)
print('patched OK: /meeting_chunk')
