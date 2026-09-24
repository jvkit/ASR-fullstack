"""会中3D-Speaker批次模拟测试 —— 验证"换成3D+有注册声纹，实时跑也能认对"。

流程：
1. 从会议后半段 GT 标签抽段注册声纹（P1 批量调用；已存在则跳过）
2. 把全会音频切固定窗口，逐批本地 diarize3ds + GPU /identify_segments
3. 聚合所有批次结果导出 RTTM，与 GT RTTM 算 DER

用法（meeting-asr 根目录）:
    .venv/bin/python server/scripts/batch_3ds_test.py <wav> <rttm> [--batch-sec 30]
        [--enroll-after 180] [--enroll-per-spk 8] [--enroll-max-per-spk 3]
        [--skip-first-batch]

依赖：GPU 服务 10096 在线；声纹库接口 8010 可用；本地 .venv 含 3D-Speaker。
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # server/

GPU = os.environ.get("DIARIZE_HTTP_URL", "http://172.16.16.120:10096")
API = "http://127.0.0.1:8010"


# ── 声纹注册（复用 enroll_from_rttm 的片段抽取逻辑）──────────────

def load_rttm(path: Path) -> dict[str, list[tuple[float, float]]]:
    segs: dict[str, list[tuple[float, float]]] = {}
    for line in path.read_text().splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            segs.setdefault(p[7], []).append((float(p[3]), float(p[4])))
    return segs


def pick_clip(wav_path: str, start: float, dur: float,
              min_len: float = 2, max_len: float = 8) -> bytes | None:
    take = min(dur - 0.2, max_len)
    if take < min_len:
        return None
    s = start + (dur - take) / 2
    with wave.open(wav_path, "rb") as w:
        w.setpos(int(s * 16000))
        return w.readframes(int(take * 16000))


def enroll_speakers(wav_path: str, rttm_path: Path,
                    enroll_after: float, max_per_spk: int, max_clips: int) -> dict[str, str]:
    """从 GT 标签 enroll_after 秒后的段中每人取最长段注册。

    返回 {gt_label: enrolled_name}。
    """
    gt = load_rttm(rttm_path)
    mapping: dict[str, str] = {}
    SIL = b"\x00\x00" * 16000
    for spk in sorted(gt):
        name = spk.replace("_", "-")
        cands = sorted(
            [(s, d) for s, d in gt[spk] if s >= enroll_after and d >= 2],
            key=lambda x: -x[1],
        )[:max_clips]
        clips = [c for c in (pick_clip(wav_path, s, d, 2, max_per_spk) for s, d in cands) if c]
        if len(clips) < max_clips:
            print(f"  ⚠ {spk} 可用段不足 {max_clips} 段，跳过", flush=True)
            continue
        r = httpx.post(f"{API}/api/speakers/enroll",
                       params={"name": name},
                       content=SIL.join(clips), timeout=180)
        if r.status_code == 200 and r.json().get("ok"):
            mapping[spk] = name
            info = r.json()
            print(f"  ✓ {spk} → {name}（{info.get('effective_sec')}s / {info.get('segments')} 段）", flush=True)
        else:
            print(f"  ✗ {spk} 注册失败 {r.status_code}", flush=True)
    print(f"注册完成: {len(mapping)} 人", flush=True)
    return mapping


# ── 批次处理 ──────────────────────────────────────────────────

def pcm_to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


def run_batch(pcm: bytes, batch_start_s: float, timeout: int = 300) -> list[dict]:
    """本地 diarize3ds + GPU /identify_segments，返回 [{start,end,speaker,text}]（秒）。"""
    # 0) 降噪（生产同路径：preprocess.denoise_pcm，3ds 对噪声敏感——scd_bench
    #    M 全曲 降噪 9.71% vs 生肉 29.24%，不降噪会把 3ds 打回原型）
    from app.core.preprocess import denoise_pcm
    pcm = denoise_pcm(pcm)

    # 1) 本地 3D-Speaker 分人（同机 CPU），契约=raw int16 PCM
    t0 = time.time()
    segs = diarize3ds.diarize(pcm)
    dt_dia = time.time() - t0

    # 2) GPU 认人 + 转写（HTTP 需要 WAV 容器）
    t0 = time.time()
    r = httpx.post(
        f"{GPU}/identify_segments",
        files={"file": ("batch.wav", pcm_to_wav(pcm), "audio/wav")},
        data={
            "segments": json.dumps(
                [[float(s["start"]), float(s["end"]), int(s["cluster_id"])] for s in segs]
            ),
            "name_threshold": "0.40",
            "hw_weight": "0.1",
            "punc": "true",
            "itn": "true",
        },
        timeout=timeout,
    )
    dt_gpu = time.time() - t0

    if r.status_code != 200:
        print(f"  GPU identify 失败 {r.status_code} @{batch_start_s:.0f}s", flush=True)
        return []

    result = r.json().get("segs", [])
    # 把说话人N 改成 "未识别"（与生产 display_speaker 一致）
    import re
    for item in result:
        if re.match(r"说话人\d+", item.get("speaker", "")):
            item["speaker"] = "未识别"
        # 时间戳转为全局偏移
        item["start"] = float(item["start"]) + batch_start_s
        item["end"] = float(item["end"]) + batch_start_s

    print(f"  batch @{batch_start_s:.0f}s: 3ds{dt_dia:.1f}s gpu{dt_gpu:.1f}s "
          f"{len(segs)}→{len(result)} 段", flush=True)
    return result


# ── 聚合 + 评分 ────────────────────────────────────────────────

def write_rttm(results: list[dict], rttm_path: Path, uri: str = "batch_3ds_test"):
    lines = []
    for item in results:
        lines.append(
            f"SPEAKER {uri} 1 {item['start']:.3f} {item['end'] - item['start']:.3f} "
            f"<NA> <NA> {item['speaker']} <NA> <NA>"
        )
    rttm_path.write_text("\n".join(lines) + "\n")


# ── main ──────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("rttm")
    ap.add_argument("--batch-sec", type=float, default=30)
    ap.add_argument("--enroll-after", type=float, default=180,
                    help="从此秒之后抽取声纹（后半段）")
    ap.add_argument("--enroll-max-per-spk", type=int, default=8,
                    help="每人注册最长片段上限秒")
    ap.add_argument("--enroll-max-clips", type=int, default=3,
                    help="每人最多拼接段数")
    ap.add_argument("--skip-first-batch", action="store_true",
                    help="跳过第一批（通常含自我介绍噪声）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划，不注册/不调用 GPU")
    args = ap.parse_args()

    wavp = Path(args.wav)
    rttm_path = Path(args.rttm)
    stem = rttm_path.stem
    out_root = Path("eval") / f"batch_3ds_{stem}"

    # 获取总时长
    with wave.open(str(wavp), "rb") as w:
        total_sec = w.getnframes() / w.getframerate()
    batches = []
    t = 0
    while t < total_sec:
        if args.skip_first_batch and t == 0:
            t += args.batch_sec
            continue
        batches.append((t, min(t + args.batch_sec, total_sec)))
        t += args.batch_sec
    n = len(batches)
    print(f"音频 {total_sec:.0f}s → {n} 批（{args.batch_sec}s/批）", flush=True)

    if args.dry_run:
        print("dry-run，退出", flush=True)
        return

    # 1) 注册声纹
    print(f"\n── 注册声纹（after={args.enroll_after}s）──", flush=True)
    mapping = enroll_speakers(str(wavp), rttm_path, args.enroll_after,
                              args.enroll_max_per_spk, args.enroll_max_clips)
    if not mapping:
        print("无人注册成功，退出", flush=True)
        return

    # 2) 逐批处理（<5s 尾巴跳过：VAD 空段会让 3ds 崩 max() empty；
    #    单批失败不中断；每批增量落盘，崩了不丢已跑结果）
    print(f"\n── 批次处理 ──", flush=True)
    out_root.mkdir(parents=True, exist_ok=True)
    out_rttm = out_root / f"batch_3ds_{stem}.rttm"
    all_results = []
    with wave.open(str(wavp), "rb") as w:
        for i, (b0, b1) in enumerate(batches):
            if b1 - b0 < 5:
                print(f"  [skip] batch @{b0:.0f}s 仅 {b1 - b0:.1f}s，跳过", flush=True)
                continue
            w.setpos(int(b0 * 16000))
            pcm = w.readframes(int((b1 - b0) * 16000))
            try:
                segs = run_batch(pcm, b0)
            except Exception as e:
                print(f"  [fail] batch @{b0:.0f}s: {type(e).__name__} {e}", flush=True)
                continue
            all_results.extend(segs)
            write_rttm(all_results, out_rttm)  # 增量落盘

    # 3) 导出统计
    print(f"\n导出 {len(all_results)} 段 → {out_rttm}", flush=True)

    # 4) DER 评分
    der_script = ROOT.parent / "scd_bench" / "scripts" / "der_eval.py"
    if not der_script.exists():
        print("未找到 der_eval.py 跳过评分", flush=True)
        return
    import subprocess
    cmd = [
        sys.executable, str(der_script),
        str(out_rttm.resolve()), str(rttm_path.resolve()),
        "--collar", "0.25",
        "--json-out", str((out_root / "batch_3ds_der.json").resolve()),
    ]
    subprocess.run(cmd, cwd=str(ROOT.parent / "scd_bench"))
    der_json = out_root / "batch_3ds_der.json"
    if der_json.exists():
        d = json.loads(der_json.read_text())
        print(f"\nDER (collar=0.25): {d['DER']:.2f}%  "
              f"miss={d['miss']:.2f} conf={d['confusion']:.2f} FA={d['FA']:.2f}  "
              f"标签={d.get('sys_labels')} 映射={d.get('mapping')}", flush=True)


if __name__ == "__main__":
    # 懒加载 3D-Speaker（sys.path 已含 server/，用 app.* 导入与生产一致）
    from app.core import diarize3ds
    main()