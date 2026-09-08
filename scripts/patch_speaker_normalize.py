#!/usr/bin/env python3
"""声纹归一化修复补丁（可复现记录）。

根因：声纹库 speaker_db.json 入库时未归一化（ERes2NetV2 原始向量，norm≈460），
而匹配用 scipy.spatial.distance.cosine（=1-cos_sim），对未归一化向量严重失真，
导致会议片段与注册声纹相似度被系统性压到 0.1~0.33，匹配永远失败/随机命中
（典型症状：整场会议被全标成同一个注册名，哪怕那人没说话）。

本次修复（在 gpu 端手动执行）：
1. 对 speaker_db.json 现有向量重新 L2 归一化。
2. funasr_wss_server.py 三处补丁：
   - 注册入库前归一化（emb / norm）
   - _sv_and_match_sync 匹配前对 query embedding 和 ref embedding 归一化
   - 匿名聚类质心滑动平均后重新归一化（c / norm）
3. diarize_svc/service.py 两处补丁：
   - /diarize_labeled 投票加 NAME_MATCH_THRESHOLD=0.5（低于阈值匿名，防全员同名）
   - /diarize_labeled 匿名编号按簇（spk）而非按段（否则 2 人被拆成 N 个说话人）

用法（记录用途，补丁已手动应用到 gpu）：
    python3 scripts/patch_speaker_normalize.py  # 说明用，不自动执行
"""
