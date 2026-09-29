# MySQL 迁移与调试产物说明（2026-09-29）

## 为什么迁 MySQL

- SQLite 单文件并发写弱，调试页大量查询历史会议容易锁。
- 新增调试产物表（`diar_segments` / `summary_llm_logs` / `meeting_events` 等）后，
  需要更稳定的连接池与线上部署能力。
- 旧 SQLite 数据已弃用，未做迁移。

## 本地开发环境

1. 确认本机已跑 mysqld（WSL/Linux）：
   ```bash
   pgrep -fa mysqld
   ```
2. 初始化数据库/账号/表：
   ```bash
   bash scripts/init_mysql.sh
   ```
   脚本默认 root 密码 `root`、创建 `meeting_asr` 库和 `meeting/meeting_asr` 账号。
3. `.env` 中的 `DATABASE_URL` 已改为：
   ```
   mysql+asyncmy://meeting:meeting_asr@127.0.0.1:3306/meeting_asr?charset=utf8mb4
   ```
4. 启动全栈：
   ```bash
   bash scripts/start_all.sh restart
   ```

## 服务器部署

复制 `server/.env.example` 到 `server/.env`，把 `DATABASE_URL` 改成服务器 MySQL 账号：

```
DATABASE_URL=mysql+asyncmy://meeting:YOUR_PASS@127.0.0.1:3306/meeting_asr?charset=utf8mb4
```

用 `APP_ENV=server` 启动时 `config.py` 自动加载 `.env.server`（已改为 MySQL 占位，
密码 `CHANGE_ME` 务必替换）。

## 新增的调试产物

| 产物 | 落盘位置 | 查询 API |
|---|---|---|
| 原始整轨 | `data/debug/{meeting_id}/raw.wav` | `GET /api/meetings/{id}/debug` |
| 降噪整轨 | `data/debug/{meeting_id}/denoised.wav` | 同上 |
| 切段音频 | `data/debug/{meeting_id}/segments/{source}/seg_*.wav` | `GET /api/meetings/{id}/debug/diar_segments` |
| 视觉帧图 | `data/debug/{meeting_id}/visual/{note_id}/frame_*.jpg` | `GET /api/meetings/{id}/debug/visual_notes` |
| LLM prompt/原始输出 | `summary_llm_logs` 表 | `GET /api/meetings/{id}/debug/summary_logs` |
| 事件时间线 | `meeting_events` 表 | `GET /api/meetings/{id}/debug` |

调试页入口：历史会议详情页 → "调试" tab。

## 旧数据

SQLite `server/meeting.db` 已停止使用，可归档或删除。
