"""VAD 调参试验场（GPU 服务器 :10097，与生产 diarize_svc 解耦）

用途：上传一段会议录音 → 调 VAD 参数 → 运行 → 返回切分列表 + 叠了软提示音的音频
（句首高音叮 / 句尾低音叮，Hanning 包络防爆音），用耳朵判断切点合不合适，
从而给生产 `max_end_silence_time=250` 等参数找更优值。调完改 config 必须重跑评测
（见 AGENTS.md §0）。

部署（GPU）：
    scp server/scripts/vad_playground.py gpu:~/jvkit/asr/ai-script/
    ssh gpu "cd ~/jvkit/asr && setsid nohup venv/bin/python ai-script/vad_playground.py \
        > vad_playground.log 2>&1 < /dev/null &"
浏览器打开 http://<gpu-ip>:10097
"""
import base64
import io
import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

# 与 diarize_svc/service.py 同一套模型路径约定（环境变量可覆盖）
_MS_MODELS = os.environ.get("MS_MODELS_DIR", os.path.expanduser("~/.cache/modelscope/models"))
_VAD_MODEL = f"{_MS_MODELS}/damo--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/v2.0.2"

# 可暴露的 VAD 参数（来自 funasr VADXOptions / 模型 config.yaml；深度参数有意不放）
# (参数名, 类型, 默认值, 说明)
PARAMS = [
    ("max_end_silence_time", int, 250, "停顿多久算一句说完(ms)。生产=250，模型默认=800"),
    ("sil_to_speech_time_thres", int, 150, "人声持续多久才算开始说话(ms)，防咳嗽/敲桌误触发"),
    ("speech_to_sil_time_thres", int, 150, "人声消失多久才认可进入静音(ms)"),
    ("lookback_time_start_point", int, 200, "句首往前多留(ms)，防吃掉第一个字的起音"),
    ("lookahead_time_end_point", int, 100, "句尾往后多留(ms)，防吃掉尾音"),
    ("speech_noise_thres", float, 0.6, "人声/噪声判决门槛：调低更敏感(轻声可抓)，调高抗噪"),
    ("max_single_segment_time", int, 60000, "单段最长(ms)，超时强制切"),
    ("max_start_silence_time", int, 3000, "开头静音最多容忍(ms)"),
]

app = FastAPI(title="VAD 调参试验场")
_VAD = None


def _vad():
    """懒加载单例：首次调用才加载模型进内存，之后常驻。"""
    global _VAD
    if _VAD is None:
        from funasr import AutoModel
        _VAD = AutoModel(model=_VAD_MODEL, device="cpu",
                         disable_pbar=True, disable_log=True, disable_update=True)
    return _VAD


def _beep(sr: int, freq: float, ms: int = 80, amp: float = 0.2) -> np.ndarray:
    """软提示音：正弦 + Hanning 包络（两端渐隐渐出，无爆音不刺耳）。"""
    n = int(sr * ms / 1000)
    t = np.arange(n) / sr
    return (np.sin(2 * np.pi * freq * t) * np.hanning(n) * amp).astype(np.float32)


def _mark(wav: np.ndarray, sr: int, sents: list, mark_start: bool, mark_end: bool,
          gap_ms: int = 500) -> np.ndarray:
    """重建时间轴：句首 = 高音叮(1175Hz) + gap 空白；句尾 = 低音叮(784Hz)。
    提示音和空白是"插入"而非叠加——段间真正断开，标记版比原音频长。"""
    b_start, b_end = _beep(sr, 1175), _beep(sr, 784)
    gap = np.zeros(int(sr * gap_ms / 1000), dtype=np.float32)
    parts: list[np.ndarray] = []
    prev = 0
    for s, e in sents:
        a, b = int(s * sr), int(e * sr)
        parts.append(wav[prev:a])              # 段间原始静音/噪声，原样保留
        if mark_start:
            parts += [b_start, gap]
        parts.append(wav[a:b])                 # 人声段本体
        if mark_end:
            parts.append(b_end)
        prev = b
    parts.append(wav[prev:])                   # 末尾残留
    return np.concatenate(parts) if parts else wav.astype(np.float32)


@app.get("/", response_class=HTMLResponse)
async def index():
    return _HTML


@app.post("/run")
async def run_vad(request: Request, file: UploadFile):
    """body 传 wav 文件；query 传参数覆盖（非空才覆盖）+ mark_start/mark_end。"""
    q = request.query_params
    overrides = {}
    for name, typ, _d, _h in PARAMS:
        v = q.get(name)
        if v not in (None, ""):
            overrides[name] = typ(float(v)) if typ is int else typ(v)
    mark_start = q.get("mark_start", "1") == "1"
    mark_end = q.get("mark_end", "1") == "1"
    gap_ms = int(float(q.get("beep_gap_ms", "500")))  # 叮声后空白(ms)，让段间真正断开

    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(data)
        tmp = f.name
    try:
        wav, sr = sf.read(tmp, dtype="float32")
        if wav.ndim > 1:
            wav = wav[:, 0]  # 立体声取左声道
        r = _vad().generate(input=wav.astype(np.float32).copy(), fs=sr, **overrides)
        segs = r[0]["value"] if isinstance(r, list) else r["value"]
        sents = [[round(a / 1000, 3), round(b / 1000, 3)] for a, b in segs]  # ms → s

        marked = _mark(wav, sr, sents, mark_start, mark_end, gap_ms)
        buf = io.BytesIO()
        sf.write(buf, marked, sr, format="WAV", subtype="PCM_16")
        return {
            "sents": sents,
            "n": len(sents),
            "speech_sec": round(sum(e - s for s, e in sents), 2),
            "total_sec": round(len(wav) / sr, 2),
            "marked_sec": round(len(marked) / sr, 2),
            "marked_b64": base64.b64encode(buf.getvalue()).decode(),
            "params_used": overrides,
        }
    except Exception as e:  # 参数非法/音频损坏等，回给页面展示
        return JSONResponse({"error": f"{type(e).__name__}: {e}"})
    finally:
        Path(tmp).unlink(missing_ok=True)


_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>VAD 调参试验场</title>
<style>
 body{font-family:system-ui,'PingFang SC','Microsoft YaHei',sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}
 h1{font-size:20px} .card{border:1px solid #ddd;border-radius:10px;padding:16px;margin:12px 0}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}
 label{font-size:12px;color:#555;display:block} input[type=number]{width:110px;padding:4px}
 button{padding:8px 22px;font-size:14px;cursor:pointer}
 table{border-collapse:collapse;font-size:13px;margin-top:8px} td,th{border:1px solid #ccc;padding:3px 10px}
 audio{width:100%;margin-top:6px} .hint{font-size:12px;color:#888}
 #status{margin-left:10px;color:#666}
</style></head><body>
<h1>🎚️ VAD 调参试验场 <span class="hint">（GPU :10097，独立服务，不影响生产）</span></h1>
<div class="card">
  <input type="file" id="file" accept="audio/*">
  <div class="grid" id="params"></div>
  <div style="margin-top:10px">
    <label><input type="checkbox" id="mkStart" checked> 句首叮（高音 1175Hz）</label>
    <label style="margin-left:16px"><input type="checkbox" id="mkEnd" checked> 句尾叮（低音 784Hz）</label>
    <label style="margin-left:16px">叮后空白(ms) <input type="number" id="gapMs" value="500" style="width:70px"></label>
  </div>
  <div style="margin-top:12px">
    <button id="go">▶ 运行切分</button><span id="status"></span>
  </div>
</div>
<div class="card" id="result" style="display:none">
  <div id="stat"></div>
  <div>原音频：<audio id="a0" controls></audio></div>
  <div>标记后：<audio id="a1" controls></audio></div>
  <table id="tbl"><thead><tr><th>#</th><th>开始(s)</th><th>结束(s)</th><th>时长(s)</th></tr></thead><tbody></tbody></table>
</div>
<script>
const PARAMS = __PARAMS__;
const box = document.getElementById('params');
for (const [name, def, hint] of PARAMS) {
  const d = document.createElement('div');
  d.innerHTML = `<label>${name} <span class="hint">${hint}</span></label>
    <input type="number" step="any" id="p_${name}" value="${def}">`;
  box.appendChild(d);
}
document.getElementById('go').onclick = async () => {
  const f = document.getElementById('file').files[0];
  if (!f) { alert('先选音频'); return; }
  const q = new URLSearchParams();
  for (const [name] of PARAMS) {
    const v = document.getElementById('p_' + name).value;
    if (v !== '') q.set(name, v);
  }
  q.set('mark_start', document.getElementById('mkStart').checked ? '1' : '0');
  q.set('mark_end', document.getElementById('mkEnd').checked ? '1' : '0');
  q.set('beep_gap_ms', document.getElementById('gapMs').value || '500');
  const st = document.getElementById('status');
  st.textContent = ' 运行中…（首次要加载模型，约 10s）';
  document.getElementById('go').disabled = true;
  try {
    const fd = new FormData(); fd.append('file', f);
    const r = await fetch('/run?' + q.toString(), { method: 'POST', body: fd });
    const j = await r.json();
    if (j.error) { st.textContent = ' 出错: ' + j.error; return; }
    document.getElementById('result').style.display = '';
    document.getElementById('stat').innerHTML =
      `共 <b>${j.n}</b> 段，总人声 <b>${j.speech_sec}s</b> / 音频 ${j.total_sec}s` +
      `（标记版 ${j.marked_sec}s：提示音+空白是插入的，会变长）`;
    document.getElementById('a0').src = URL.createObjectURL(f);
    const bin = atob(j.marked_b64), buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    document.getElementById('a1').src = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
    const tb = document.querySelector('#tbl tbody'); tb.innerHTML = '';
    j.sents.forEach(([s, e], i) => {
      tb.innerHTML += `<tr><td>${i + 1}</td><td>${s.toFixed(2)}</td><td>${e.toFixed(2)}</td><td>${(e - s).toFixed(2)}</td></tr>`;
    });
    st.textContent = ' 完成';
  } catch (e) { st.textContent = ' 失败: ' + e; }
  finally { document.getElementById('go').disabled = false; }
};
</script></body></html>"""

_PARAMS_JS = "[" + ",".join(f'["{n}",{repr(d)},"{h}"]' for n, _t, d, h in PARAMS) + "]"
_HTML = _HTML.replace("__PARAMS__", _PARAMS_JS)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10097, log_level="info")
