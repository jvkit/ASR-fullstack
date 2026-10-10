#!/bin/bash
cd ~/jvkit/asr/funasr-src/runtime/python/websocket
M=~/.cache/modelscope/models
exec ~/jvkit/asr/venv/bin/python -u funasr_wss_server.py \
  --host 0.0.0.0 --port 10095 \
  --asr_model_online $M/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online/snapshots/master \
  --asr_model $M/iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch/snapshots/master \
  --vad_model $M/iic--speech_fsmn_vad_zh-cn-16k-common-pytorch/snapshots/master \
  --punc_model $M/iic--punc_ct-transformer_zh-cn-common-vocab272727-pytorch/snapshots/master \
  --ngpu 0 --device cpu --ncpu 8 \
  --worker_threads 4 \
  --speaker_match_threshold 0.40
