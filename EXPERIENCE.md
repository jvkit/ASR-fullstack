# 最近工作总结（2026-10-12）

## 今天干了什么

### 1. 会中模式核对
- 确认会中（实时版）已经走 3D-Speaker，不是自研聚类
- 链路：本地降噪 → 本地 3D-Speaker 聚类 → GPU /identify_segments 声纹认人+ASR

### 2. 手机 mic 录音质量分析（05b49d64b786）
- 没有 WebRTC 掉块断流（任意 15ms-10s 能量断口 = 0）
- 真正问题：响度忽大忽小（1s 窗 rms std 16.1dB，是最好场的 2 倍多）
- 前 3.5 分钟散布 5 个 1.0-1.4s 绝对全零洞（疑似手机 AGC 或 Chrome tab 挂起）
- 这种电平不稳会拉低声纹分数，不宜直接注册声纹

### 3. 声纹库容量调研
- 检索行业资料：VoxWatch（AWS）开集声纹 benchmark 证明"库内人越多，陌生人分数右移，误识率上升"是公认难题
- 我们当前 13 人远在舒适区内，不用担心规模
- 决定误识的是段质量和阈值，不是人数

### 4. 线上/线下声纹不识别问题（核心）
- **根因**：GPU `_load_db_multi()` 把每人所有注册段 embedding **算术平均成单质心**
- 线上/线下 embedding 是两个簇，强行平均 → 向量落在中间空白地带 → 自相关分数大降
- **验证**：新增 `/enroll_debug` 只读端点，对比单质心 vs multi-vector
  - 刘恒：min 0.768 → 0.915（+0.147）
  - 蒋泽鹏：min 0.667 → 0.734（+0.067）
  - 冯忠俊（同质旧批次）：微降 0.01，无害
- **结论**：multi-vector（每段一个代表，匹配取 max）有效，**但还没进生产**

### 5. 9d26278 会末版灾难诊断（李炳晨→刘恒）
- 用户重跑会末，大量本该李炳晨的段被标成刘恒
- **排查过程**：
  1. 先怀疑声纹库污染 → 验证刘恒↔李炳晨 cross 相似度 0.32-0.47，**无污染**
  2. 用降噪版音频复现 → 确认生产结果（李炳晨 133 段）
  3. 用原始版音频跑 → 结果完全不同（cluster 3 19/20 是刘恒）
  4. 1 分钟本机测试 → cluster 0 段投票 2/4 是李炳晨
- **根因**：**簇级 argmax 的时长加权平均被长段主导，混合簇上系统性出错**
- **用户拍板**：改段投票（每段 vs 声纹库 argmax，多数决），但还没实现

### 6. 视频落盘检查
- 代码链路完整（前端 MediaRecorder 分片 → 后端写盘）
- 但今天三场都是 mic 模式，没开视频，所以没视频文件——预期行为

### 7. 代码上传
- 创建 GitHub 仓库：
  - `ASR-fullstack`：本地全栈（meeting-asr）
  - `ASR-service`：GPU 服务器 diarize_svc
- ASR-service 已上传（含 EXPERIENCE.md）
- ASR-fullstack 遇到 pack 超 2GB，大文件（eval 数据集、video、debug 音频）需要清理，**还没完成**

## 待办
1. **改 GPU /identify_segments**：簇级 argmax → 段投票（治李炳晨→刘恒）
2. **上 multi-vector**：替换单质心（治跨设备不识别）
3. **清理 ASR-fullstack 大文件**：eval/aishell4、video/spike、scd_bench、server/data/debug、audio_denoise/checkpoints
4. 用户随时可能再开 mic 会议测试两人/三人聚类——注意 cam 路径、ere 勿开
