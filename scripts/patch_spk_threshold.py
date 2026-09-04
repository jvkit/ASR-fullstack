"""给 gpu8 的 funasr_wss_server.py 打补丁：支持客户端配置动态聚类阈值。

改动点：
1. ws_serve 初始化时给 websocket 对象加 spk_threshold 属性（默认 0.55）
2. 读客户端配置消息里的 spk_threshold（若有）
3. 聚类判断处用 websocket.spk_threshold 代替写死的 CLUSTER_SIM_THRESHOLD
"""
from pathlib import Path

P = Path("/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py")
s = P.read_text()

# 1) ws_serve 初始化：加 spk_threshold 属性
init_old = """    # 匿名说话人聚类状态（会话级）
    websocket.spk_centroids = []"""
init_new = """    # 匿名说话人聚类状态（会话级）
    websocket.spk_centroids = []
    websocket.spk_threshold = 0.55  # 默认聚类阈值，可被客户端配置覆盖"""
assert init_old in s, "init anchor not found"
s = s.replace(init_old, init_new)

# 2) 客户端配置消息解析：读 spk_threshold
#    找到 audio_fs 的解析处（它就是从 messagejson 读配置的样板）
cfg_old = """                    websocket.audio_fs = _safe_int(messagejson["audio_fs"], 16000)"""
cfg_new = """                    websocket.audio_fs = _safe_int(messagejson["audio_fs"], 16000)
                    # 动态聚类阈值（前端可调，做说话人分离效果实验用）
                    if "spk_threshold" in messagejson:
                        try:
                            websocket.spk_threshold = float(messagejson["spk_threshold"])
                        except (TypeError, ValueError):
                            pass"""
assert cfg_old in s, "cfg anchor not found"
s = s.replace(cfg_old, cfg_new)

# 3) 聚类判断：用会话阈值
sim_old = """            if best_idx >= 0 and best_sim >= CLUSTER_SIM_THRESHOLD:"""
sim_new = """            cluster_thr = getattr(ws_conn, "spk_threshold", CLUSTER_SIM_THRESHOLD)
            if best_idx >= 0 and best_sim >= cluster_thr:"""
assert sim_old in s, "sim anchor not found"
s = s.replace(sim_old, sim_new)

# 4) _sv_and_match_sync 签名加 ws_conn（现名 websocket，统一改名清晰）
sig_old = """def _sv_and_match_sync(audio_in: bytes, reload_sec: int, ws_conn=None):"""
assert sig_old in s, "sig already ok"

P.write_text(s)
print("patched OK")
