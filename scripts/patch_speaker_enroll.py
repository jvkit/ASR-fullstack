"""补丁：FunASR WS 服务端增加声纹管理短连接 + 可配置注册匹配阈值。

1. 新增 handle_speaker_ctrl：mode=enroll 收 PCM → cam++ embedding → 写
   speaker_db.json（原子替换）；mode=speaker_list / speaker_delete 即时回复。
   在 ws_serve 消息循环入口按首条消息的 mode 分流，不走 ASR 管线。
2. 注册匹配阈值从写死 0.2 改为 --speaker_match_threshold（默认 0.45），
   低于阈值回退匿名聚类，避免未注册的人被强行匹配到最近注册者。
"""
import subprocess

SRC = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py"

# 1) 新增控制函数（插在 async_asr 定义之前）
ANCHOR_FUNC = "async def async_asr(websocket, audio_in: bytes):"
CTRL_FUNC = '''async def handle_speaker_ctrl(websocket, first_msg):
    """声纹管理短连接：enroll 收 PCM 算 embedding 入库；list/delete 即时回复。"""
    ctrl_mode = first_msg.get("mode")
    spk_name = str(first_msg.get("spk_name") or "").strip()
    try:
        if ctrl_mode == "speaker_list":
            db = _load_speaker_db_sync()
            await websocket.send(json.dumps(
                {"mode": "speaker_list_result", "speakers": sorted(db.keys())},
                ensure_ascii=False))
            return
        if ctrl_mode == "speaker_delete":
            if not spk_name:
                raise ValueError("spk_name required")
            db = _load_speaker_db_sync()
            removed = db.pop(spk_name, None) is not None
            tmp_path = SPEAKER_DB_PATH + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False)
            os.replace(tmp_path, SPEAKER_DB_PATH)
            await websocket.send(json.dumps(
                {"mode": "speaker_delete_result", "ok": True,
                 "removed": removed, "speakers": sorted(db.keys())},
                ensure_ascii=False))
            return
        # enroll：持续收二进制 PCM，直到 is_speaking=false / enroll_finish
        if not spk_name:
            raise ValueError("spk_name required")
        pcm_chunks = []
        async for message in websocket:
            if isinstance(message, (bytes, bytearray)):
                pcm_chunks.append(bytes(message))
                continue
            try:
                mj = json.loads(message)
            except Exception:
                continue
            if mj.get("is_speaking") is False or mj.get("enroll_finish"):
                break
        audio = b"".join(pcm_chunks)
        if len(audio) < 16000 * 2 * 3:
            raise ValueError("enroll audio too short: {:.1f}s < 3s".format(len(audio) / 32000))
        sv_out = model_sv.generate(input=audio, embedding=True)[0]
        emb = sv_out["spk_embedding"][0].cpu().numpy().tolist()
        db = _load_speaker_db_sync()
        db[spk_name] = emb
        tmp_path = SPEAKER_DB_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False)
        os.replace(tmp_path, SPEAKER_DB_PATH)
        print("speaker enrolled: {} (total {})".format(spk_name, len(db)), flush=True)
        await websocket.send(json.dumps(
            {"mode": "enroll_result", "ok": True,
             "spk_name": spk_name, "speakers": sorted(db.keys())},
            ensure_ascii=False))
    except Exception as e:
        print("speaker ctrl failed: {}".format(e), flush=True)
        try:
            await websocket.send(json.dumps(
                {"mode": "{}_result".format(ctrl_mode), "ok": False, "error": str(e)},
                ensure_ascii=False))
        except Exception:
            pass


'''

# 2) ws_serve 消息循环入口分流（在 messagejson 打印之后）
ANCHOR_ROUTE = '''                print("=============messagejson============", messagejson)

                end_of_input = False'''
ROUTE_NEW = '''                print("=============messagejson============", messagejson)

                # 声纹管理短连接：enroll / speaker_list / speaker_delete，不走 ASR 管线
                _ctrl_mode = messagejson.get("mode")
                if _ctrl_mode in ("enroll", "speaker_list", "speaker_delete"):
                    try:
                        await handle_speaker_ctrl(websocket, messagejson)
                    finally:
                        websocket_users.discard(websocket)
                    return

                end_of_input = False'''

# 3) 匹配阈值参数化
ANCHOR_THR_OLD = "if similarity > best_score and similarity > 0.2:"
ANCHOR_THR_NEW = "if similarity > best_score and similarity > args.speaker_match_threshold:"
ANCHOR_ARG = '''parser.add_argument(
    "--speaker_db_reload_sec",'''
ARG_NEW = '''parser.add_argument(
    "--speaker_match_threshold",
    type=float,
    default=0.45,
    help="Min cosine similarity to accept a registered speaker match; below this, fall back to anonymous clustering.",
)
parser.add_argument(
    "--speaker_db_reload_sec",'''


def main() -> None:
    src = subprocess.run(["cat", SRC], capture_output=True, text=True).stdout
    if "handle_speaker_ctrl" in src:
        print("already patched")
        return
    for anchor, new, name in [
        (ANCHOR_FUNC, CTRL_FUNC + ANCHOR_FUNC, "ctrl func"),
        (ANCHOR_ROUTE, ROUTE_NEW, "route"),
        (ANCHOR_THR_OLD, ANCHOR_THR_NEW, "threshold"),
        (ANCHOR_ARG, ARG_NEW, "arg"),
    ]:
        if anchor not in src:
            print(f"ERROR: anchor not found: {name}")
            raise SystemExit(1)
        src = src.replace(anchor, new, 1)
    subprocess.run(["bash", "-c", f"cat > {SRC}"], input=src, text=True, check=True)
    r = subprocess.run(
        ["/home/bo1/jvkit/asr/venv/bin/python", "-m", "py_compile", SRC],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("COMPILE FAILED:", r.stderr)
        raise SystemExit(1)
    print("patched OK")


if __name__ == "__main__":
    main()
