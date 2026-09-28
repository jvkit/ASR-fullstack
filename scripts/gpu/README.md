# GPU 侧部署（diarize_svc：ASR + 认人 + 声纹库，端口 10096）

```bash
# 在 GPU 机器（ssh gpu）上：
mkdir -p ~/jvkit/asr/diarize_svc
# 同步代码（service.py 等，以 GPU 上现有布局为准）
scp meeting-asr/scripts/gpu/run_supervised.sh gpu:~/jvkit/asr/diarize_svc/
ssh gpu "cd ~/jvkit/asr/diarize_svc && setsid nohup bash run_supervised.sh > /dev/null 2>&1 &"
```

- 看护语义：进程被杀/崩溃 3s 自动拉起；`diarize_svc_respawn.log` 记死亡时间线
- 冷启动首请求含模型加载（~40s）；funasr 已加 disable_update，不依赖 PyPI
- 环境：HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1
