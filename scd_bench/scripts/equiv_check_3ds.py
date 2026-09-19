"""等价性验收：项目 venv + server/app/core/diarize3ds.py（A 阶段 wrapper）跑两个标定件，
与 scd_bench venv 成绩对比（rotate_m30 7.66% / M5min 4.77%，collar 0.25）。
差 >1pp = 环境并入有问题。运行（项目根）：
  cd server && ../.venv/bin/python ../scd_bench/scripts/equiv_check_3ds.py
"""
import subprocess
import sys
import wave
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
SCD = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))

from app.core.preprocess import denoise_pcm  # noqa: E402  （运行期导入，不改代码）
from app.core.diarize3ds import diarize  # noqa: E402

OUT = SCD / "out" / "equiv"
OUT.mkdir(parents=True, exist_ok=True)

JOBS = [
    {
        "name": "rotate_m30_s5",
        "wav": SERVER.parent / "eval" / "shuffled" / "rotate_m30_s5.wav",
        "gt": SERVER.parent / "eval" / "shuffled" / "rotate_m30_s5.rttm",
        "target": {0.25: 7.66, 0.0: 10.03},
    },
    {
        "name": "M_R003S01C01_0m",
        "wav": SERVER.parent / "eval" / "aishell4" / "chunks" / "M_R003S01C01_0m.wav",
        "gt": SCD / "out" / "bench" / "gt_M_R003S01C01_0m.rttm",
        "target": {0.25: 4.77, 0.0: 6.33},
    },
]

for job in JOBS:
    with wave.open(str(job["wav"]), "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        pcm = w.readframes(w.getnframes())
    segs = diarize(denoise_pcm(pcm))
    rttm = OUT / f"{job['name']}_equiv.rttm"
    with open(rttm, "w") as f:
        for s in segs:
            f.write(f"SPEAKER {job['name']} 1 {s['start']:.3f} "
                    f"{s['end'] - s['start']:.3f} <NA> <NA> {s['cluster_id']} <NA> <NA>\n")
    print(f"[{job['name']}] {len(segs)} 段 -> {rttm}")
    for collar in (0.25, 0.0):
        r = subprocess.run(
            [sys.executable, str(SCD / "scripts" / "der_eval.py"), str(rttm),
             str(job["gt"]), "--collar", str(collar)],
            capture_output=True, text=True)
        import json as _json
        d = _json.loads(r.stdout)
        diff = d["DER"] - job["target"][collar]
        flag = "OK" if abs(diff) <= 1.0 else "★偏差超标"
        print(f"  collar={collar}: DER {d['DER']}% miss {d['miss']}% conf {d['confusion']}% "
              f"标签{len(d['sys_labels'])} | 目标 {job['target']}% 差 {diff:+.2f}pp {flag}")
print("EQUIV_CHECK_DONE")
