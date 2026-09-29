#!/bin/bash
# 本地 MySQL 初始化脚本（开发环境用）。
# 前提：本机已运行 mysqld，root 密码为 root。
# 若不是，请手动建库并授权：
#   CREATE DATABASE meeting_asr CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
#   GRANT ALL PRIVILEGES ON meeting_asr.* TO 'meeting'@'%' IDENTIFIED BY 'meeting_asr';
#   FLUSH PRIVILEGES;

set -e
DB_NAME=meeting_asr
DB_USER=meeting
DB_PASS=meeting_asr

cd "$(dirname "$0")/.."
ROOT=$PWD

echo "初始化本地 MySQL: database=$DB_NAME user=$DB_USER"
python3 - <<PY
import asyncmy
import asyncio

async def main():
    conn = await asyncmy.connect(host='127.0.0.1', port=3306, user='root', password='root')
    async with conn.cursor() as cur:
        await cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
        await cur.execute(f"CREATE DATABASE {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        await cur.execute(f"CREATE USER IF NOT EXISTS '{DB_USER}'@'%%' IDENTIFIED BY '{DB_PASS}'")
        await cur.execute(f"GRANT ALL PRIVILEGES ON {DB_NAME}.* TO '{DB_USER}'@'%%'")
        await cur.execute("FLUSH PRIVILEGES")
    conn.close()
    print("MySQL 初始化完成")

asyncio.run(main())
PY

echo "从 .venv 启动后端建表（init_db）..."
"$ROOT/.venv/bin/python" - <<PY
import asyncio, sys
sys.path.insert(0, "$ROOT/server")
from app.db.database import init_db
asyncio.run(init_db())
print("表结构创建完成")
PY

echo "完成。当前 DATABASE_URL:"
grep "^DATABASE_URL" .env 2>/dev/null || grep "^DATABASE_URL" server/.env.example
