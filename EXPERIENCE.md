# ASR-fullstack 经验

## 架构
- 本地全栈：FastAPI 后端 :8010 + Vue3 前端 :5174（https 自签）
- MySQL docker 容器 `mysql`，重启后先 `docker start mysql`
- GPU 服务器 `ssh gpu`（172.16.16.120），diarize_svc :10096 承担所有重运算
- 启动：`bash scripts/start_all.sh restart`
- LLM 网关 `http://127.0.0.1:4000/v1`，key `sk-Dg178ahJjKa6gOUa096`，现在只剩 model='chat'（多模态）

## 双版本管道（互不纠正）
- **实时版**：30s 批次 → 本地降噪 → 本地 3D-Speaker 聚类 → GPU identify_segments → 并行波次 LLM
- **会末版**：全场降噪 → GPU /diarize_full 聚类 → /identify_segments 认人 → 并行波次 → 会末总结

## 踩坑
1. GPU 服务可能无故死掉，症状是 httpx.ConnectError，检查 10096 端口，down 就重启
2. 批量 HTTP 脚本别在后台跑（shell 包装 + pipe head 会 SIGPIPE 卡死），要前台跑或 setsid nohup 无 pipe
3. `/speaker_identify` 要求 ≥3s 语音，短段被拒
4. `file://` 下 fetch 本地 JSON 被禁，需内嵌 HTML
5. 用户电脑弱，禁止本地跑 torch/模型；用户喜欢"先对齐再开做"、不拍脑袋自研

## 已知问题
1. **簇级 argmax 在混合簇上系统性出错**：cluster 3 91 段 93% 是李炳晨，簇级却标成刘恒。段投票（每段 vs 声纹库 argmax，多数决）更鲁棒
2. **单质心算术平均毁跨设备声纹**：线上/线下 embedding 平均后落在两簇中间空白地带，自相关分数大降。multi-vector（每段一个代表，匹配取 max）有效（刘恒 min 0.768→0.915，蒋泽鹏 0.667→0.734）
3. **会末版 /diarize_full 对长音频不稳定**，可能服务崩掉
4. **OCR model 'new' 池已下线**，用 'chat'（实测支持图片）
5. **手机 mic 录音响度忽大忽小**（1s 窗 rms std 16.1dB），声纹分数被拉低，不宜直接注册

## 关键配置
- `reannotate_name_threshold = 0.50`
- `merge_thr = 0.55`（实时版）
- `mer_cos = 0.8`（簇合并）
- `min_cluster_size = 4`

## 数据
- 声纹库 13 人：何鑫、冯忠俊、刘恒、彭赛威、李炳晨、李迪、王建龙、王旭东、王翊冰、符龙、蒋泽鹏、郭静雯、陈俊荣、镇宇
- 注册声纹 cross 相似度：刘恒↔李炳晨 0.32-0.47（正常间隔），冯忠俊↔蒋泽鹏 0.85（近邻对）
