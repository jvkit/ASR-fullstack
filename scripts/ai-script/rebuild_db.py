"""重建 speaker_db.json：对指定 enroll 目录算 cam++ embedding 写成 db。

用法: venv/bin/python rebuild_db.py <enroll_dir> <db_path>
"""
import json
import sys
from pathlib import Path

import numpy as np
from funasr import AutoModel

enroll_dir, db_path = sys.argv[1], sys.argv[2]
sv = AutoModel(
    model="/home/bo1/.cache/modelscope/models/iic--speech_campplus_sv_zh-cn_16k-common/snapshots/master",
    device="cpu", disable_pbar=True, disable_log=True)
db = {}
for p in sorted(Path(enroll_dir).glob("*.pcm")):
    name = p.stem
    if "_" in name:
        name = name.rsplit("_", 1)[0]
    pcm = np.frombuffer(p.read_bytes(), dtype=np.int16).astype(np.float32) / 32768.0
    e = sv.generate(input=pcm, extract_embedding=True)[0]["spk_embedding"]
    e = np.asarray(e, dtype=np.float32).ravel()
    e = e / (np.linalg.norm(e) + 1e-9)
    db[name] = e.tolist()
json.dump(db, open(db_path, "w"), ensure_ascii=False)
print("db rebuilt:", sorted(db.keys()), flush=True)
