"""真实管线基准（换库版）：声纹库换成本场块的人 -> WS 重放 -> 对rttm评分 -> 还原库。

用法: python scripts/bench_real.py <meeting> <start_min> [more pairs...]
例:  python scripts/bench_real.py S_R003S01C01 0 S_R003S01C01 5 L_R004S01C01 0
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GPU_SPK_DIR = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/speaker_enrolls"
GPU_DB = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/speaker_db.json"
GPU_ENROLL_BASE = "/home/bo1/jvkit/asr/eval/aishell4_enrolls"
WS_URL = "ws://127.0.0.1:8010/ws/meeting"


def sh(cmd: str) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr[-500:]}")
    return r.stdout


def swap_library(chunk_enroll_dir: str) -> None:
    """生产库换成本场块的人（备份原库）。"""
    sh(f"ssh gpu 'cp {GPU_DB} {GPU_DB}.bench_bak && "
       f"mkdir -p {GPU_SPK_DIR}.bench_bak && rm -f {GPU_SPK_DIR}.bench_bak/*.pcm && "
       f"mv {GPU_SPK_DIR}/0*.pcm {GPU_SPK_DIR}.bench_bak/ 2>/dev/null; "
       f"cp {chunk_enroll_dir}/*.pcm {GPU_SPK_DIR}/'")
    # cam++ embedding 重写 speaker_db.json（只含本场人）
    rebuild_src = Path(__file__).resolve().parents[2] / "scripts" / "ai-script" / "rebuild_db.py"
    sh(f"scp -q {rebuild_src} gpu:/home/bo1/jvkit/asr/eval/")
    sh(f"ssh gpu 'cd /home/bo1/jvkit/asr && venv/bin/python eval/rebuild_db.py "
       f"{GPU_SPK_DIR} {GPU_DB}'")


def restore_library() -> None:
    sh(f"ssh gpu 'mv {GPU_SPK_DIR}.bench_bak/*.pcm {GPU_SPK_DIR}/ 2>/dev/null; "
       f"rm -f {GPU_SPK_DIR}/0*.pcm; "
       f"mv {GPU_DB}.bench_bak {GPU_DB}'")


async def replay(wav_name: str, title: str) -> str:
    async with websockets.connect(WS_URL, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "start", "title": title,
                                  "replay_of": wav_name, "speaker_count": 0}))
        mid = None
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "started":
                mid = msg["meeting_id"]
                print(f"  STARTED {mid}", flush=True)
            elif msg.get("type") == "ended":
                break
            elif msg.get("type") == "error":
                raise RuntimeError(msg.get("message"))
        await asyncio.sleep(3)
    return mid


def wait_reannotated(mid: str, timeout: int = 900) -> None:
    import httpx
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = httpx.get(f"http://127.0.0.1:8010/api/meetings/{mid}", timeout=10).json()
        if d.get("reannotated"):
            return
        time.sleep(10)
    raise TimeoutError("reannotate timeout")


def score(mid: str, rttm_mid: str, title: str) -> None:
    # bench_real 在 async 上下文里，评分含自己的事件循环，放线程里跑
    import threading
    done = threading.Event()
    box = {}

    def _w() -> None:
        try:
            from score_real_mod import score_and_write
            score_and_write(mid, rttm_mid, title)
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            done.set()

    threading.Thread(target=_w, daemon=True).start()
    done.wait(timeout=600)
    if "err" in box:
        raise box["err"]


async def run_one(meeting: str, start_m: int) -> None:
    chunk_enroll = f"{GPU_ENROLL_BASE}/{meeting}_{start_m}m"
    wav_name = f"chunk_{meeting}_{start_m}m"
    title = f"【基准】{meeting} {start_m}-{start_m + 5}min 真实管线"
    print(f"== {title} ==", flush=True)
    print("  换声纹库...", flush=True)
    swap_library(chunk_enroll)
    try:
        mid = await replay(wav_name, title)
        print("  等会后版...", flush=True)
        wait_reannotated(mid)
        score(mid, meeting, title)
        print(f"  完成 {mid}", flush=True)
    finally:
        restore_library()
        print("  库已还原", flush=True)


def main() -> None:
    args = sys.argv[1:]
    pairs = [(args[i], int(args[i + 1])) for i in range(0, len(args), 2)]
    for meeting, start_m in pairs:
        asyncio.run(run_one(meeting, start_m))


if __name__ == "__main__":
    main()
