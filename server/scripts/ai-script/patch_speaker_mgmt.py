"""一次性补丁：diarize_svc/service.py 增加声纹库管理端点（唯一权威库改造）。
在 GPU 上执行：python patch_speaker_mgmt.py（先 git commit 基线，改完再 commit）。

2026-09-15 注：本脚本为初始版本，应用后又经 fix_spk_names（人名解析正则化+
音量地板放宽）与 fix_consistency（一致性双规则：gap 截断 + 绝对地板）两次修订，
最终状态以 GPU diarize_svc git 提交为准。
"""
import pathlib

SP = pathlib.Path.home() / "jvkit/asr/diarize_svc/service.py"
src = SP.read_text(encoding="utf-8")

# 1) _load_db_multi：每段归一化到 RMS 0.1（与 meeting_chunk 处理域对齐，
#    消除注册音量差异带来的域差；2026-09-15 起新旧注册统一在此归一化）
OLD = """                pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
                if len(pcm) < 16000 * 1.5:  # <1.5s 跳过
                    continue
"""
NEW = """                pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
                if len(pcm) < 16000 * 1.5:  # <1.5s 跳过
                    continue
                # RMS 归一化到 0.1：与会议管线 meeting_chunk 处理域对齐，
                # 消除注册音量差异带来的域差（2026-09-15 起统一）
                _r = float(np.sqrt(np.mean(pcm ** 2)))
                if _r > 1e-6:
                    pcm = np.clip(pcm * (0.1 / _r), -1.0, 1.0)
"""
assert OLD in src, "db_multi anchor not found"
src = src.replace(OLD, NEW, 1)

# 2) 追加声纹库管理端点
APPEND = '''

# ============ 声纹库管理（2026-09-15：唯一权威库 = speaker_enrolls/*.pcm） ============
# 背景：旧架构声纹有两套互不通气的存储——FunASR speaker_db.json（仅列表展示）
# 与 speaker_enrolls/*.pcm（ER 认人实际读取）。DELETE 只删前者导致"假删除"
# （删了照样被认出），两库成员互相不一致（幽灵名/隐形人）。
# 现收敛为单库：注册/列表/删除全部走本服务，db.json 废弃不再读写。
import time as _time
from fastapi import Request as _Request, Query as _Query


def _enroll_names() -> list[str]:
    d = Path(_SPEAKER_ENROLL_DIR)
    if not d.is_dir():
        return []
    return sorted({(p.stem.rsplit("_", 1)[0] if "_" in p.stem else p.stem)
                   for p in d.glob("*.pcm")})


def _drop_enroll_files(nm: str) -> list[str]:
    """删除某人的全部注册段文件并失效缓存，返回删掉的文件名。"""
    d = Path(_SPEAKER_ENROLL_DIR)
    removed = []
    if d.is_dir():
        for p in d.glob("*.pcm"):
            stem = p.stem
            pname = stem.rsplit("_", 1)[0] if "_" in stem else stem
            if pname == nm:
                p.unlink(missing_ok=True)
                removed.append(p.name)
    _DB_MULTI_CACHE.pop(str(Path(_SPEAKER_ENROLL_DIR)), None)
    _DB_MULTI_MTIME.pop(str(Path(_SPEAKER_ENROLL_DIR)), None)
    return removed


@app.get("/speaker_names")
async def speaker_names():
    """已注册声纹列表（pcm 目录前缀去重，唯一权威）。"""
    return {"speakers": _enroll_names()}


@app.post("/speaker_delete")
async def speaker_delete(name: str = Form(...)):
    """删除声纹：删掉此人全部 pcm 注册段并失效缓存（真删除，立即生效）。"""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "error": "name required"}
    removed = _drop_enroll_files(nm)
    return {"ok": True, "removed": removed, "speakers": _enroll_names()}


@app.post("/speaker_enroll")
async def speaker_enroll(request: _Request, name: str = _Query(..., min_length=1, max_length=32),
                         min_total_sec: float = Form(default=5.0),
                         min_seg_sec: float = Form(default=2.0),
                         min_segments: int = Form(default=3),
                         min_effective_sec: float = Form(default=10.0),
                         max_keep: int = Form(default=5),
                         consistency: float = Form(default=0.50),
                         vad_end_silence_ms: int = Form(default=800)):
    """智能注册：raw PCM(int16/16k/mono) -> fsmn VAD 切段 -> 逐段质量过滤
    -> ER 一致性选优 -> 留 3~5 段写 {name}_{ts}_{i}.pcm（多段质心），旧段整批替换。

    录入是全场识别的参照物来源，标准与识别相反（宁缺勿滥）：太短/太轻/爆音/
    与其它段声纹不一致（咳嗽、旁人插话、突发环境声）的段一律丢弃。
    不合格不落库；返回 kept/rejected 明细报告。
    """
    import re as _re
    nm = name.strip()
    if not _re.fullmatch(r"[\\w\\u4e00-\\u9fff\\-]+", nm):
        return {"ok": False, "error": "姓名只允许中文/英文/数字/下划线/横线"}
    raw = await request.body()
    if len(raw) % 2:
        return {"ok": False, "error": "音频数据格式错误（奇数字节）"}
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    total = len(audio) / 16000.0
    if total < min_total_sec:
        return {"ok": False, "error": f"录音太短（{total:.1f}s < {min_total_sec:.0f}s），请自然说 20~60 秒"}
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 10 ** (-45 / 20):
        return {"ok": False, "error": "整体音量过低，几乎全是环境噪音，请靠近麦克风"}
    # 归一化到 RMS 0.1：与会议管线 meeting_chunk 处理域一致
    audio = np.clip(audio * (0.1 / rms), -1.0, 1.0)
    r = _vad().generate(input=audio.copy(), fs=16000, max_end_silence_time=vad_end_silence_ms)
    vad = r[0]["value"] if isinstance(r, list) else r["value"]
    segs = [[a / 1000.0, b / 1000.0] for a, b in vad]
    if not segs:
        return {"ok": False, "error": "未检测到语音，请对着麦克风自然说话"}
    # 逐段质量过滤
    kept, rejected = [], []
    ere = _ere()
    for i, (a, b) in enumerate(segs):
        dur = b - a
        if dur < min_seg_sec:
            rejected.append({"i": i, "dur": round(dur, 2), "reason": f"太短(<{min_seg_sec}s)"})
            continue
        clip = audio[int(a * 16000):int(b * 16000)]
        sr_ = float(np.sqrt(np.mean(clip ** 2)))
        if sr_ < 10 ** (-42 / 20):
            rejected.append({"i": i, "dur": round(dur, 2), "reason": "音量过低(疑似环境音)"})
            continue
        if float(np.mean(np.abs(clip) > 0.999)) > 0.01:
            rejected.append({"i": i, "dur": round(dur, 2), "reason": "爆音(麦克风太近/增益过高)"})
            continue
        e = np.asarray(ere.generate(input=clip, fs=16000, extract_embedding=True)[0]["spk_embedding"],
                       dtype=np.float32).ravel()
        kept.append({"i": i, "start": round(a, 2), "end": round(b, 2), "dur": round(dur, 2),
                     "emb": e / (np.linalg.norm(e) + 1e-9)})
    if not kept:
        return {"ok": False, "error": "没有合格的语音段，请重录", "rejected": rejected}
    # 一致性选优：与质心余弦过低 -> 非本人声，迭代剔除（最少保留 1 段，由门槛兜住）
    while len(kept) >= 2:
        c = np.mean([k["emb"] for k in kept], axis=0)
        c /= np.linalg.norm(c) + 1e-9
        sims = [float(k["emb"] @ c) for k in kept]
        worst = min(range(len(kept)), key=lambda j: sims[j])
        if sims[worst] >= consistency:
            break
        k = kept.pop(worst)
        rejected.append({"i": k["i"], "dur": k["dur"],
                         "reason": f"声纹不一致({sims[worst]:.2f}<{consistency:.2f}，疑似他人/咳嗽/环境声)"})
    # 按时长优先取 top max_keep（长段 embedding 更稳）
    kept.sort(key=lambda k: -k["dur"])
    kept = kept[:max_keep]
    effective = sum(k["dur"] for k in kept)
    if len(kept) < min_segments or effective < min_effective_sec:
        return {"ok": False,
               "error": f"合格语音不足：{len(kept)}段/{effective:.1f}s"
                        f"（要求>={min_segments}段且有效>={min_effective_sec:.0f}s），请多说一会儿",
               "kept": [{"dur": k["dur"], "start": k["start"], "end": k["end"]} for k in kept],
               "rejected": rejected}
    # 旧段整批替换（同名重注册=换新一批段，防脏段累积）
    _drop_enroll_files(nm)
    d = Path(_SPEAKER_ENROLL_DIR)
    d.mkdir(parents=True, exist_ok=True)
    ts = int(_time.time())
    for j, k in enumerate(kept):
        clip = audio[int(k["start"] * 16000):int(k["end"] * 16000)]
        (d / f"{nm}_{ts}_{j}.pcm").write_bytes(
            (np.clip(clip, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    return {"ok": True, "name": nm, "total_sec": round(total, 1),
            "effective_sec": round(effective, 1), "segments": len(kept),
            "kept": [{"dur": k["dur"], "start": k["start"], "end": k["end"]} for k in kept],
            "rejected": rejected}


@app.post("/speaker_identify")
async def speaker_identify(request: _Request,
                           min_seg_sec: float = Form(default=1.5),
                           match_threshold: float = Form(default=0.40),
                           weak_threshold: float = Form(default=0.30)):
    """录入检测：说几秒 -> 命中声纹库里的谁（返回全候选相似度降序）。

    兼作注册质量自查：注册完立刻测，能稳定认出自己=注册合格。
    verdict: matched(>=match_threshold) / weak(>=weak_threshold) / unknown。
    """
    raw = await request.body()
    if len(raw) % 2:
        return {"ok": False, "error": "音频数据格式错误（奇数字节）"}
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    total = len(audio) / 16000.0
    if total < 1.5:
        return {"ok": False, "error": f"太短（{total:.1f}s），请说 3~10 秒"}
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < 10 ** (-45 / 20):
        return {"ok": False, "error": "几乎没声音，请靠近麦克风再说"}
    audio = np.clip(audio * (0.1 / rms), -1.0, 1.0)
    r = _vad().generate(input=audio.copy(), fs=16000, max_end_silence_time=800)
    vad = r[0]["value"] if isinstance(r, list) else r["value"]
    segs = [[a / 1000.0, b / 1000.0] for a, b in vad if (b - a) / 1000.0 >= min_seg_sec]
    if not segs:
        return {"ok": False, "error": "未检测到足够长的语音，请说 3~10 秒"}
    ere = _ere()
    vecs = []
    for a, b in segs[:8]:  # 最多 8 段，平均抑制单段噪声
        clip = audio[int(a * 16000):int(b * 16000)]
        e = np.asarray(ere.generate(input=clip, fs=16000, extract_embedding=True)[0]["spk_embedding"],
                       dtype=np.float32).ravel()
        vecs.append(e / (np.linalg.norm(e) + 1e-9))
    q = np.mean(vecs, axis=0)
    q /= np.linalg.norm(q) + 1e-9
    refs = _load_db_multi()
    if not refs:
        return {"ok": False, "error": "声纹库为空，请先注册"}
    ranked = sorted(({"name": n, "sim": round(float(q @ c), 3)} for n, c in refs.items()),
                    key=lambda x: -x["sim"])
    best = ranked[0]
    margin = round(best["sim"] - ranked[1]["sim"], 3) if len(ranked) > 1 else None
    verdict = "matched" if best["sim"] >= match_threshold else \
              "weak" if best["sim"] >= weak_threshold else "unknown"
    return {"ok": True, "total_sec": round(total, 1), "segments": len(vecs),
            "best": best["name"], "best_sim": best["sim"], "margin": margin,
            "verdict": verdict, "ranked": ranked}
'''
assert '@app.post("/speaker_enroll")' not in src, "already patched?"
SP.write_text(src + APPEND, encoding="utf-8")
print("patched ok, total lines:", len((src + APPEND).splitlines()))
