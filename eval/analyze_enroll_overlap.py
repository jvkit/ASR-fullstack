#!/usr/bin/env python3
"""验证：各说话人的注册段（GT, t>=180s, 最长3段）是否与其它说话人的语音重叠。
重叠部分会污染注册声纹——双向互污正好解释双向互认。"""
from collections import defaultdict

GT = "aishell4/test/TextGrid/M_R003S01C01.rttm"
AFTER, MAX_CLIPS, MIN_DUR = 180.0, 3, 2.0

gt = defaultdict(list)
for line in open(GT):
    p = line.split()
    if p[0] == "SPEAKER":
        gt[p[7]].append((float(p[3]), float(p[3]) + float(p[4])))

for spk in sorted(gt):
    cands = sorted([(s, e) for s, e in gt[spk] if s >= AFTER and e - s >= MIN_DUR],
                   key=lambda x: -(x[1] - x[0]))[:MAX_CLIPS]
    tot = sum(e2 - s2 for s2, e2 in cands)
    ov = 0.0
    detail = defaultdict(float)
    for s, e in cands:
        for s2, e2 in gt[spk]:
            pass  # 同说话人忽略
        for other, segs in gt.items():
            if other == spk:
                continue
            for s2, e2 in segs:
                o = min(e, e2) - max(s, s2)
                if o > 0:
                    ov += o
                    detail[other] += o
    parts = ", ".join(f"{k}:{v:.1f}s" for k, v in sorted(detail.items(), key=lambda x: -x[1]) if v > 0.3)
    print(f"{spk}: 注册段共 {tot:5.1f}s，其中与他人重叠 {ov:5.1f}s ({ov/tot*100:4.1f}%)  [{parts}]")
