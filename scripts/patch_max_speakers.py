"""patch：给 gpu FunASR ws server 加 max_speakers（匿名聚类人数上限）。

逻辑：客户端配置消息可传 max_speakers（>0 生效）。匿名聚类新建说话人前检查：
已达上限时不再新建第 N+1 个说话人，强制并入相似度最高的已有簇（即使低于阈值）。
0 = 不限制（默认，行为与之前完全一致）。

幂等：重复运行安全（用锚点检测，已打过就跳过）。
"""
from pathlib import Path

SERVER = Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py")

src = SERVER.read_text(encoding="utf-8")

# 1) 连接初始化默认值
INIT_ANCHOR = '    websocket.spk_min_seg_sec = 1.0'
INIT_NEW = INIT_ANCHOR + '\n    # 匿名聚类人数上限（0=不限制；>0 时达到上限强制并入最相近簇）\n    websocket.max_speakers = 0'
if "websocket.max_speakers" not in src:
    assert INIT_ANCHOR in src, "init anchor not found"
    src = src.replace(INIT_ANCHOR, INIT_NEW, 1)
    print("[1/3] init default added")
else:
    print("[1/3] already patched, skip")

# 2) 配置消息解析（紧跟 spk_min_seg_sec 的解析 tuple 之后）
PARSE_ANCHOR = '''                        ("spk_min_seg_sec", "spk_min_seg_sec", float),
                    ):'''
PARSE_NEW = '''                        ("spk_min_seg_sec", "spk_min_seg_sec", float),
                        ("max_speakers", "max_speakers", int),
                    ):'''
if '("max_speakers", "max_speakers", int)' not in src:
    assert PARSE_ANCHOR in src, "parse anchor not found"
    src = src.replace(PARSE_ANCHOR, PARSE_NEW, 1)
    print("[2/3] config parse added")
else:
    print("[2/3] already patched, skip")

# 3) 聚类逻辑：达到上限时强制并入最相近簇
CLUSTER_ANCHOR = '''            else:
                centroids.append((embedding.copy(), 1))
                spk_name = "说话人{}".format(len(centroids))
                best_score = float(best_sim if best_idx >= 0 else 0.0)'''
CLUSTER_NEW = '''            else:
                max_spk = int(getattr(ws_conn, "max_speakers", 0) or 0)
                if max_spk > 0 and len(centroids) >= max_spk and best_idx >= 0:
                    # 人数已达上限：不再新建，强制并入最相近簇（宁混不裂）
                    c, n = centroids[best_idx]
                    centroids[best_idx] = (c * n + embedding) / (n + 1), n + 1
                    spk_name = "说话人{}".format(best_idx + 1)
                    best_score = float(best_sim)
                else:
                    centroids.append((embedding.copy(), 1))
                    spk_name = "说话人{}".format(len(centroids))
                    best_score = float(best_sim if best_idx >= 0 else 0.0)'''
if "宁混不裂" not in src:
    assert CLUSTER_ANCHOR in src, "cluster anchor not found"
    src = src.replace(CLUSTER_ANCHOR, CLUSTER_NEW, 1)
    print("[3/3] cluster cap added")
else:
    print("[3/3] already patched, skip")

SERVER.write_text(src, encoding="utf-8")
print("patch done:", SERVER)
