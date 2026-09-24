"""降噪试听台：FastAPI + 单页。
- POST /api/denoise (上传 wav/webm) → 四家音频 URL（原声+三家降噪）
- GET /out/{file} 提供音频
浏览器录音：getUserMedia(16k) + AudioWorklet 攒 PCM → WAV blob 上传。
"""
import os
import time
import uuid
import shutil
import subprocess
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import imageio_ffmpeg
import denoise_models as dm

FFMPEG = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "out")
os.makedirs(OUT, exist_ok=True)

app = FastAPI(title="降噪试听台")
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))

@app.post("/api/denoise")
async def denoise(file: UploadFile = File(...)):
    raw = await file.read()
    prefix = time.strftime("%H%M%S") + "_" + uuid.uuid4().hex[:6]
    raw_path = os.path.join(OUT, prefix + "_upload.bin")
    with open(raw_path, "wb") as f:
        f.write(raw)
    wav_path = os.path.join(OUT, prefix + "_src.wav")
    # 统一转成 16k wav（浏览器录的是 webm 或 wav 都可能）
    r = subprocess.run(
        [FFMPEG, "-y", "-i", raw_path, "-ac", "1", "-ar", "16000", wav_path],
        capture_output=True)
    if r.returncode != 0:
        os.remove(raw_path)
        return JSONResponse({"error": "ffmpeg 转码失败: " + r.stderr.decode()[-300:]}, status_code=400)
    os.remove(raw_path)
    try:
        outs = dm.run_all(wav_path, OUT, prefix)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)
    return {
        "id": prefix,
        "audios": {k: f"/out/{os.path.basename(v)}" for k, v in outs.items()},
    }

@app.get("/out/{name}")
def get_out(name: str):
    # 防目录穿越
    safe = os.path.basename(name)
    p = os.path.join(OUT, safe)
    if not os.path.exists(p):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="audio/wav")

@app.get("/api/history")
def history():
    """扫描 out/ 按批次归组，刷新后历史不丢。只列四件套齐全的批次。"""
    import re
    batches = {}
    for f in os.listdir(OUT):
        m = re.match(r"(.+)_(orig|gtcrn|dfnet|frcrn)\.wav$", f)
        if m:
            batches.setdefault(m.group(1), {})[m.group(2)] = f"/out/{f}"
    complete = [
        {"id": k, "audios": v}
        for k, v in batches.items()
        if len(v) == 4
    ]
    complete.sort(key=lambda x: x["id"], reverse=True)
    return {"history": complete}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8030)
