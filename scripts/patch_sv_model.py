"""gpu FunASR 服务端补丁：声纹模型 cam++ -> ERes2NetV2 + enroll 留存原始音频。

改动点：
1. model_sv 模型 id 换 ERes2NetV2（本地缓存路径，离线加载）
2. enroll 成功时把 PCM 存为 speaker_enrolls/{spk_name}_{ts}.wav，供日后换模型重算
3. CLUSTER_SIM_THRESHOLD 注释更新

用法: python scripts/patch_sv_eres2netv2.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REMOTE = "gpu"
SERVER = "/home/bo1/jvkit/asr/funasr-src/runtime/python/websocket/funasr_wss_server.py"

OLD_SV = '''model_sv = AutoModel(
    model="iic/speech_campplus_sv_zh-cn_16k-common",'''
NEW_SV = '''model_sv = AutoModel(
    model="/home/bo1/.cache/modelscope/models/iic--speech_eres2netv2_sv_zh-cn_16k-common/snapshots/master",'''

OLD_ENROLL = '''        audio = b"".join(pcm_chunks)
        if len(audio) < 16000 * 2 * 3:
            raise ValueError("enroll audio too short: {:.1f}s < 3s".format(len(audio) / 32000))
        sv_out = model_sv.generate(input=audio, embedding=True)[0]'''
NEW_ENROLL = '''        audio = b"".join(pcm_chunks)
        if len(audio) < 16000 * 2 * 3:
            raise ValueError("enroll audio too short: {:.1f}s < 3s".format(len(audio) / 32000))
        # 留存原始注册音频：换声纹模型/复核时可直接重算 embedding
        try:
            enroll_dir = os.path.join(os.path.dirname(SPEAKER_DB_PATH), "speaker_enrolls")
            os.makedirs(enroll_dir, exist_ok=True)
            with open(os.path.join(enroll_dir, "{}_{}.pcm".format(
                    spk_name, int(time.time()))), "wb") as f:
                f.write(audio)
        except Exception as _e:
            print("enroll audio save failed: {}".format(_e), flush=True)
        sv_out = model_sv.generate(input=audio, embedding=True)[0]'''


def main() -> None:
    src = subprocess.run(
        ["ssh", REMOTE, f"cat {SERVER}"], capture_output=True, text=True, check=True
    ).stdout

    assert OLD_SV in src, "sv model block not found"
    assert OLD_ENROLL in src, "enroll block not found"
    assert "eres2netv2" not in src, "already patched"

    src = src.replace(OLD_SV, NEW_SV)
    src = src.replace(OLD_ENROLL, NEW_ENROLL)
    src = src.replace(
        "CLUSTER_SIM_THRESHOLD = 0.55  # cam++ 同人 cosine 阈值，低于此判新人",
        "CLUSTER_SIM_THRESHOLD = 0.55  # ERes2NetV2 同人 cosine 阈值，低于此判新人",
    )

    p = Path(__file__).parent / "_funasr_wss_server.patched.py"
    p.write_text(src)
    subprocess.run(["scp", "-q", str(p), f"{REMOTE}:{SERVER}.new"], check=True)
    # 备份原文件（仅首次），再原子替换
    subprocess.run(
        ["ssh", REMOTE,
         f"test -f {SERVER}.orig || cp {SERVER} {SERVER}.orig; "
         f"mv {SERVER}.new {SERVER}"],
        check=True,
    )
    p.unlink()
    print("patched. 重启 FunASR 生效（声纹库需全员重新注册）。")


if __name__ == "__main__":
    sys.exit(main())
