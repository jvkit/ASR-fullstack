"""一次性补丁 v2：/meeting_chunk 升级为子段级 ASR + SeACo 热词版（在 GPU 服务器上执行）。

变更点（相对 v1）：
1. ASR 模型换成 SeACo Paraformer（paraformer-large 的热词增强版，快照已在
   modelscope 缓存里，generate 支持 hotwords 参数）
2. ASR 从"整句"改为"逐子段"：v1 是整句 ASR 后把整句文字复制到每个子段，
   长句换人时同一句话的文字会在两个子段上重复出现；v2 每个子段切自己的
   音频单独 ASR，文字与子段时间严格一一对应
3. 新增 Form 参数 hotwords（空格分隔；FunASR 按空白切分，逗号会被当成一个词）

会末重标注与会议中批次从此共用这个端点，返回格式不变：
    {"segs": [{"start": s, "end": s, "speaker": name, "text": str}, ...]}
"""
from pathlib import Path

P = Path('/home/bo1/jvkit/asr/diarize_svc/service.py')
s = P.read_text()

start_anchor = '# ============ /meeting_chunk：非实时一体化管线（VAD+ASR+区分人） ============'
end_anchor = '@app.get("/health")'
i0 = s.index(start_anchor)
i1 = s.index(end_anchor)
assert i0 < i1

new_block = '''# ============ /meeting_chunk：非实时一体化管线（VAD+区分人+逐子段ASR+热词） ============
_ASR = None


def _asr():
    global _ASR
    if _ASR is None:
        from funasr import AutoModel
        # SeACo Paraformer：paraformer-large 的热词增强版（generate 支持 hotwords）
        _ASR = AutoModel(
            model="/home/bo1/.cache/modelscope/models/iic--speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master",
            device="cpu", disable_pbar=True, disable_log=True, disable_update=True,
        )
    return _ASR


@app.post("/meeting_chunk")
async def meeting_chunk(file: UploadFile = File(...), name_threshold: float = Form(default=0.30),
                        max_end_silence_ms: int = Form(default=250), hotwords: str = Form(default="")):
    """wav -> [{start,end,speaker,text}]，内部完整走好管线（含归一化）。

    v2：ASR 逐子段执行（v1 整句 ASR + 子段复制整句文字，长句换人时整句文字
    会重复出现在两个子段上）；hotwords 空格分隔（FunASR 按空白切分）。
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
        # 区分人（diarize_sent 逻辑内联：复用其滑窗子段切分）
        r2 = await diarize_sent(
            file=UploadFile(file=io.BytesIO(data), filename="chunk.wav"),
            sents=json.dumps(sents), name_threshold=name_threshold,
            enroll_dir="", sub_min=2.5, sub_win=0.6, sub_shift=0.3,
            sub_min_dur=0.5, merge_margin=0.10, change_margin=0.04,
        )
        # 逐子段 ASR：文字与子段时间严格对应（v1 的整句复制会有重复文字）
        asr = _asr()
        segs = []
        for seg in r2["segs"]:
            a, b = float(seg[0]), float(seg[1])
            clip = audio[int(a * sr):int(b * sr)]
            if len(clip) < sr * 0.1:
                txt = ""
            elif hotwords:
                rr = asr.generate(input=clip, fs=sr, hotwords=hotwords)
                txt = (rr[0]["text"] if isinstance(rr, list) else rr["text"]).strip()
            else:
                rr = asr.generate(input=clip, fs=sr)
                txt = (rr[0]["text"] if isinstance(rr, list) else rr["text"]).strip()
            segs.append({"start": round(a, 3), "end": round(b, 3),
                         "speaker": seg[3], "text": txt})
        return {"segs": segs}
    finally:
        Path(tmp).unlink(missing_ok=True)


'''

s = s[:i0] + new_block + s[i1:]
P.write_text(s)
print('patched OK: /meeting_chunk v2 (per-seg ASR + SeACo hotwords)')
